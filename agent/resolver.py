"""
Resolver — settles open paper trades after matches finish.

Two resolution paths:
  Path A (match-based): trade has match_id → check if match has a result in DB
  Path B (Polymarket-based): trade has market_id → check if PM market resolved via API

For each resolved trade:
  1. Determine win/loss
  2. Fetch Pinnacle closing odds to calculate CLV (when available)
  3. Update paper_trades.result, payout_units, clv, closing_price, resolved_at
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests

import os
from dotenv import load_dotenv

# Support both package import (from agent import resolver) and
# standalone import (import resolver) used by dc_scanner.py
try:
    from .tools.db import run_analysis_query, log_agent_run, find_match_id
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
    from db import run_analysis_query, log_agent_run, find_match_id

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))
DATABASE_URL = os.getenv('DATABASE_URL')
GAMMA_API = 'https://gamma-api.polymarket.com'

RESOLUTION_THRESHOLD = 0.99


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


# ─── Polymarket resolution ────────────────────────────────────────────────────

def _check_pm_resolution(external_id: str, past_resolution_time: bool = False) -> str | None:
    """
    Fetch a Polymarket market and check if it resolved.
    Returns 'yes' if YES resolved, 'no' if NO resolved, None if still open.

    PM sports markets often reach extreme prices (0.00/1.00) before being
    officially marked ``closed``.  We treat a market as resolved when it is
    closed, OR when resolution_time is past and prices hit extreme levels.
    """
    try:
        r = requests.get(f'{GAMMA_API}/markets/{external_id}', timeout=10)
        if not r.ok:
            return None
        data = r.json()
    except Exception:
        return None

    raw_prices = data.get('outcomePrices')
    if not raw_prices:
        return None
    prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
    if len(prices) < 2:
        return None

    yes_price = float(prices[0])
    no_price = float(prices[1])

    is_closed = data.get('closed', False)
    if not is_closed and not past_resolution_time:
        return None

    if yes_price >= RESOLUTION_THRESHOLD:
        return 'yes'
    if no_price >= RESOLUTION_THRESHOLD:
        return 'no'

    return None


# ─── Settle a single trade ────────────────────────────────────────────────────

def _settle_trade(conn, trade_id: int, result: str, entry_odds: float | None,
                  stake: float, match_id: int | None, outcome_label: str,
                  label: str) -> dict | None:
    if result == 'won':
        payout = stake * entry_odds if entry_odds else stake
    elif result == 'void':
        # Match abandoned / postponed → stake refunded
        payout = stake
    else:
        payout = 0.0

    closing_odds, closing_prob, clv = None, None, None
    if match_id:
        closing_odds, closing_prob = _get_closing_odds_for_outcome(match_id, outcome_label)
        if entry_odds and closing_odds and closing_odds > 0:
            clv = round((entry_odds / closing_odds) - 1, 4)

    cur = conn.cursor()
    cur.execute("""
        UPDATE paper_trades SET
            result       = %s,
            payout_units = %s,
            closing_price = %s,
            clv          = %s,
            resolved_at  = NOW()
        WHERE id = %s
    """, (result, round(payout, 4), closing_prob, clv, trade_id))
    conn.commit()

    sign = {'won': 'WIN', 'lost': 'LOSS', 'void': 'VOID'}.get(result, result.upper())
    pl = payout - stake
    clv_str = f'CLV={clv:+.3f}' if clv is not None else 'CLV=n/a'
    log.info(f'[resolver] #{trade_id} {sign} {label} | {outcome_label} | P&L={pl:+.2f}u | {clv_str}')

    return {
        'trade_id': trade_id,
        'match': label,
        'result': result,
        'payout_units': round(payout, 4),
        'clv': clv,
    }


# ─── Backfill match_id on unlinked trades ────────────────────────────────────

def _extract_teams_from_title(title: str) -> tuple[str, str] | None:
    m = re.match(r'(?:will\s+)?(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s*[\-:\?]|$)', title, re.I)
    if m:
        home = re.sub(r'\s+(?:FC|CF|SC|AC|AFC)$', '', m.group(1).strip(), flags=re.I)
        away = re.sub(r'\s+(?:FC|CF|SC|AC|AFC)$', '', m.group(2).strip(), flags=re.I)
        return home, away
    return None


def _backfill_match_ids(conn):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT pt.id AS trade_id, pm.title AS market_title,
               pm.resolution_time, pm.market_type, e.title AS event_title
        FROM paper_trades pt
        JOIN pm_markets pm ON pm.id = pt.market_id
        LEFT JOIN LATERAL (
            SELECT title FROM pm_markets
            WHERE external_id = pm.external_id
            LIMIT 1
        ) e ON true
        WHERE pt.match_id IS NULL AND pt.result IS NULL
    """)
    rows = cur.fetchall()
    if not rows:
        return

    linked = 0
    for row in rows:
        title = row.get('event_title') or row.get('market_title') or ''
        teams = _extract_teams_from_title(title)
        if not teams:
            continue

        kickoff_date = None
        rt = row.get('resolution_time')
        if rt:
            try:
                if isinstance(rt, str):
                    rt = datetime.fromisoformat(rt.replace('Z', '+00:00'))
                kickoff_date = rt.date() if hasattr(rt, 'date') else None
            except (ValueError, TypeError):
                pass

        mid = find_match_id(conn, teams[0], teams[1], kickoff_date, days_window=1)
        if mid:
            cur2 = conn.cursor()
            cur2.execute("UPDATE paper_trades SET match_id = %s WHERE id = %s",
                         (mid, row['trade_id']))
            linked += 1

    if linked:
        conn.commit()
        log.info(f'[resolver] Backfilled match_id on {linked} trade(s)')


