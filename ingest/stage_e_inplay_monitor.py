#!/usr/bin/env python3
"""
Stage E: In-play monitor — Polymarket vs Sharp Poisson model.

Polls every N minutes during live football matches:
  1. Fetches current PM prices for moneyline + O/U markets (CLOB API)
  2. Fetches live Pinnacle + Betfair odds (The Odds API — in-play)
  3. Gets live score + minute from The Odds API scores endpoint
  4. Runs Poisson in-play model given (score, minute, pre-match lambda)
  5. Computes edges for: home_win, draw, away_win, O1.5, U1.5, O2.5, U2.5
  6. Logs price snapshots to inplay_snapshots table (always)
  7. Creates paper_trade records for edges > threshold (configurable)

Usage:
    python stage_e_inplay_monitor.py --probe                  # show prices + edges, no DB writes
    python stage_e_inplay_monitor.py --sport soccer_uefa_champs_league
    python stage_e_inplay_monitor.py                          # run once across all target sports
    python stage_e_inplay_monitor.py --loop --interval 300    # poll every 5 min (daemon mode)
    python stage_e_inplay_monitor.py --event-id <odds_api_id> # specific event

Architecture:
    Pre-match lambda is derived from Pinnacle pre-match h2h odds (from DB or live fetch).
    In-play Poisson adjusts lambda for remaining time + current score.
    Sharp in-play consensus uses The Odds API live odds (Pinnacle + Betfair) when available,
    otherwise falls back to Poisson model.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from scipy.stats import poisson

load_dotenv(Path(__file__).parent / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

DATABASE_URL   = os.getenv('DATABASE_URL')
ODDS_API_KEY   = os.getenv('THE_ODDS_API_KEY')
ODDS_API_BASE  = 'https://api.the-odds-api.com/v4'

POLYMARKET_CLOB = 'https://clob.polymarket.com'
POLYMARKET_GAMMA = 'https://gamma-api.polymarket.com'

TARGET_SPORTS = [
    'soccer_uefa_champs_league',
    'soccer_epl',
    'soccer_spain_la_liga',
    'soccer_germany_bundesliga',
    'soccer_italy_serie_a',
    'soccer_france_ligue_one',
    'soccer_uefa_europa_league',
]

SHARP_BOOKS = {
    'pinnacle':      0.60,
    'betfair_ex_eu': 0.40,
}

EDGE_THRESHOLD_PP = 3.0   # higher bar for in-play (more noise)
STAKE_UNITS       = 1.0
POLL_INTERVAL_S   = 300   # 5 minutes default


# ─── Poisson in-play model ────────────────────────────────────────────────────

class PoissonInplay:
    """
    Given pre-match expected goals (lambda_home, lambda_away),
    current score (h_goals, a_goals), and minutes elapsed,
    compute fair probabilities for all outcomes.

    Method:
      - Scale lambdas by fraction of time remaining
      - Compute P(home_remaining = i, away_remaining = j) for i,j in 0..8
      - Aggregate into: home_win, draw, away_win, over/under N.5
    """

    def __init__(
        self,
        lambda_home: float,
        lambda_away: float,
        home_goals: int,
        away_goals: int,
        minute: int,
        total_minutes: int = 90,
    ):
        self.lh0 = lambda_home
        self.la0 = lambda_away
        self.hg  = home_goals
        self.ag  = away_goals
        self.min = minute
        self.tot = total_minutes

        frac_remaining = max(0.0, (total_minutes - minute) / total_minutes)
        self.lh_rem = lambda_home * frac_remaining
        self.la_rem = lambda_away * frac_remaining

    def _goal_grid(self, max_goals: int = 9) -> list[list[float]]:
        """Returns grid[i][j] = P(home scores i more, away scores j more)."""
        grid = []
        for i in range(max_goals):
            row = []
            for j in range(max_goals):
                # Cast to plain float — scipy returns numpy.float64 which psycopg2/json can't handle
                p = float(poisson.pmf(i, self.lh_rem) * poisson.pmf(j, self.la_rem))
                row.append(p)
            grid.append(row)
        return grid

    def probabilities(self, max_goals: int = 9) -> dict:
        grid = self._goal_grid(max_goals)

        home_win = draw = away_win = 0.0
        over_1_5 = over_2_5 = btts_yes = 0.0

        for i in range(max_goals):
            for j in range(max_goals):
                p = grid[i][j]
                # Final score
                fh = self.hg + i
                fa = self.ag + j

                if fh > fa:
                    home_win += p
                elif fh == fa:
                    draw += p
                else:
                    away_win += p

                total_goals = fh + fa
                if total_goals > 1.5:
                    over_1_5 += p
                if total_goals > 2.5:
                    over_2_5 += p
                if fh > 0 and fa > 0:
                    btts_yes += p

        # Normalise (grid may not sum to exactly 1 due to truncation)
        total = home_win + draw + away_win
        if total > 0:
            home_win /= total
            draw     /= total
            away_win /= total

        return {
            'home_win':  home_win,
            'draw':      draw,
            'away_win':  away_win,
            'over_1_5':  over_1_5,
            'under_1_5': 1 - over_1_5,
            'over_2_5':  over_2_5,
            'under_2_5': 1 - over_2_5,
            'btts_yes':  btts_yes,
            'btts_no':   1 - btts_yes,
            # context
            'lambda_home_remaining': round(self.lh_rem, 4),
            'lambda_away_remaining': round(self.la_rem, 4),
            'minute': self.min,
            'score': f'{self.hg}-{self.ag}',
        }


# ─── The Odds API ─────────────────────────────────────────────────────────────

def fetch_live_scores(sport: str) -> list[dict]:
    """Fetch current live scores (in-play events) for a sport."""
    url = f'{ODDS_API_BASE}/sports/{sport}/scores'
    resp = requests.get(url, params={
        'apiKey': ODDS_API_KEY,
        'daysFrom': 1,
    }, timeout=20)
    if resp.status_code != 200:
        log.warning(f'Scores API {sport}: {resp.status_code}')
        return []
    events = resp.json()
    # Filter to in-progress events only
    live = [e for e in events if e.get('completed') is False
            and e.get('scores') is not None]
    log.info(f'  {sport}: {len(live)} live event(s)')
    return live


def parse_score(event: dict) -> tuple[int, int] | None:
    """Extract (home_goals, away_goals) from event scores list."""
    scores = event.get('scores') or []
    score_map = {s['name']: s['score'] for s in scores}
    home = event.get('home_team')
    away = event.get('away_team')
    try:
        return int(score_map.get(home, 0)), int(score_map.get(away, 0))
    except (ValueError, TypeError):
        return None


def fetch_live_odds(sport: str, event_id: str) -> dict | None:
    """Fetch live h2h odds for a specific event."""
    url = f'{ODDS_API_BASE}/sports/{sport}/events/{event_id}/odds'
    resp = requests.get(url, params={
        'apiKey':     ODDS_API_KEY,
        'regions':    'eu',
        'markets':    'h2h',
        'bookmakers': ','.join(SHARP_BOOKS.keys()),
        'oddsFormat': 'decimal',
    }, timeout=20)
    if resp.status_code != 200:
        log.warning(f'  Live odds {event_id}: {resp.status_code}')
        return None
    return resp.json()


def sharp_consensus_from_event(event: dict) -> dict | None:
    """Compute vig-removed sharp consensus from a bookmakers list in an event dict."""
    book_probs = {}
    home = event.get('home_team', '')
    away = event.get('away_team', '')

    for bk in event.get('bookmakers', []):
        key = bk['key']
        if key not in SHARP_BOOKS:
            continue
        for mkt in bk.get('markets', []):
            if mkt['key'] != 'h2h':
                continue
            raw = {o['name']: float(o['price']) for o in mkt['outcomes']}
            home_odds = raw.get(home)
            draw_odds = raw.get('Draw')
            away_odds = raw.get(away)
            if not (home_odds and away_odds):
                continue
            h_imp = 1 / home_odds
            d_imp = (1 / draw_odds) if draw_odds else 0
            a_imp = 1 / away_odds
            total  = h_imp + d_imp + a_imp
            book_probs[key] = {
                'home': h_imp / total,
                'draw': d_imp / total if draw_odds else None,
                'away': a_imp / total,
                'home_odds': home_odds,
                'draw_odds': draw_odds,
                'away_odds': away_odds,
            }

    if not book_probs:
        return None

    total_w = sum(SHARP_BOOKS[k] for k in book_probs)

    def wavg(field):
        vals = [(book_probs[k][field], SHARP_BOOKS[k])
                for k in book_probs if book_probs[k].get(field) is not None]
        if not vals:
            return None
        return sum(v * w for v, w in vals) / sum(w for _, w in vals)

    sharp = {
        'home_prob':  wavg('home'),
        'draw_prob':  wavg('draw'),
        'away_prob':  wavg('away'),
        'sources':    book_probs,
    }
    total = (sharp['home_prob'] or 0) + (sharp['draw_prob'] or 0) + (sharp['away_prob'] or 0)
    if total > 0:
        for k in ('home_prob', 'draw_prob', 'away_prob'):
            if sharp[k] is not None:
                sharp[k] /= total
    return sharp


def estimate_minute(event: dict) -> int:
    """
    Best-effort estimate of current match minute from wall-clock time.

    Timeline (real minutes after kickoff):
      0–47  → first half in progress  → match_minute = elapsed_min
      47–62 → half-time break         → match_minute = 45
      62+   → second half in progress → match_minute = 45 + (elapsed_min - 62)

    Assumes:
      - First half lasts ~47 real minutes (45 + ~2 stoppage)
      - Half-time lasts ~15 real minutes
      - Second half starts at ~62 real minutes after kickoff
    """
    commence = event.get('commence_time')
    if not commence:
        return 45  # fallback

    try:
        kick        = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        now         = datetime.now(timezone.utc)
        elapsed_min = int((now - kick).total_seconds() / 60)

        if elapsed_min <= 47:
            # First half in progress
            match_minute = elapsed_min
        elif elapsed_min <= 62:
            # Half-time break
            match_minute = 45
        else:
            # Second half in progress
            match_minute = 45 + (elapsed_min - 62)

        return max(1, min(90, match_minute))
    except Exception:
        return 45


# ─── Polymarket price fetcher ─────────────────────────────────────────────────

def fetch_pm_live_price(external_id: str) -> float | None:
    """
    Fetch current Yes price for a PM market from Gamma API using external_id.
    outcomePrices is returned as a JSON-encoded string; parse it before indexing.
    outcomePrices[0] = Yes price (first outcome is always Yes in binary markets).
    Returns None on failure.
    """
    try:
        resp = requests.get(
            f'{POLYMARKET_GAMMA}/markets/{external_id}',
            timeout=10,
        )
        if resp.status_code != 200:
            log.debug(f'  PM live price fetch {external_id}: {resp.status_code}')
            return None
        data = resp.json()
        raw_prices = data.get('outcomePrices')
        if not raw_prices:
            return None
        # outcomePrices is a JSON-encoded string like '["0.655","0.345"]'
        if isinstance(raw_prices, str):
            prices = json.loads(raw_prices)
        else:
            prices = raw_prices  # already a list
        return float(prices[0])  # index 0 = Yes price
    except Exception as e:
        log.debug(f'  PM live price fetch failed ({external_id}): {e}')
    return None


def fetch_pm_prices_for_match(
    home_team: str,
    away_team: str,
    conn,
    use_live: bool = True,
) -> list[dict]:
    """
    Look up PM markets for this match, returning current live prices.

    Steps:
      1. Query pm_markets from DB to find relevant markets (title fuzzy match)
      2. Skip tournament-winner markets (market_type = 'winner')
      3. Fetch current price from Gamma API using external_id
         (falls back to last DB snapshot if API call fails)

    Returns list of dicts: {market_id, title, outcome, pm_price, market_type, source}
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    h8 = home_team[:8]
    a8 = away_team[:8]

    # Get market list (no snapshot join yet — we'll fetch live prices separately)
    cur.execute("""
        SELECT pmm.id AS market_id, pmm.title, pmm.market_type, pmm.external_id
        FROM pm_markets pmm
        WHERE
            pmm.status = 'active'
            AND pmm.market_type != 'winner'
            AND (
                pmm.title ILIKE %s OR pmm.title ILIKE %s
                OR pmm.title ILIKE %s OR pmm.title ILIKE %s
            )
        ORDER BY pmm.id
    """, (f'%{home_team}%', f'%{away_team}%', f'%{h8}%', f'%{a8}%'))
    markets = [dict(r) for r in cur.fetchall()]

    if not markets:
        return []

    results = []
    for mkt in markets:
        mid = mkt['market_id']
        ext_id = mkt['external_id']
        price_source = 'live'

        # Try live price first
        yes_price = fetch_pm_live_price(ext_id) if (use_live and ext_id) else None

        if yes_price is None:
            # Fallback: last DB snapshot
            cur.execute("""
                SELECT outcome, price FROM pm_market_snapshots
                WHERE market_id = %s AND outcome = 'Yes'
                ORDER BY observed_at DESC LIMIT 1
            """, (mid,))
            row = cur.fetchone()
            if row:
                yes_price = float(row['price'])
                price_source = 'db_snapshot'

        if yes_price is None:
            log.debug(f'  No price for market {mid}: {mkt["title"][:50]}')
            continue

        results.append({
            'market_id':   mid,
            'title':       mkt['title'],
            'market_type': mkt['market_type'],
            'outcome':     'Yes',
            'pm_price':    yes_price,
            'source':      price_source,
        })

        # Also get No price (= 1 - Yes in binary markets)
        results.append({
            'market_id':   mid,
            'title':       mkt['title'],
            'market_type': mkt['market_type'],
            'outcome':     'No',
            'pm_price':    round(1 - yes_price, 4),
            'source':      price_source,
        })

    return results


