#!/usr/bin/env python3
"""
Closing Odds Collector — captures Pinnacle closing odds for open trades.

Uses api-football.com (FOOTBALL_API_KEY) to fetch Pinnacle pre-match odds
for all bet types: Match Winner, Over/Under, Asian Handicap, First Half, BTTS.
Stores vig-removed closing probabilities on each paper_trade row for CLV calculation.

Run ~5-10 min before kickoff windows to capture closing lines.
The resolver then uses closing_sharp_odds JSONB for CLV.

Usage:
    python closing_collector.py                # live — writes to DB
    python closing_collector.py --dry-run      # compute only, no DB writes
    python closing_collector.py --backfill     # all open trades (not just upcoming)

API cost: 1 request per fixture (not per bet type).
Typical run: 5-15 requests for a day's matches.
"""

from __future__ import annotations

import argparse
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

# How far ahead to look for kickoffs (minutes)
LOOKAHEAD_MINUTES = 60


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


def _parse_handicap(values: list[dict], result: dict, prefix: str):
    """Parse Asian Handicap into vig-removed probs per line."""
    lines: dict[str, dict] = {}
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
        line = m.group(2)
        key = f'{side}_{line}'
        if key not in lines:
            lines[key] = {}
        lines[key] = {'odds': o, 'side': side, 'line': line}

    # Group by matching lines (home -X and away +X are the same market)
    paired: dict[str, dict] = {}
    for key, info in lines.items():
        line_val = float(info['line'])
        # Home -1.5 pairs with Away +1.5
        pair_key = f'{abs(line_val)}'
        if pair_key not in paired:
            paired[pair_key] = {}
        paired[pair_key][info['side']] = info['odds']

    for pair_key, odds in paired.items():
        if 'home' not in odds or 'away' not in odds:
            continue
        h_imp = 1 / odds['home']
        a_imp = 1 / odds['away']
        total = h_imp + a_imp
        if total <= 0:
            continue

        line_key = pair_key.replace('.', '_').replace('-', 'm')
        result[f'{prefix}spread_home_{line_key}'] = round(h_imp / total, 6)
        result[f'{prefix}spread_away_{line_key}'] = round(a_imp / total, 6)


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


# ── Find fixture IDs for trades ──────────────────────────────────────────────

def _extract_teams_from_trade(trade: dict) -> tuple[str, str] | None:
    """Extract home/away team names from trade metadata."""
    title = trade.get('market_title') or trade.get('event_title') or ''
    reasoning = trade.get('reasoning') or ''

    # Try "Team A vs Team B" or "Will Team A win?" patterns
    for text in [title, reasoning]:
        m = re.search(
            r'(?:will\s+)?(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s*[\-:\?\n]|$)',
            text, re.I,
        )
        if m:
            home = re.sub(r'\s+(?:FC|CF|SC|AC|AFC)$', '', m.group(1).strip(), flags=re.I)
            away = re.sub(r'\s+(?:FC|CF|SC|AC|AFC)$', '', m.group(2).strip(), flags=re.I)
            return home, away

    return None