# ─── Main resolver ────────────────────────────────────────────────────────────

def run() -> list[dict]:
    """
    Resolve all open paper trades via two paths:
      A) Match-based: trade has match_id + match has score in DB
      B) Polymarket-based: trade has market_id → check PM API for resolution
    """
    log.info('[resolver] Checking for settled trades...')

    conn = _conn()
    resolved = []
    resolved_ids = set()

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # ── Backfill: link unlinked trades to matches ──
        _backfill_match_ids(conn)

        # ── Path A: match-based resolution ──
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
                th.canonical_name AS home_team,
                ta.canonical_name AS away_team
            FROM paper_trades pt
            JOIN matches m      ON m.id = pt.match_id
            JOIN teams th       ON th.id = m.home_team_id
            JOIN teams ta       ON ta.id = m.away_team_id
            WHERE pt.result IS NULL
              AND m.home_score IS NOT NULL
              AND m.away_score IS NOT NULL
        """)

        for trade in cur.fetchall():
            trade = dict(trade)
            tid = trade['trade_id']
            result = _determine_result(trade['outcome_label'], trade['home_score'], trade['away_score'])
            if result is None:
                log.warning(f'[resolver] Trade #{tid}: cannot parse outcome "{trade["outcome_label"]}" — skipping')
                continue

            entry_price = float(trade['entry_price']) if trade['entry_price'] else None
            entry_odds = float(trade['entry_odds']) if trade['entry_odds'] else (1 / entry_price if entry_price else None)
            label = f'{trade["home_team"]} vs {trade["away_team"]} ({trade["home_score"]}-{trade["away_score"]})'

            info = _settle_trade(conn, tid, result, entry_odds,
                                 float(trade['stake_units']), trade['match_id'],
                                 trade['outcome_label'], label)
            if info:
                resolved.append(info)
                resolved_ids.add(tid)

        # ── Path B: Polymarket-based resolution ──
        # Only check markets whose resolution_time is in the past
        # to avoid resolving future games on transient PM API data.
        cur.execute("""
            SELECT
                pt.id       AS trade_id,
                pt.outcome  AS outcome_label,
                pt.entry_price,
                pt.entry_odds,
                pt.stake_units,
                pt.match_id,
                pt.market_id,
                pm.external_id,
                pm.title AS market_title
            FROM paper_trades pt
            JOIN pm_markets pm ON pm.id = pt.market_id
            WHERE pt.result IS NULL
              AND pm.resolution_time IS NOT NULL
              AND pm.resolution_time <= NOW()
        """)

        pm_trades = [dict(r) for r in cur.fetchall()]
        pm_trades = [t for t in pm_trades if t['trade_id'] not in resolved_ids]

        if pm_trades:
            log.info(f'[resolver] Checking {len(pm_trades)} trade(s) via Polymarket API...')

        for trade in pm_trades:
            tid = trade['trade_id']
            ext_id = trade['external_id']
            if not ext_id:
                continue

            pm_resolution = _check_pm_resolution(ext_id, past_resolution_time=True)
            if pm_resolution is None:
                continue

            # Map PM yes/no resolution to won/lost based on trade position.
            # Most trades are YES bets (e.g. "Will X win?" → outcome=home).
            # Trades with "not" in the outcome are NO bets.
            outcome_lower = (trade['outcome_label'] or '').lower()
            is_no_bet = outcome_lower.startswith('not ') or outcome_lower.startswith('no ')
            if is_no_bet:
                result = 'won' if pm_resolution == 'no' else 'lost'
            else:
                result = 'won' if pm_resolution == 'yes' else 'lost'

            entry_price = float(trade['entry_price']) if trade['entry_price'] else None
            entry_odds = float(trade['entry_odds']) if trade['entry_odds'] else (1 / entry_price if entry_price else None)
            label = trade['market_title'] or f'PM market {ext_id}'

            info = _settle_trade(conn, tid, result, entry_odds,
                                 float(trade['stake_units']), trade.get('match_id'),
                                 trade['outcome_label'], label)
            if info:
                resolved.append(info)

        if not resolved:
            log.info('[resolver] No settled trades to resolve')

        log_agent_run(
            'resolver', 'complete',
            f'Resolved {len(resolved)} trade(s)' if resolved else 'Nothing to resolve',
            {'resolved': resolved} if resolved else None,
        )

    finally:
        conn.close()

    return resolved
