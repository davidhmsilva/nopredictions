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

# Real sports CLV essentially never exceeds this. Anything larger is a data
# artifact (bad outcome mapping / longshot extraction noise), not edge.
MAX_PLAUSIBLE_CLV = 0.50


def _safe_market_clv(entry_odds: float | None,
                     closing_prob: float | None) -> float | None:
    """
    Compute real closing-line value, or None if the inputs are untrustworthy.

    clv = entry_odds / closing_odds - 1 = entry_odds * closing_prob - 1

    Rejects: missing inputs, impossible closing prob (<=0 or >=1), and
    implausibly large |clv| (> MAX_PLAUSIBLE_CLV) which signals a mapping bug.
    """
    if not entry_odds or closing_prob is None:
        return None
    if not (0.0 < closing_prob < 1.0):
        return None
    clv = entry_odds * closing_prob - 1.0
    if abs(clv) > MAX_PLAUSIBLE_CLV:
        return None
    return round(clv, 4)


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


def _get_closing_from_sharp_snapshot(closing_sharp_odds: dict | None,
                                     outcome_label: str) -> float | None:
    """
    Extract closing probability from the closing_sharp_odds JSONB blob
    captured by closing_collector.py.
    """
    if not closing_sharp_odds:
        return None

    ol = outcome_label.lower()
    h2h = closing_sharp_odds.get('h2h', {})

    # 1X2
    if 'home_win' == ol or ('home' in ol and 'ht_' not in ol and 'wins_by' not in ol):
        return h2h.get('home')
    if 'away_win' == ol or ('away' in ol and 'ht_' not in ol and 'wins_by' not in ol):
        return h2h.get('away')
    if 'draw' in ol and 'ht_' not in ol:
        return h2h.get('draw')

    # Named team wins
    if 'win' in ol and 'ht_' not in ol:
        home_name = _norm_team(closing_sharp_odds.get('home', ''))
        away_name = _norm_team(closing_sharp_odds.get('away', ''))
        ol_clean = re.sub(r'\b(fc|cf|sc|ac|afc)\b', '', ol, flags=re.I).strip()
        if home_name and home_name[:6] in ol_clean.lower():
            return h2h.get('home')
        if away_name and away_name[:6] in ol_clean.lower():
            return h2h.get('away')

    # NOT outcomes
    if ol.startswith('not ') or ol.startswith('no '):
        inner = re.sub(r'^(not |no )', '', ol).strip()
        inner_prob = _get_closing_from_sharp_snapshot(closing_sharp_odds, inner)
        if inner_prob is not None:
            return round(1.0 - inner_prob, 6)

    # Over/Under totals
    m = re.search(r'(over|under)[_ ]?(\d+)[_ ](\d+)', ol)
    if m:
        key = f'{m.group(1)}_{m.group(2)}_{m.group(3)}'
        if key in closing_sharp_odds:
            return closing_sharp_odds[key]
    m2 = re.search(r'(over|under)\s+([\d.]+)', ol)
    if m2:
        line_str = m2.group(2).replace('.', '_')
        key = f'{m2.group(1)}_{line_str}'
        if key in closing_sharp_odds:
            return closing_sharp_odds[key]

    # Handicap/spread
    m = re.search(r'(home|away)_wins_by_(\d+)plus', ol)
    if m:
        side = m.group(1)
        margin = int(m.group(2))
        spread_line = -(margin - 0.5)
        skey = f'spread_{side}_{str(spread_line).replace(".", "_").replace("-", "m")}'
        return closing_sharp_odds.get(skey)

    return None