# ─── Edge computation ─────────────────────────────────────────────────────────

OUTCOME_MAP = {
    # PM market title patterns → (outcome_key_in_poisson, side_label)
    'win':  {
        'home': ('home_win', 'Yes (home win)'),
        'away': ('away_win', 'Yes (away win)'),
    },
    'draw': ('draw', 'Yes (draw)'),
    'o/u 1.5': {
        'over':  ('over_1_5', 'Yes (Over 1.5)'),
        'under': ('under_1_5', 'Yes (Under 1.5)'),
    },
    'o/u 2.5': {
        'over':  ('over_2_5', 'Yes (Over 2.5)'),
        'under': ('under_2_5', 'Yes (Under 2.5)'),
    },
}


def classify_pm_market(
    title: str,
    home_team: str,
    away_team: str,
    outcome: str,
    poisson_probs: dict,
) -> tuple[str, str, float] | None:
    """
    Maps a PM market (title + outcome) to the corresponding Poisson probability.

    For Yes outcomes: returns the Poisson prob for that event happening.
    For No outcomes: returns the Poisson prob for that event NOT happening.
    Both directions can have positive edge — PM can overprice or underprice either side.

    Returns (side_key, bet_label, sharp_prob) or None if unmappable.
    """
    t = title.lower()
    o = outcome.lower()
    is_yes = (o == 'yes')
    is_no  = (o == 'no')
    if not (is_yes or is_no):
        return None

    h_lc = home_team.lower()
    a_lc = away_team.lower()
    h8   = h_lc[:8]
    a8   = a_lc[:8]

    # Home win
    if 'will' in t and h8 in t and 'win' in t and 'draw' not in t:
        p = poisson_probs['home_win']
        if is_yes:
            return ('home_win_yes', f'Yes ({home_team} win)', p)
        else:
            return ('home_win_no', f'No ({home_team} win)', 1 - p)

    # Away win
    if 'will' in t and a8 in t and 'win' in t and 'draw' not in t:
        p = poisson_probs['away_win']
        if is_yes:
            return ('away_win_yes', f'Yes ({away_team} win)', p)
        else:
            return ('away_win_no', f'No ({away_team} win)', 1 - p)

    # Draw
    if 'draw' in t and 'spread' not in t:
        p = poisson_probs['draw']
        if is_yes:
            return ('draw_yes', 'Yes (draw)', p)
        else:
            return ('draw_no', 'No (draw)', 1 - p)

    # O/U 1.5
    if 'o/u 1.5' in t or ('1.5' in t and ('over' in t or 'under' in t)):
        if 'over' in t:
            p = poisson_probs['over_1_5']
            if is_yes:
                return ('over_1_5_yes', 'Yes (Over 1.5)', p)
            else:
                return ('over_1_5_no', 'No (Over 1.5)', 1 - p)
        if 'under' in t:
            p = poisson_probs['under_1_5']
            if is_yes:
                return ('under_1_5_yes', 'Yes (Under 1.5)', p)
            else:
                return ('under_1_5_no', 'No (Under 1.5)', 1 - p)

    # O/U 2.5
    if 'o/u 2.5' in t or ('2.5' in t and ('over' in t or 'under' in t)):
        if 'over' in t:
            p = poisson_probs['over_2_5']
            if is_yes:
                return ('over_2_5_yes', 'Yes (Over 2.5)', p)
            else:
                return ('over_2_5_no', 'No (Over 2.5)', 1 - p)
        if 'under' in t:
            p = poisson_probs['under_2_5']
            if is_yes:
                return ('under_2_5_yes', 'Yes (Under 2.5)', p)
            else:
                return ('under_2_5_no', 'No (Under 2.5)', 1 - p)

    # O/U 3.5
    if 'o/u 3.5' in t or ('3.5' in t and ('over' in t or 'under' in t)):
        return None  # skip for now (would need over_3_5 in Poisson model)

    # BTTS
    if 'both teams to score' in t or 'btts' in t:
        if is_yes:
            return ('btts_yes', 'Yes (BTTS)', poisson_probs['btts_yes'])
        else:
            return ('btts_no', 'No (BTTS)', poisson_probs['btts_no'])

    return None


