#!/usr/bin/env python3
"""
Closing Odds Collector — captures Pinnacle closing odds for open trades.

Uses api-football.com (FOOTBALL_API_KEY) to fetch Pinnacle pre-match odds
for all bet types: Match Winner, Over/Under, Asian Handicap, First Half, BTTS.
Stores vig-removed closing probabilities on each paper_trade row for CLV calculation.

Kickoff-indexed: each run looks up every open trade's real kickoff (api-football)
and only captures within CAPTURE_LEAD_MIN (45) of KO. Designed to run every 30 min
(cron */30) — a match too early is simply caught on a later tick (this is also the
failure-retry path), and re-capturing pre-KO overwrites with a line closer to
kickoff. The snapshot records minutes_before_kickoff so we keep the closest line;
after KO the value is frozen. If KO passed with no capture, one last-chance grab is
allowed within POST_KO_GRACE_MIN (30). The resolver then uses closing_sharp_odds for CLV.

Usage:
    python closing_collector.py                # live — writes to DB
    python closing_collector.py --dry-run      # compute only, no DB writes
    python closing_collector.py --backfill     # fill gaps on all open trades, ignore KO gating

API cost: 1 fixtures call per distinct match-date (cached) + 1 odds call per match
in the capture window. Typically a handful of requests per 30-min tick.
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [closing] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('closing_collector')

DATABASE_URL = os.getenv('DATABASE_URL')
FOOTBALL_API_KEY = os.getenv('FOOTBALL_API_KEY', '')
API_BASE = 'https://v3.football.api-sports.io'

# Pinnacle bookmaker ID on api-football
PINNACLE_BOOKMAKER_ID = 4

# Start capturing the closing line this many minutes before kickoff. With a
# 30-min cron cadence this yields a capture ~0-15 min before KO (the closing line).
CAPTURE_LEAD_MIN = 45
# If kickoff already passed but we never captured (failures), allow one last-chance
# grab within this grace window — Pinnacle pre-match odds linger briefly post-KO.
POST_KO_GRACE_MIN = 30
# How far ahead to pull open trades from the DB each run (real kickoff gates capture).
SELECT_WINDOW_HOURS = 18


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r'[^a-z0-9 ]', '', s)
    s = re.sub(
        r'\b(fc|cf|sc|ac|ss|afc|bsc|1\.|vfb|vfl|rb|sv|fk|sk|bv|borussia|club|de|da|do|dos|la|el|al|cd|ca|cs)\b',
        '', s,
    )
    return re.sub(r'\s+', ' ', s).strip()


def _match_keys(home: str, away: str) -> list[str]:
    hn, an = _norm(home), _norm(away)
    return [
        f'{hn}_{an}',
        f'{hn[:6]}_{an[:6]}',
        f'{hn[:8]}_{an[:8]}',
    ]


def _conn():
    return psycopg2.connect(DATABASE_URL)


# ── Fetch Pinnacle odds from api-football ─────────────────────────────────────

def _fetch_pinnacle_odds(fixture_id: int) -> dict | None:
    """
    Fetch all Pinnacle bet types for a single fixture from api-football.
    Returns vig-removed closing probabilities for all available markets.
    """
    try:
        resp = requests.get(
            f'{API_BASE}/odds',
            params={'fixture': fixture_id, 'bookmaker': PINNACLE_BOOKMAKER_ID},
            headers={'x-apisports-key': FOOTBALL_API_KEY},
            timeout=15,
        )
        remaining = resp.headers.get('x-ratelimit-requests-remaining', '?')
        log.info(f'  fixture {fixture_id}: API remaining = {remaining}')
        if resp.status_code != 200:
            log.warning(f'  fixture {fixture_id}: HTTP {resp.status_code}')
            return None
        data = resp.json()
    except Exception as e:
        log.warning(f'  fixture {fixture_id}: fetch failed — {e}')
        return None

    responses = data.get('response', [])
    if not responses:
        return None

    result: dict = {'captured_at': datetime.now(timezone.utc).isoformat()}

    for item in responses:
        fixture_info = item.get('fixture', {})
        result['fixture_id'] = fixture_info.get('id')

        for bm in item.get('bookmakers', []):
            if bm.get('id') != PINNACLE_BOOKMAKER_ID:
                continue

            for bet in bm.get('bets', []):
                bet_name = bet.get('name', '')
                bet_id = bet.get('id', 0)
                values = bet.get('values', [])

                if bet_id == 1:  # Match Winner (1X2)
                    _parse_1x2(values, result, prefix='h2h')

                elif bet_id == 5:  # Goals Over/Under
                    _parse_over_under(values, result, prefix='')

                elif bet_id == 6:  # Goals Over/Under First Half
                    _parse_over_under(values, result, prefix='ht_')

                elif bet_id == 13:  # First Half Winner
                    _parse_1x2(values, result, prefix='ht_h2h')

                elif bet_id == 4:  # Asian Handicap
                    _parse_handicap(values, result, prefix='')

                elif bet_id == 19:  # Asian Handicap First Half
                    _parse_handicap(values, result, prefix='ht_')

                elif bet_id == 16:  # Total - Home
                    _parse_team_total(values, result, side='home')

                elif bet_id == 17:  # Total - Away
                    _parse_team_total(values, result, side='away')

    return result if len(result) > 1 else None


def _parse_1x2(values: list[dict], result: dict, prefix: str):
    """Parse Match Winner / First Half Winner odds into vig-removed probs."""
    odds = {}
    for v in values:
        val = v.get('value', '').lower()
        try:
            o = float(v.get('odd', 0))
        except (ValueError, TypeError):
            continue
        if o <= 0:
            continue
        if val == 'home':
            odds['home'] = o
        elif val == 'draw':
            odds['draw'] = o
        elif val == 'away':
            odds['away'] = o

    if 'home' not in odds or 'away' not in odds:
        return

    h_imp = 1 / odds['home']
    d_imp = 1 / odds.get('draw', 999) if 'draw' in odds else 0
    a_imp = 1 / odds['away']
    total = h_imp + d_imp + a_imp
    if total <= 0:
        return

    result[prefix] = {
        'home': round(h_imp / total, 6),
        'draw': round(d_imp / total, 6) if 'draw' in odds else None,
        'away': round(a_imp / total, 6),
        'home_odds': odds['home'],
        'draw_odds': odds.get('draw'),
        'away_odds': odds['away'],
    }


def _parse_over_under(values: list[dict], result: dict, prefix: str):
    """Parse Goals Over/Under into vig-removed probs per line."""
    lines: dict[str, dict] = {}
    for v in values:
        val = v.get('value', '')
        try:
            o = float(v.get('odd', 0))
        except (ValueError, TypeError):
            continue
        if o <= 0:
            continue

        m = re.match(r'(Over|Under)\s+([\d.]+)', val)
        if not m:
            continue
        direction = m.group(1).lower()
        line = m.group(2)
        if line not in lines:
            lines[line] = {}
        lines[line][direction] = o

    for line, odds in lines.items():
        if 'over' not in odds or 'under' not in odds:
            continue
        o_imp = 1 / odds['over']
        u_imp = 1 / odds['under']
        total = o_imp + u_imp
        if total <= 0:
            continue

        line_key = line.replace('.', '_')
        result[f'{prefix}over_{line_key}'] = round(o_imp / total, 6)
        result[f'{prefix}under_{line_key}'] = round(u_imp / total, 6)


def _spread_line_key(x: float) -> str:
    """Encode a signed handicap line as a key fragment.

    1.5 -> '1_5', -1.5 -> 'm1_5', 0.0 -> '0_0', 0.75 -> '0_75'. The 'm' prefix
    marks a *negative* (give-the-goals) line, matching the convention the
    resolver already expects (resolver._get_closing_from_sharp_snapshot).
    """
    body = f'{abs(x)}'.replace('.', '_')
    return ('m' if x < 0 else '') + body


def _parse_handicap(values: list[dict], result: dict, prefix: str):
    """Parse Asian Handicap into vig-removed probs per line.

    api-football labels BOTH sides of a single handicap market with the same
    *home-perspective* signed line. e.g. for the home -1.5 / away +1.5 market it
    emits 'Home -1.5' and 'Away -1.5'; for home +1.5 / away -1.5 it emits
    'Home +1.5' and 'Away +1.5'. So the two entries that share a signed line
    string are the two complementary sides of one market and vig-remove against
    each other. The away side's *own* handicap is the negation of that line.

    Keys are sign-aware: spread_home_m1_5 = P(home -1.5) = P(home wins by 2+);
    spread_away_m1_5 = P(away -1.5) = P(away wins by 2+); the unprefixed
    spread_home_1_5 = P(home +1.5) = P(home does not lose by 2+).
    """
    markets: dict[float, dict] = {}   # home-perspective signed line -> {side: odd}
    for v in values:
        val = v.get('value', '')
        try:
            o = float(v.get('odd', 0))
        except (ValueError, TypeError):
            continue
        if o <= 0:
            continue

        m = re.match(r'(Home|Away)\s+([+-]?[\d.]+)', val)
        if not m:
            continue
        side = m.group(1).lower()
        try:
            line = float(m.group(2))
        except (ValueError, TypeError):
            continue
        markets.setdefault(line, {})[side] = o

    for line, odds in markets.items():
        if 'home' not in odds or 'away' not in odds:
            continue
        h_imp = 1 / odds['home']
        a_imp = 1 / odds['away']
        total = h_imp + a_imp
        if total <= 0:
            continue

        # Home side carries handicap `line`; away side carries handicap `-line`.
        result[f'{prefix}spread_home_{_spread_line_key(line)}'] = round(h_imp / total, 6)
        result[f'{prefix}spread_away_{_spread_line_key(-line)}'] = round(a_imp / total, 6)


def _parse_team_total(values: list[dict], result: dict, side: str):
    """Parse Total Home / Total Away goals."""
    for v in values:
        val = v.get('value', '')
        try:
            o = float(v.get('odd', 0))
        except (ValueError, TypeError):
            continue
        if o <= 0:
            continue

        m = re.match(r'(Over|Under)\s+([\d.]+)', val)
        if not m:
            continue
        direction = m.group(1).lower()
        line = m.group(2).replace('.', '_')
        imp = 1 / o  # Just store raw implied — paired removal done elsewhere
        result[f'{side}_total_{direction}_{line}'] = round(imp, 6)


# ── Match identity: clean teams + kickoff from metadata, then title ──────────

def _strip_team(name: str) -> str:
    return re.sub(r'\s+(?:FC|CF|SC|AC|AFC|SK|FK|CD|SV)$', '', name.strip(), flags=re.I).strip()


def _parse_dt(s) -> datetime | None:
    """Parse PM gameStartTime ('2026-05-28 18:00:00+00') robustly on py3.9."""
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    s2 = str(s).strip().replace('Z', '+00:00').replace(' ', 'T', 1)
    s2 = re.sub(r'([+-]\d{2})$', r'\1:00', s2)  # +00 -> +00:00 for fromisoformat
    try:
        dt = datetime.fromisoformat(s2)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _parse_title_teams(title: str) -> tuple[str | None, str | None]:
    """(home, away|None) from a PM market title, incl. single-team titles."""
    t = (title or '').strip()
    # NB: do NOT treat '-' as a stop char — team names contain hyphens
    # (Saint-Étienne, Paris Saint-Germain). Stop only at ':', '?', or keywords.
    m = re.search(r'(?:will\s+)?(.+?)\s+vs?\.?\s+(.+?)(?:\s*[:\?]| end | both | leading |$)',
                  t, re.I)
    if m:
        return _strip_team(m.group(1)), _strip_team(m.group(2))
    for pat in (r'will\s+(.+?)\s+win\s+on\b',
                r'^(.+?)\s+leading at halftime',
                r'spread:\s*(.+?)\s*\('):
        m = re.search(pat, t, re.I)
        if m:
            return _strip_team(m.group(1)), None
    return None, None


def _extract_match(trade: dict) -> dict:
    """
    Resolve a trade's match identity, preferring clean PM metadata over the title.
    Returns {home, away|None, kickoff|None, date|None}.
    """
    md = trade.get('raw_metadata') or {}
    if isinstance(md, str):
        try:
            md = json.loads(md)
        except (ValueError, TypeError):
            md = {}

    home = md.get('_home_team')
    away = md.get('_away_team')
    kickoff = _parse_dt(md.get('gameStartTime'))

    if not (home and away):
        th, ta = _parse_title_teams(trade.get('market_title') or '')
        home = home or th
        away = away or ta

    date = kickoff.date().isoformat() if kickoff else None
    if not date:
        m = re.search(r'on\s+(\d{4}-\d{2}-\d{2})', trade.get('market_title') or '')
        date = m.group(1) if m else None

    return {'home': home, 'away': away, 'kickoff': kickoff, 'date': date}


def _build_match_groups(trades: list[dict]) -> list[dict]:
    """
    Cluster trades into real matches. Two-team trades define a pairing; single-team
    siblings (halftime / spread / 'Will X win') then attach via a shared team name,
    recovering the opponent + date + kickoff from the pairing.
    """
    infos = [{**_extract_match(t), 'trade': t} for t in trades]
    infos = [i for i in infos if i['home']]
    # Process two-team trades first so pairings exist before singles attach.
    infos.sort(key=lambda i: 0 if i['away'] else 1)

    clusters: list[dict] = []
    for info in infos:
        hn = _norm(info['home'])
        an = _norm(info['away']) if info['away'] else None
        target = None
        for c in clusters:
            if hn in c['teams'] or (an and an in c['teams']):
                if not c['date'] or not info['date'] or c['date'] == info['date']:
                    target = c
                    break
        if target is None:
            target = {'teams': set(), 'home': None, 'away': None,
                      'kickoff': None, 'date': None, 'trades': []}
            clusters.append(target)
        target['teams'].add(hn)
        if an:
            target['teams'].add(an)
        if info['away'] and not target['away']:
            target['home'], target['away'] = info['home'], info['away']
        if not target['home']:
            target['home'] = info['home']
        target['kickoff'] = target['kickoff'] or info['kickoff']
        target['date'] = target['date'] or info['date']
        target['trades'].append(info['trade'])
    return clusters


def _fetch_fixtures_for_date(search_date: str,
                             cache: dict[str, list]) -> list:
    """All fixtures on a date in ONE api-football call, cached per run."""
    if search_date in cache:
        return cache[search_date]
    fixtures: list = []
    if FOOTBALL_API_KEY:
        try:
            resp = requests.get(
                f'{API_BASE}/fixtures',
                params={'date': search_date},
                headers={'x-apisports-key': FOOTBALL_API_KEY},
                timeout=15,
            )
            if resp.status_code == 200:
                fixtures = resp.json().get('response', [])
        except Exception as e:
            log.warning(f'  fixtures {search_date}: fetch failed — {e}')
    cache[search_date] = fixtures
    return fixtures


def _name_match(a: str, b: str) -> bool:
    """Fuzzy team-name equality on normalised names."""
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and (a in b or b in a):
        return True
    if len(a) >= 6 and len(b) >= 6 and a[:6] == b[:6]:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.78


def _find_fixture(home: str, away: str | None, date_str: str | None,
                  cache: dict[str, list]) -> tuple[int | None, datetime | None]:
    """
    Find (fixture_id, kickoff_utc). Requires BOTH teams to match in the SAME
    orientation (home->home, away->away) so we never map odds to the wrong side.
    If `away` is unknown we cannot safely resolve a fixture -> return (None, None).
    Searches the date and ±1 day to absorb timezone-edge kickoffs.
    """
    if not away:
        return None, None

    base = datetime.now(timezone.utc)
    if date_str:
        base = _parse_dt(date_str) or base

    home_n, away_n = _norm(home), _norm(away)
    for delta in (0, -1, 1):
        day = (base + timedelta(days=delta)).strftime('%Y-%m-%d')
        for f in _fetch_fixtures_for_date(day, cache):
            f_home_n = _norm(f.get('teams', {}).get('home', {}).get('name', ''))
            f_away_n = _norm(f.get('teams', {}).get('away', {}).get('name', ''))
            if _name_match(home_n, f_home_n) and _name_match(away_n, f_away_n):
                fid = f.get('fixture', {}).get('id')
                ko = _parse_dt(f.get('fixture', {}).get('date'))
                if fid:
                    return fid, ko
    return None, None


# ── Outcome → closing probability mapper ────────────────────────────────────

def _outcome_to_closing_prob(outcome: str, closing: dict) -> float | None:
    """Map a trade outcome label to a closing probability from the JSONB blob."""
    ol = outcome.lower()
    h2h = closing.get('h2h', {})
    ht_h2h = closing.get('ht_h2h', {})

    # Halftime outcomes
    if 'ht_home' in ol or 'leading at halftime' in ol:
        return ht_h2h.get('home')
    if 'ht_away' in ol:
        return ht_h2h.get('away')
    if 'ht_draw' in ol:
        return ht_h2h.get('draw')

    # Full-time 1X2
    if 'home_win' == ol or 'home' == ol:
        return h2h.get('home')
    if 'away_win' == ol or 'away' == ol:
        return h2h.get('away')
    if 'draw' in ol:
        return h2h.get('draw')

    # Named team wins
    if 'win' in ol:
        home_name = _norm(closing.get('home', ''))
        away_name = _norm(closing.get('away', ''))
        ol_clean = _norm(ol)
        if home_name and home_name[:6] in ol_clean:
            return h2h.get('home')
        if away_name and away_name[:6] in ol_clean:
            return h2h.get('away')

    # NOT outcomes
    if ol.startswith('not ') or ol.startswith('no '):
        inner = re.sub(r'^(not |no )', '', ol).strip()
        inner_prob = _outcome_to_closing_prob(inner, closing)
        if inner_prob is not None:
            return round(1.0 - inner_prob, 6)

    # Over/Under totals (full-time)
    m = re.search(r'(over|under)[_ ]?(\d+)[_ ](\d+)', ol)
    if m:
        key = f'{m.group(1)}_{m.group(2)}_{m.group(3)}'
        if key in closing:
            return closing[key]
    m2 = re.search(r'(over|under)\s+([\d.]+)', ol)
    if m2:
        line_str = m2.group(2).replace('.', '_')
        key = f'{m2.group(1)}_{line_str}'
        if key in closing:
            return closing[key]

    # BTTS — check if we have team totals to derive it
    if 'btts' in ol or 'both teams' in ol:
        # api-football doesn't give BTTS directly from Pinnacle
        # but we could derive from team totals (future enhancement)
        return None

    # Handicap/spread
    #   "home wins by N+" == home -(N-0.5) == the negative ('m') handicap line.
    #   e.g. home_wins_by_2plus -> P(home -1.5) -> spread_home_m1_5.
    m = re.search(r'(home|away)_wins_by_(\d+)plus', ol)
    if m:
        side = m.group(1)
        margin = int(m.group(2))
        line_key = _spread_line_key(-(margin - 0.5))
        return closing.get(f'spread_{side}_{line_key}')

    return None


# ── Main ─────────────────────────────────────────────────────────────────────

def _existing_mbk(trade: dict) -> float | None:
    """minutes_before_kickoff of the snapshot already stored on a trade, if any."""
    snap = trade.get('closing_sharp_odds')
    if not snap:
        return None
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except (ValueError, TypeError):
            return None
    return snap.get('minutes_before_kickoff')


def _should_capture(trade: dict, mins_to_ko: float, backfill: bool) -> bool:
    """
    Capture if it gets us a closing line closer to kickoff than what we have.
    New capture's closeness = mins_to_ko (smaller-but-non-negative is better).
    """
    have = trade.get('closing_sharp_odds')
    if backfill:
        return not have  # backfill only fills genuine gaps
    existing = _existing_mbk(trade)
    if not have or existing is None:
        return True  # nothing usable stored yet
    # Only overwrite when the new snapshot is a *better* (closer, non-negative) line.
    if mins_to_ko < 0:
        return False  # never replace a real pre-KO line with a post-KO grab
    return mins_to_ko < existing


def run(dry_run: bool = False, backfill: bool = False) -> dict:
    if not FOOTBALL_API_KEY:
        log.error('FOOTBALL_API_KEY not set')
        return {'error': 'no_api_key', 'updated': 0}

    now = datetime.now(timezone.utc)
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Pull open trades. We re-capture pre-kickoff (to chase the closing line), so
    # we do NOT filter on closing_sharp_odds here — _should_capture decides.
    if backfill:
        cur.execute("""
            SELECT pt.id AS trade_id, pt.outcome, pt.entry_price, pt.entry_odds,
                   pt.reasoning, pm.title AS market_title, pm.resolution_time,
                   pm.raw_metadata, pt.closing_sharp_odds
            FROM paper_trades pt
            LEFT JOIN pm_markets pm ON pm.id = pt.market_id
            WHERE pt.result IS NULL AND pt.closing_sharp_odds IS NULL
            ORDER BY pt.placed_at
        """)
    else:
        cur.execute("""
            SELECT pt.id AS trade_id, pt.outcome, pt.entry_price, pt.entry_odds,
                   pt.reasoning, pm.title AS market_title, pm.resolution_time,
                   pm.raw_metadata, pt.closing_sharp_odds
            FROM paper_trades pt
            LEFT JOIN pm_markets pm ON pm.id = pt.market_id
            WHERE pt.result IS NULL
              AND pm.resolution_time BETWEEN %s AND %s
            ORDER BY pm.resolution_time
        """, (now - timedelta(hours=3), now + timedelta(hours=SELECT_WINDOW_HOURS)))

    trades = [dict(r) for r in cur.fetchall()]
    if not trades:
        log.info('No open trades in window')
        conn.close()
        return {'updated': 0}

    # Cluster trades into real matches (metadata-first, sibling-recovered).
    groups = _build_match_groups(trades)
    log.info(f'{len(trades)} open trade(s) across {len(groups)} match(es)')

    updated = matched = skipped_early = skipped_late = 0
    date_cache: dict[str, list] = {}      # date → fixtures list (1 API call/date)
    odds_cache: dict[int, dict | None] = {}  # fixture_id → closing odds

    for group in groups:
        home, away = group['home'], group['away']
        meta_ko = group.get('kickoff')  # exact PM kickoff when metadata present

        # Cheap early-gate using the metadata kickoff — avoids any API call for
        # matches still far from kickoff (skipped in backfill).
        if meta_ko and not backfill:
            mins = (meta_ko - now).total_seconds() / 60.0
            if mins > CAPTURE_LEAD_MIN:
                skipped_early += 1
                log.info(f'  {home} vs {away or "?"}: KO in {mins:.0f}min — too early')
                continue
            if mins < -POST_KO_GRACE_MIN:
                skipped_late += 1
                continue

        fixture_id, fx_ko = _find_fixture(home, away, group.get('date'), date_cache)
        kickoff = meta_ko or fx_ko
        if not fixture_id or kickoff is None:
            log.info(f'  {home} vs {away or "?"}: no fixture/kickoff found — retry next run')
            continue

        mins_to_ko = (kickoff - now).total_seconds() / 60.0

        # ── kickoff-indexed gating (skipped in backfill) ──
        if not backfill:
            if mins_to_ko > CAPTURE_LEAD_MIN:
                skipped_early += 1
                log.info(f'  {home} vs {away}: KO in {mins_to_ko:.0f}min — too early')
                continue
            if mins_to_ko < -POST_KO_GRACE_MIN:
                skipped_late += 1
                continue

        # Do any trades in this group still want a (better) capture?
        wanters = [t for t in group['trades'] if _should_capture(t, mins_to_ko, backfill)]
        if not wanters:
            continue

        if fixture_id in odds_cache:
            closing = odds_cache[fixture_id]
        else:
            closing = _fetch_pinnacle_odds(fixture_id)
            odds_cache[fixture_id] = closing
        if not closing:
            log.info(f'  {home} vs {away}: no Pinnacle odds yet — retry next run')
            continue

        closing['home'] = home
        closing['away'] = away
        closing['source'] = 'api-football-pinnacle'
        closing['kickoff_utc'] = kickoff.isoformat()
        closing['minutes_before_kickoff'] = round(mins_to_ko, 1)
        matched += len(wanters)

        for trade in wanters:
            closing_prob = _outcome_to_closing_prob(trade['outcome'], closing)
            if dry_run:
                entry_p = float(trade['entry_price']) if trade['entry_price'] else None
                clv_str = ''
                if entry_p and closing_prob and closing_prob > 0:
                    clv_str = f' CLV={(1.0/entry_p)*closing_prob - 1:+.4f}'
                log.info(f'  #{trade["trade_id"]} {trade["outcome"][:36]:36s} '
                         f'KO-{mins_to_ko:.0f}min prob={closing_prob or "n/a":>8}{clv_str}')
            else:
                cur2 = conn.cursor()
                cur2.execute(
                    "UPDATE paper_trades SET closing_sharp_odds = %s WHERE id = %s",
                    (json.dumps(closing), trade['trade_id']))
                updated += 1

    if not dry_run:
        conn.commit()
    conn.close()

    log.info(f'Done: matched {matched}, updated {updated} '
             f'(skipped {skipped_early} too-early, {skipped_late} too-late)')
    return {'total': len(trades), 'matched': matched, 'updated': updated,
            'skipped_early': skipped_early, 'skipped_late': skipped_late}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--backfill', action='store_true',
                        help='Process all open trades missing closing odds (not just upcoming)')
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, backfill=args.backfill)
    log.info(f'Result: {json.dumps(result)}')


if __name__ == '__main__':
    main()