def _norm_team(s: str) -> str:
    s = s.lower()
    s = re.sub(r'[^a-z0-9 ]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _get_closing_odds_for_outcome(match_id: int, outcome_label: str) -> tuple[float | None, float | None]:
    """
    Returns (closing_odds, closing_probability) from Pinnacle closing odds
    in the match_odds table (Football-Data historical data). Only works for 1X2.
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

CLOB_API = 'https://clob.polymarket.com'


def _check_clob_resolution(condition_id: str) -> str | None:
    """
    Resolve a market by on-chain conditionId via the CLOB API. Used for trades
    whose external_id is a 0x conditionId (e.g. on-chain / Live Polymarket
    positions synced from the wallet) rather than a numeric Gamma market id.
    The CLOB exposes a definitive per-token ``winner`` flag.
    Returns 'yes' if the first outcome (YES) won, 'no' if the second won, else None.
    """
    try:
        d = requests.get(f'{CLOB_API}/markets/{condition_id}', timeout=10).json()
    except Exception:
        return None
    tokens = d.get('tokens') or []
    if len(tokens) < 2:
        return None
    if tokens[0].get('winner'):
        return 'yes'
    if tokens[1].get('winner'):
        return 'no'
    return None


def _check_pm_resolution(external_id: str, past_resolution_time: bool = False) -> str | None:
    """
    Fetch a Polymarket market and check if it resolved.
    Returns 'yes' if YES resolved, 'no' if NO resolved, None if still open.

    Markets identified by a 0x conditionId (on-chain / Live Polymarket positions)
    are resolved via the CLOB API; numeric Gamma ids via the Gamma API below.

    Resolves when the outcome is genuinely settled — either the market is
    officially ``closed`` by Polymarket, OR the UMA oracle has a proposed/
    resolved outcome — AND prices are at extreme levels (>= 0.99).

    Why both signals: after a game ends, PM markets sit at ``closed=False`` for
    hours during the UMA dispute window, even though ``umaResolutionStatus`` is
    already 'proposed' and the price is 0.9995. Waiting for ``closed=True`` left
    finished games unresolved for hours. ``umaResolutionStatus`` only becomes
    proposed/resolved AFTER the game ends, so it cannot fire mid-match — which is
    the failure mode the price-alone check originally guarded against.
    """
    if external_id and external_id.startswith('0x'):
        return _check_clob_resolution(external_id)

    try:
        r = requests.get(f'{GAMMA_API}/markets/{external_id}', timeout=10)
        if not r.ok:
            return None
        data = r.json()
    except Exception:
        return None

    is_closed = data.get('closed', False)
    uma_status = (data.get('umaResolutionStatus') or '').lower()
    # 'proposed' and 'resolved' both mean the oracle has the result in hand.
    settled = is_closed or uma_status in ('proposed', 'resolved')

    # Fallback for markets that auto-settle to an extreme price without ever
    # showing closed/uma (some halftime / derived markets): trust an extreme
    # price only once the match is unambiguously over (endDate > 3h ago), so
    # this can never fire mid-game.
    if not settled:
        end_dt = None
        try:
            ed = data.get('endDate')
            if ed:
                end_dt = datetime.fromisoformat(str(ed).replace('Z', '+00:00'))
        except (ValueError, TypeError):
            end_dt = None
        now = datetime.now(timezone.utc)
        if end_dt and (now - end_dt).total_seconds() > 3 * 3600:
            settled = True  # game long over; the price check below still guards

    if not settled:
        return None

    raw_prices = data.get('outcomePrices')
    if not raw_prices:
        return None
    prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
    if len(prices) < 2:
        return None

    yes_price = float(prices[0])
    no_price = float(prices[1])

    if yes_price >= RESOLUTION_THRESHOLD:
        return 'yes'
    if no_price >= RESOLUTION_THRESHOLD:
        return 'no'

    return None


# ─── Settle a single trade ────────────────────────────────────────────────────

def _settle_trade(conn, trade_id: int, result: str, entry_odds: float | None,
                  stake: float, match_id: int | None, outcome_label: str,
                  label: str, closing_sharp_odds: dict | None = None,
                  model_probability: float | None = None) -> dict | None:
    if result == 'won':
        payout = stake * entry_odds if entry_odds else stake
    elif result == 'void':
        payout = stake
    else:
        payout = 0.0

    # Real market CLV only goes in `clv` + `closing_price`. The model-vs-entry
    # diagnostic goes in `model_clv` and is NEVER conflated with real CLV.
    closing_prob, clv, clv_source, model_clv = None, None, None, None

    # Priority 1: closing_sharp_odds JSONB from closing_collector (all market types)
    if closing_sharp_odds:
        sharp_prob = _get_closing_from_sharp_snapshot(closing_sharp_odds, outcome_label)
        candidate = _safe_market_clv(entry_odds, sharp_prob)
        if candidate is not None:
            closing_prob, clv, clv_source = sharp_prob, candidate, 'sharp_closing'
        elif sharp_prob is not None and 0.0 < sharp_prob < 1.0:
            # plausible prob but |clv| too large -> flag for review, don't trust
            clv_source = 'suspect'

    # Priority 2: Pinnacle closing from match_odds table (1X2 only, historical)
    if clv is None and match_id:
        _, closing_prob_legacy = _get_closing_odds_for_outcome(match_id, outcome_label)
        candidate = _safe_market_clv(entry_odds, closing_prob_legacy)
        if candidate is not None:
            closing_prob, clv, clv_source = closing_prob_legacy, candidate, 'pinnacle_fd'
        elif clv_source is None and closing_prob_legacy and 0.0 < closing_prob_legacy < 1.0:
            clv_source = 'suspect'

    # Diagnostic only: how did our entry compare to our own model line?
    if model_probability and 0.0 < float(model_probability) < 1.0 and entry_odds:
        model_clv = round(entry_odds * float(model_probability) - 1.0, 4)
        if clv_source is None:
            clv_source = 'model'  # no real closing line was available

    cur = conn.cursor()
    cur.execute("""
        UPDATE paper_trades SET
            result        = %s,
            payout_units  = %s,
            closing_price = %s,
            clv           = %s,
            clv_source    = %s,
            model_clv     = %s,
            resolved_at   = NOW()
        WHERE id = %s
    """, (result, round(payout, 4), closing_prob, clv, clv_source, model_clv, trade_id))
    conn.commit()

    sign = {'won': 'WIN', 'lost': 'LOSS', 'void': 'VOID'}.get(result, result.upper())
    pl = payout - stake
    if clv is not None:
        clv_str = f'CLV={clv:+.3f} ({clv_source})'
    elif model_clv is not None:
        clv_str = f'CLV=n/a (model_clv={model_clv:+.3f})'
    else:
        clv_str = f'CLV=n/a ({clv_source or "none"})'
    log.info(f'[resolver] #{trade_id} {sign} {label} | {outcome_label} | P&L={pl:+.2f}u | {clv_str}')

    return {
        'trade_id': trade_id,
        'match': label,
        'result': result,
        'payout_units': round(payout, 4),
        'clv': clv,
        'clv_source': clv_source,
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
                pt.closing_sharp_odds,
                pt.model_probability,
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

            cso = trade.get('closing_sharp_odds')
            if isinstance(cso, str):
                cso = json.loads(cso)
            model_prob = float(trade['model_probability']) if trade.get('model_probability') else None

            info = _settle_trade(conn, tid, result, entry_odds,
                                 float(trade['stake_units']), trade['match_id'],
                                 trade['outcome_label'], label,
                                 closing_sharp_odds=cso,
                                 model_probability=model_prob)
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
                pt.closing_sharp_odds,
                pt.model_probability,
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

            cso = trade.get('closing_sharp_odds')
            if isinstance(cso, str):
                cso = json.loads(cso)
            model_prob = float(trade['model_probability']) if trade.get('model_probability') else None

            info = _settle_trade(conn, tid, result, entry_odds,
                                 float(trade['stake_units']), trade.get('match_id'),
                                 trade['outcome_label'], label,
                                 closing_sharp_odds=cso,
                                 model_probability=model_prob)
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