# ─── Pre-match lambda lookup ──────────────────────────────────────────────────

def odds_to_lambda(h_odds: float, d_odds: float, a_odds: float) -> tuple[float, float]:
    """
    Convert Pinnacle h2h odds to expected goals (lambda_home, lambda_away).

    Method:
      1. Vig-remove the three-way odds
      2. Use the home/away win ratio + assumed total goals (2.5 European avg)
         to split into lh, la.
    """
    h_imp = 1 / h_odds
    d_imp = 1 / d_odds if d_odds else 0
    a_imp = 1 / a_odds
    total = h_imp + d_imp + a_imp
    ph = h_imp / total
    pa = a_imp / total

    # lh + la ≈ 2.5 (European UCL average total goals)
    # lh / la ≈ (ph / pa) ^ 0.5  (heuristic from Dixon-Coles)
    total_goals = 2.5
    if pa > 0 and ph > 0:
        ratio = ph / pa
        la = total_goals / (1 + ratio ** 0.5)
        lh = total_goals - la
    else:
        lh, la = 1.35, 1.05
    return max(0.3, lh), max(0.3, la)


def get_prematch_lambda(
    home_team: str,
    away_team: str,
    conn,
    prematch_odds: tuple[float, float, float] | None = None,
) -> tuple[float, float]:
    """
    Derive pre-match goal expectation (lambda_home, lambda_away).

    Priority:
      1. Caller-supplied prematch_odds (H, D, A) e.g. from --prematch-odds CLI arg
      2. DB lookup in match_odds (domestic leagues covered by Stage A)
      3. The Odds API odds stored in odds_api_snapshots table (if available)
      4. Fallback: European UCL average (1.35 home, 1.05 away)
    """
    # 1. Caller-supplied
    if prematch_odds:
        h_odds, d_odds, a_odds = prematch_odds
        lh, la = odds_to_lambda(h_odds, d_odds, a_odds)
        log.info(f'  Pre-match lambda from supplied odds ({h_odds}/{d_odds}/{a_odds}): '
                 f'lh={lh:.3f} la={la:.3f}')
        return lh, la

    # 2. DB match_odds lookup (domestic leagues — Football-Data Stage A)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT mo.home_odds, mo.draw_odds, mo.away_odds
            FROM match_odds mo
            JOIN matches m ON m.id = mo.match_id
            JOIN bookmakers b ON b.id = mo.bookmaker_id
            JOIN teams th ON th.id = m.home_team_id
            JOIN teams ta ON ta.id = m.away_team_id
            WHERE
                (th.canonical_name ILIKE %s OR th.canonical_name ILIKE %s)
                AND (ta.canonical_name ILIKE %s OR ta.canonical_name ILIKE %s)
                AND b.name ILIKE '%%pinnacle%%'
                AND m.kickoff_utc >= NOW() - INTERVAL '2 days'
            ORDER BY m.kickoff_utc DESC
            LIMIT 1
        """, (
            f'%{home_team[:8]}%', f'%{home_team}%',
            f'%{away_team[:8]}%', f'%{away_team}%',
        ))
        row = cur.fetchone()
        if row and all(row):
            h_odds, d_odds, a_odds = float(row[0]), float(row[1]), float(row[2])
            lh, la = odds_to_lambda(h_odds, d_odds, a_odds)
            log.info(f'  Pre-match lambda from DB: lh={lh:.3f} la={la:.3f}')
            return lh, la
    except Exception as e:
        conn.rollback()
        log.debug(f'  DB lambda lookup failed: {e}')

    # 3. Check odds_api_snapshots (populated by stage_d if it stores pre-match odds)
    try:
        cur.execute("""
            SELECT home_odds, draw_odds, away_odds
            FROM odds_api_snapshots
            WHERE
                (home_team ILIKE %s OR home_team ILIKE %s)
                AND (away_team ILIKE %s OR away_team ILIKE %s)
                AND snapshot_time >= NOW() - INTERVAL '12 hours'
            ORDER BY snapshot_time DESC
            LIMIT 1
        """, (
            f'%{home_team[:8]}%', f'%{home_team}%',
            f'%{away_team[:8]}%', f'%{away_team}%',
        ))
        row = cur.fetchone()
        if row and all(row):
            h_odds, d_odds, a_odds = float(row[0]), float(row[1]), float(row[2])
            lh, la = odds_to_lambda(h_odds, d_odds, a_odds)
            log.info(f'  Pre-match lambda from odds_api_snapshots: lh={lh:.3f} la={la:.3f}')
            return lh, la
    except Exception:
        conn.rollback()  # table may not exist — clear failed transaction state

    # 4. Fallback: UCL average (teams are roughly balanced at this level)
    log.info(f'  Pre-match lambda: no source found → using UCL defaults (1.30, 1.10)')
    return 1.30, 1.10


# ─── Paper trade writer ───────────────────────────────────────────────────────

def get_or_create_inplay_strategy(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE name = 'PM-vs-Poisson In-Play' LIMIT 1")
    row = cur.fetchone()
    if row:
        return row[0]

    # Need a hypothesis_id (NOT NULL constraint)
    cur.execute("""
        SELECT id FROM research_hypotheses
        WHERE title ILIKE '%in-play%' OR title ILIKE '%inplay%' OR title ILIKE '%PM%sharp%'
        LIMIT 1
    """)
    hyp = cur.fetchone()
    if not hyp:
        cur.execute("""
            INSERT INTO research_hypotheses (
                title, description, rationale, source, status, created_by
            ) VALUES (
                'PM In-Play Poisson Edge',
                'Polymarket in-play markets (moneyline + O/U) are systematically mispriced vs Poisson model calibrated on Pinnacle pre-match odds.',
                'PM user base lacks in-play pricing sophistication. O/U and draw markets lag real-time score + time adjustments.',
                'agent', 'live', 'agent'
            ) RETURNING id
        """)
        hyp_id = cur.fetchone()[0]
        conn.commit()
    else:
        hyp_id = hyp[0]

    cur.execute("""
        INSERT INTO strategies (hypothesis_id, name, rules, promoted_at)
        VALUES (%s, 'PM-vs-Poisson In-Play', %s::jsonb, NOW())
        RETURNING id
    """, (hyp_id, json.dumps({
        'description': 'Back PM in-play outcomes where price < Poisson fair value by ≥ 3pp.',
        'edge_threshold_pp': EDGE_THRESHOLD_PP,
        'model': 'Poisson with Pinnacle pre-match lambda scaling',
        'sharp_benchmark': 'Poisson model + live Pinnacle/Betfair when available',
        'stake': '1u flat',
        'markets': 'Polymarket football — moneyline + O/U',
        'phase': 'paper-only',
    }),))
    strat_id = cur.fetchone()[0]
    conn.commit()
    log.info(f'  Created PM-vs-Poisson In-Play strategy #{strat_id}')
    return strat_id


def create_inplay_paper_trade(
    edge: dict,
    poisson_probs: dict,
    strategy_id: int,
    conn,
) -> int | None:
    """
    Insert a paper trade. Returns trade id or None if this exact position
    was already traded in the last 15 minutes (deduplication guard).
    """
    cur = conn.cursor()

    # Dedup: don't re-enter same market+outcome within 15 minutes
    cur.execute("""
        SELECT id FROM paper_trades
        WHERE market_id = %s
          AND outcome = %s
          AND strategy_id = %s
          AND placed_at >= NOW() - INTERVAL '15 minutes'
        LIMIT 1
    """, (edge['market_id'], edge['bet_label'], strategy_id))
    if cur.fetchone():
        log.debug(f'  Dedup skip: already have {edge["bet_label"]} on market {edge["market_id"]}')
        return None

    reasoning = (
        f"PM in-play Poisson edge.\n"
        f"Match: {edge['home_team']} vs {edge['away_team']}\n"
        f"Score: {poisson_probs['score']}  Minute: {poisson_probs['minute']}'\n"
        f"Pick: {edge['bet_label']}\n"
        f"\n"
        f"PM price:      {edge['pm_price']:.4f}  (odds {edge['pm_odds']:.3f})\n"
        f"Poisson fair:  {edge['sharp_prob']:.4f}  (odds {edge['sharp_odds']:.3f})\n"
        f"Edge:          +{edge['edge_pp']:.2f}pp\n"
        f"Expected value: +{edge['expected_value']*100:.2f}%\n"
        f"\n"
        f"Poisson model: lambda_remaining_home={poisson_probs['lambda_home_remaining']:.4f}, "
        f"lambda_remaining_away={poisson_probs['lambda_away_remaining']:.4f}\n"
        f"\n"
        f"Thesis: PM in-play prices lag Poisson adjustments for score + time elapsed.\n"
        f"O/U markets especially slow to update when game remains scoreless.\n"
    )

    cur.execute("""
        INSERT INTO paper_trades (
            strategy_id, market_id,
            outcome, entry_price, entry_odds,
            model_probability, sharp_consensus_price, sharp_consensus_sources,
            expected_edge, confidence, stake_units,
            reasoning, placed_at
        ) VALUES (
            %s, %s,
            %s, %s, %s,
            %s, %s, %s::jsonb,
            %s, %s, %s,
            %s, NOW()
        ) RETURNING id
    """, (
        strategy_id,
        edge['market_id'],
        edge['bet_label'],
        edge['pm_price'],
        edge['pm_odds'],
        edge['sharp_prob'],
        edge['sharp_prob'],
        json.dumps({
            'model': 'Poisson in-play',
            'poisson_probs': {k: round(v, 4) for k, v in poisson_probs.items()
                               if isinstance(v, float)},
            'score': poisson_probs['score'],
            'minute': poisson_probs['minute'],
            'lambda_home_rem': poisson_probs['lambda_home_remaining'],
            'lambda_away_rem': poisson_probs['lambda_away_remaining'],
        }),
        round(edge['edge_pp'], 4),
        round(min(edge['edge_pp'] / 10.0, 1.0), 4),
        STAKE_UNITS,
        reasoning,
    ))
    trade_id = cur.fetchone()[0]
    conn.commit()
    return trade_id


# ─── Core: process one live event ─────────────────────────────────────────────

def process_live_event(
    event: dict,
    sport: str,
    conn,
    probe: bool = False,
    prematch_odds: tuple[float, float, float] | None = None,
) -> list[dict]:
    """
    Main per-event loop:
      1. Parse current score + estimate minute
      2. Derive pre-match lambda from DB
      3. Run Poisson model
      4. Fetch PM prices from DB
      5. Compute edges
      6. If probe: just print; otherwise create paper trades
    Returns list of edges found.
    """
    home = event['home_team']
    away = event['away_team']
    event_id = event['id']
    commence = event.get('commence_time', '')

    score = parse_score(event)
    if score is None:
        log.warning(f'  {home} vs {away}: no score available, skipping')
        return []
    hg, ag = score
    minute = estimate_minute(event)

    log.info(f'\n{"="*60}')
    log.info(f'LIVE: {home} {hg}-{ag} {away}  ~{minute}\'')
    log.info(f'      {commence[:16]}  event_id={event_id}')

    # Pre-match lambda
    lh, la = get_prematch_lambda(home, away, conn, prematch_odds=prematch_odds)
    log.info(f'  Pre-match lambdas: lh={lh:.3f}  la={la:.3f}')

    # Poisson in-play model
    model = PoissonInplay(lh, la, hg, ag, minute)
    probs = model.probabilities()

    log.info(f'  Poisson in-play probabilities:')
    log.info(f'    {home} win: {probs["home_win"]:.3f}  ({1/probs["home_win"]:.2f})')
    log.info(f'    Draw:       {probs["draw"]:.3f}  ({1/probs["draw"]:.2f})')
    log.info(f'    {away} win: {probs["away_win"]:.3f}  ({1/probs["away_win"]:.2f})')
    log.info(f'    Over 1.5:   {probs["over_1_5"]:.3f}  Under 1.5: {probs["under_1_5"]:.3f}')
    log.info(f'    Over 2.5:   {probs["over_2_5"]:.3f}  Under 2.5: {probs["under_2_5"]:.3f}')

    # Fetch live PM prices (Gamma API, fallback to DB snapshot)
    pm_rows = fetch_pm_prices_for_match(home, away, conn, use_live=True)
    if not pm_rows:
        log.info(f'  No PM markets found in DB for this match')
        return []

    all_edges = []
    strategy_id = None if probe else get_or_create_inplay_strategy(conn)

    # pm_rows is now a flat list of {market_id, title, market_type, outcome, pm_price, source}
    # Each market has a Yes row (and a No row — we mostly care about Yes)
    for mkt_data in pm_rows:
        mid      = mkt_data['market_id']
        title    = mkt_data['title']
        outcome  = mkt_data['outcome']
        pm_price = float(mkt_data['pm_price'])

        mapped = classify_pm_market(title, home, away, outcome, probs)
        if mapped is None:
            continue
        side_key, bet_label, sharp_prob = mapped

        if sharp_prob <= 0 or pm_price <= 0:
            continue

        edge_pp   = (sharp_prob - pm_price) * 100
        pm_odds   = 1 / pm_price
        sharp_odds = 1 / sharp_prob
        ev        = (sharp_prob * pm_odds) - 1

        price_src = mkt_data.get('source', '?')
        sign = '✅' if edge_pp >= EDGE_THRESHOLD_PP else ('~' if edge_pp > 0 else '❌')
        log.info(f'  [{mid}] {title[:52]} [{price_src}]')
        log.info(f'    {bet_label}: PM={pm_price:.3f} ({pm_odds:.2f}) '
                 f'Poisson={sharp_prob:.3f} ({sharp_odds:.2f}) '
                 f'edge={edge_pp:+.2f}pp EV={ev*100:+.2f}% {sign}')

        if edge_pp >= EDGE_THRESHOLD_PP:
            edge = {
                'market_id': mid,
                'title':     title,
                'side':      side_key,
                'bet_label': bet_label,
                'pm_price':  pm_price,
                'pm_odds':   pm_odds,
                'sharp_prob': sharp_prob,
                'sharp_odds': sharp_odds,
                'edge_pp':   edge_pp,
                'expected_value': ev,
                'home_team': home,
                'away_team': away,
                'minute':    minute,
                'score':     f'{hg}-{ag}',
            }
            all_edges.append(edge)

            if not probe and strategy_id:
                trade_id = create_inplay_paper_trade(edge, probs, strategy_id, conn)
                if trade_id:
                    log.info(f'    ✅ Paper trade #{trade_id} created')
                else:
                    log.info(f'    ↩ Skipped (already traded this position recently)')

    if not all_edges:
        log.info(f'  No edges ≥ {EDGE_THRESHOLD_PP}pp found for this match')

    return all_edges


# ─── Main loop ────────────────────────────────────────────────────────────────

def run_once(
    sports: list[str],
    conn,
    probe: bool = False,
    event_id_filter: str | None = None,
    prematch_odds: tuple[float, float, float] | None = None,
) -> list[dict]:
    """Fetch all live events and process them."""
    all_edges = []
    seen_events = set()

    for sport in sports:
        try:
            live_events = fetch_live_scores(sport)
        except Exception as e:
            log.warning(f'  {sport}: scores fetch failed ({e})')
            continue

        for event in live_events:
            eid = event['id']
            if eid in seen_events:
                continue
            if event_id_filter and eid != event_id_filter:
                continue
            seen_events.add(eid)

            try:
                edges = process_live_event(
                    event, sport, conn, probe=probe, prematch_odds=prematch_odds
                )
                all_edges.extend(edges)
            except Exception as e:
                log.error(f'  Error processing {event.get("home_team")} vs '
                          f'{event.get("away_team")}: {e}', exc_info=True)

    return all_edges


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--probe',      action='store_true',
                        help='Print edges, no DB writes')
    parser.add_argument('--loop',       action='store_true',
                        help='Run continuously (daemon mode)')
    parser.add_argument('--interval',   type=int, default=POLL_INTERVAL_S,
                        help=f'Polling interval in seconds (default {POLL_INTERVAL_S})')
    parser.add_argument('--sport',      default=None,
                        help='Single sport key (e.g. soccer_uefa_champs_league)')
    parser.add_argument('--event-id',   default=None,
                        help='Filter to a single Odds API event ID')
    parser.add_argument('--prematch-odds', default=None,
                        metavar='H/D/A',
                        help='Pre-match Pinnacle h2h odds to use as lambda source '
                             '(e.g. "3.25/2.98/2.41"). Bypasses DB lookup.')
    args = parser.parse_args()

    # Parse --prematch-odds
    prematch_odds = None
    if args.prematch_odds:
        try:
            parts = [float(x) for x in args.prematch_odds.split('/')]
            if len(parts) == 3:
                prematch_odds = tuple(parts)
            else:
                log.error('--prematch-odds must be H/D/A e.g. 3.25/2.98/2.41')
                sys.exit(1)
        except ValueError:
            log.error('--prematch-odds must be three floats: H/D/A')
            sys.exit(1)

    if not ODDS_API_KEY:
        log.error('THE_ODDS_API_KEY not set in ingest/.env')
        sys.exit(1)

    sports = [args.sport] if args.sport else TARGET_SPORTS
    conn   = psycopg2.connect(DATABASE_URL)

    log.info('In-play monitor starting')
    log.info(f'  Sports: {sports}')
    log.info(f'  Edge threshold: {EDGE_THRESHOLD_PP}pp')
    log.info(f'  Mode: {"probe" if args.probe else "live"}')
    if args.loop:
        log.info(f'  Loop mode: every {args.interval}s')

    try:
        while True:
            t_start = time.time()
            edges = run_once(
                sports, conn,
                probe=args.probe,
                event_id_filter=args.event_id,
                prematch_odds=prematch_odds,
            )
            elapsed = time.time() - t_start

            log.info(f'\nCycle complete in {elapsed:.1f}s — '
                     f'{len(edges)} edge(s) found')

            if not args.loop:
                break

            sleep_s = max(0, args.interval - elapsed)
            log.info(f'Sleeping {sleep_s:.0f}s until next poll...\n')
            time.sleep(sleep_s)

    finally:
        conn.close()
        log.info('Monitor stopped')


if __name__ == '__main__':
    main()