def _find_fixture_id(home: str, away: str, date_str: str | None) -> int | None:
    """
    Search api-football for a fixture matching home vs away on a given date.
    """
    if not FOOTBALL_API_KEY:
        return None

    # Try to get date from resolution_time
    search_date = None
    if date_str:
        try:
            if isinstance(date_str, str):
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            else:
                dt = date_str
            search_date = dt.strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            pass

    if not search_date:
        search_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        resp = requests.get(
            f'{API_BASE}/fixtures',
            params={'date': search_date},
            headers={'x-apisports-key': FOOTBALL_API_KEY},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        fixtures = resp.json().get('response', [])
    except Exception:
        return None

    home_n = _norm(home)
    away_n = _norm(away)

    for f in fixtures:
        f_home = f.get('teams', {}).get('home', {}).get('name', '')
        f_away = f.get('teams', {}).get('away', {}).get('name', '')
        f_home_n = _norm(f_home)
        f_away_n = _norm(f_away)

        # Match on first 6 chars of normalised names
        if (home_n[:6] in f_home_n or f_home_n[:6] in home_n) and \
           (away_n[:6] in f_away_n or f_away_n[:6] in away_n):
            fid = f.get('fixture', {}).get('id')
            if fid:
                log.info(f'  Matched: {home} vs {away} → fixture {fid} ({f_home} vs {f_away})')
                return fid

    return None


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
    m = re.search(r'(home|away)_wins_by_(\d+)plus', ol)
    if m:
        side = m.group(1)
        margin = int(m.group(2))
        spread_line = margin - 0.5
        line_key = str(spread_line).replace('.', '_').replace('-', 'm')
        return closing.get(f'spread_{side}_{line_key}')

    return None


# ── Main ─────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, backfill: bool = False) -> dict:
    if not FOOTBALL_API_KEY:
        log.error('FOOTBALL_API_KEY not set')
        return {'error': 'no_api_key', 'updated': 0}

    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Find trades that need closing odds
    if backfill:
        cur.execute("""
            SELECT pt.id AS trade_id, pt.outcome, pt.entry_price, pt.entry_odds,
                   pt.reasoning, pm.title AS market_title, pm.resolution_time,
                   pt.closing_sharp_odds
            FROM paper_trades pt
            LEFT JOIN pm_markets pm ON pm.id = pt.market_id
            WHERE pt.closing_sharp_odds IS NULL
              AND pt.result IS NULL
            ORDER BY pt.placed_at
        """)
    else:
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=LOOKAHEAD_MINUTES)
        cur.execute("""
            SELECT pt.id AS trade_id, pt.outcome, pt.entry_price, pt.entry_odds,
                   pt.reasoning, pm.title AS market_title, pm.resolution_time,
                   pt.closing_sharp_odds
            FROM paper_trades pt
            LEFT JOIN pm_markets pm ON pm.id = pt.market_id
            WHERE pt.closing_sharp_odds IS NULL
              AND pt.result IS NULL
              AND pm.resolution_time BETWEEN %s AND %s
            ORDER BY pm.resolution_time
        """, (now - timedelta(hours=1), cutoff))

    trades = [dict(r) for r in cur.fetchall()]
    if not trades:
        log.info('No trades need closing odds')
        conn.close()
        return {'updated': 0}

    log.info(f'Found {len(trades)} trade(s) needing closing odds')

    # Group trades by match to avoid duplicate fixture lookups
    match_groups: dict[str, list[dict]] = {}
    for trade in trades:
        teams = _extract_teams_from_trade(trade)
        if not teams:
            log.debug(f'  #{trade["trade_id"]}: cannot extract teams — skipping')
            continue
        home, away = teams
        key = f'{_norm(home)[:8]}_{_norm(away)[:8]}'
        if key not in match_groups:
            match_groups[key] = {'home': home, 'away': away, 'trades': [],
                                  'resolution_time': trade.get('resolution_time')}
        match_groups[key]['trades'].append(trade)

    log.info(f'Grouped into {len(match_groups)} unique match(es)')

    # Fetch closing odds per match (with fixture + odds caching)
    updated = 0
    matched = 0
    fixture_cache: dict[int, dict | None] = {}  # fixture_id → closing odds
    fixture_id_cache: dict[str, int | None] = {}  # date_home_away → fixture_id

    for match_key, group in match_groups.items():
        home, away = group['home'], group['away']
        res_time = group.get('resolution_time')
        date_str = str(res_time) if res_time else None

        # Cache fixture lookups by normalised key
        fixture_key = f'{_norm(home)[:6]}_{_norm(away)[:6]}_{date_str or "today"}'
        if fixture_key in fixture_id_cache:
            fixture_id = fixture_id_cache[fixture_key]
        else:
            fixture_id = _find_fixture_id(home, away, date_str)
            fixture_id_cache[fixture_key] = fixture_id

        if not fixture_id:
            log.info(f'  {home} vs {away}: no fixture found on api-football')
            continue

        # Cache odds per fixture (avoid re-fetching same fixture)
        if fixture_id in fixture_cache:
            closing = fixture_cache[fixture_id]
        else:
            closing = _fetch_pinnacle_odds(fixture_id)
            fixture_cache[fixture_id] = closing

        if not closing:
            log.info(f'  {home} vs {away}: no Pinnacle odds available')
            continue

        closing['home'] = home
        closing['away'] = away
        closing['source'] = 'api-football-pinnacle'

        matched += len(group['trades'])

        for trade in group['trades']:
            closing_prob = _outcome_to_closing_prob(trade['outcome'], closing)

            if dry_run:
                entry_p = float(trade['entry_price']) if trade['entry_price'] else None
                clv_str = ''
                if entry_p and closing_prob and closing_prob > 0:
                    entry_odds = 1.0 / entry_p
                    closing_odds = 1.0 / closing_prob
                    clv = (entry_odds / closing_odds) - 1
                    clv_str = f' CLV={clv:+.4f}'
                log.info(
                    f'  #{trade["trade_id"]} {trade["outcome"][:40]:40s} '
                    f'closing_prob={closing_prob or "n/a":>8}{clv_str}'
                )
            else:
                cur2 = conn.cursor()
                cur2.execute("""
                    UPDATE paper_trades
                    SET closing_sharp_odds = %s
                    WHERE id = %s
                """, (json.dumps(closing), trade['trade_id']))
                updated += 1

    if not dry_run:
        conn.commit()

    conn.close()

    log.info(f'Done: {matched} matched, {updated} updated out of {len(trades)} trades')
    return {'total': len(trades), 'matched': matched, 'updated': updated}


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
