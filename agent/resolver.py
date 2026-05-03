"""
Resolver — settles open paper trades after matches finish.

For each open paper trade (result IS NULL):
  1. Check if the linked match has a result (home_score IS NOT NULL)
  2. Determine win/loss based on outcome label vs actual result
  3. Fetch Pinnacle closing odds to calculate CLV
  4. Update paper_trades.result, payout_units, clv, closing_price, resolved_at
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

import os
from dotenv import load_dotenv
from .tools.db import run_analysis_query, log_agent_run

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))
DATABASE_URL = os.getenv('DATABASE_URL')


def _conn():
    return psycopg2.connect(DATABASE_URL)

log = logging.getLogger(__name__)


# ─── Result helpers ────────────────────────────────────────────────────────────

def _determine_result(outcome_label: str, home_score: int, away_score: int) -> str | None:
    """
    Map an outcome label and match scores to 'won' | 'lost' | 'void'.
    Returns None if the outcome label is unrecognisable.
    """
    ol = outcome_label.lower()
    hs, as_ = int(home_score), int(away_score)
    total = hs + as_

    # Home win
    if 'home' in ol or (re.search(r'\bwin\b', ol) and 'away' not in ol and 'draw' not in ol):
        return 'won' if hs > as_ else 'lost'

    # Away win
    if 'away' in ol:
        return 'won' if as_ > hs else 'lost'

    # Draw
    if 'draw' in ol:
        return 'won' if hs == as_ else 'lost'

    # Over / under goals
    m = re.search(r'(over|under)\s+([\d.]+)', ol)
    if m:
        direction = m.group(1)
        line = float(m.group(2))
        if direction == 'over':
            return 'won' if total > line else 'lost'
        else:
            return 'won' if total < line else 'lost'

    # BTTS
    if 'btts' in ol or 'both teams' in ol:
        btts_actual = hs > 0 and as_ > 0
        if 'no' in ol:
            return 'won' if not btts_actual else 'lost'
        return 'won' if btts_actual else 'lost'

    return None  # unrecognisable


def _get_closing_odds_for_outcome(match_id: int, outcome_label: str) -> tuple[float | None, float | None]:
    """
    Returns (closing_odds, closing_probability) from Pinnacle closing odds for outcome.
    """
    ol = outcome_label.lower()

    if 'home' in ol or ('win' in ol and 'away' not in ol and 'draw' not in ol):
        col = 'home_odds'
    elif 'away' in ol:
        col = 'away_odds'
    elif 'draw' in ol:
        col = 'draw_odds'
    else:
        return None, None

    rows = run_analysis_query(f"""
        SELECT mo.{col} AS closing_odds,
               1.0 / (mo.home_odds) + 1.0 / (mo.away_odds) +
               COALESCE(1.0 / NULLIF(mo.draw_odds, 0), 0) AS overround
        FROM match_odds mo
        JOIN bookmakers b ON b.id = mo.bookmaker_id
        WHERE mo.match_id = {match_id}
          AND b.name = 'Pinnacle (closing)'
          AND mo.{col} IS NOT NULL
        LIMIT 1
    """)
    if not rows:
        return None, None

    raw_odds = float(rows[0]['closing_odds'])
    overround = float(rows[0]['overround']) if rows[0]['overround'] else 1.0
    closing_prob = (1 / raw_odds) / overround if raw_odds > 0 else None
    return raw_odds, closing_prob


# ─── Main resolver ────────────────────────────────────────────────────────────

def run() -> list[dict]:
    """
    Resolve all open paper trades for matches that have finished.
    Returns list of resolved trade summaries.
    """
    log.info('[resolver] Checking for settled trades...')

    conn = _conn()
    resolved = []

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Find open trades where the linked match has a result
        cur.execute("""
            SELECT
                pt.id       AS trade_id,
                pt.outcome  AS outcome_label,
                pt.entry_price,
                pt.entry_odds,
                pt.stake_units,
                pt.match_id,
                m.home_score,
                m.away_score,
                m.kickoff_utc,
                th.canonical_name AS home_team,
                ta.canonical_name AS away_team
            FROM paper_trades pt
            JOIN matches m      ON m.id = pt.match_id
            JOIN teams th       ON th.id = m.home_team_id
            JOIN teams ta       ON ta.id = m.away_team_id
            WHERE pt.result IS NULL
              AND m.home_score IS NOT NULL
              AND m.away_score IS NOT NULL
            ORDER BY m.kickoff_utc
        """)
        open_trades = [dict(r) for r in cur.fetchall()]

        if not open_trades:
            log.info('[resolver] No settled trades to resolve')
            log_agent_run('resolver', 'complete', 'Nothing to resolve')
            return []

        log.info(f'[resolver] {len(open_trades)} trade(s) to resolve')

        for trade in open_trades:
            tid          = trade['trade_id']
            outcome_label = trade['outcome_label']
            home_score   = trade['home_score']
            away_score   = trade['away_score']
            match_id     = trade['match_id']
            entry_price  = float(trade['entry_price']) if trade['entry_price'] else None
            stake        = float(trade['stake_units'])
            home_team    = trade['home_team']
            away_team    = trade['away_team']

            result = _determine_result(outcome_label, home_score, away_score)
            if result is None:
                log.warning(f'[resolver] Trade #{tid}: cannot parse outcome "{outcome_label}" — skipping')
                continue

            # P&L
            entry_odds = float(trade['entry_odds']) if trade['entry_odds'] else (1 / entry_price if entry_price else None)
            if result == 'won':
                payout = stake * (entry_odds - 1) if entry_odds else stake
            else:
                payout = -stake

            # CLV
            closing_odds, closing_prob = _get_closing_odds_for_outcome(match_id, outcome_label)
            clv = None
            if entry_odds and closing_odds and closing_odds > 0:
                clv = round((entry_odds / closing_odds) - 1, 4)

            # Update
            update_cur = conn.cursor()
            update_cur.execute("""
                UPDATE paper_trades SET
                    result       = %s,
                    payout_units = %s,
                    closing_price = %s,
                    clv          = %s,
                    resolved_at  = NOW()
                WHERE id = %s
            """, (result, round(payout, 4), closing_prob, clv, tid))
            conn.commit()

            sign = '✅' if result == 'won' else '❌'
            clv_str = f'CLV={clv:+.3f}' if clv is not None else 'CLV=n/a'
            log.info(
                f'[resolver] #{tid} {sign} {home_team} vs {away_team} '
                f'({home_score}-{away_score}) | {outcome_label} | '
                f'P&L={payout:+.2f}u | {clv_str}'
            )

            resolved.append({
                'trade_id':     tid,
                'match':        f'{home_team} vs {away_team}',
                'result':       result,
                'payout_units': round(payout, 4),
                'clv':          clv,
            })

        log_agent_run(
            'resolver', 'complete',
            f'Resolved {len(resolved)} trade(s)',
            {'resolved': resolved},
        )

    finally:
        conn.close()

    return resolved
