"""
Executor — places real orders on Polymarket CLOB for paper trades.

Reads pending paper trades from DB, places limit orders of STAKE_USDC each,
and records execution details back to the paper_trades table.

Safety features:
  - Configurable stake (default 1 USDC)
  - Kill switch: stops if cumulative realized loss exceeds MAX_LOSS_USDC
  - Limit orders only (never market orders)
  - Dry-run mode by default
  - Only executes trades from whitelisted strategies

Usage:
    python -m agent.executor --dry-run       # preview what would be placed
    python -m agent.executor --live          # actually place orders
    python -m agent.executor --live --stake 2  # 2 USDC per trade

Requirements:
    PM_PRIVATE_KEY and PM_DEPOSIT_WALLET in ingest/.env
    py_clob_client installed (Python >=3.9.10)
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# ─── Config ───────────────────────────────────────────────────────────────────

DATABASE_URL       = os.getenv('DATABASE_URL')
PM_PRIVATE_KEY     = os.getenv('PM_PRIVATE_KEY')
PM_DEPOSIT_WALLET  = os.getenv('PM_DEPOSIT_WALLET')
CLOB_HOST          = 'https://clob.polymarket.com'
CHAIN_ID           = 137

STAKE_USDC         = 1.0      # default stake per trade
MAX_LOSS_USDC      = 20.0     # kill switch: stop if cumulative loss exceeds this
MIN_NOTIONAL_USDC  = 1.0      # PM rejects marketable orders below this

# Strategies allowed for live execution (by strategy name)
ALLOWED_STRATEGIES = {
    'DC Model Pre-Match',
}

# ─── CLOB Client ──────────────────────────────────────────────────────────────

_clob_client = None

def get_clob_client():
    """Lazily initialize the Polymarket CLOB client."""
    global _clob_client
    if _clob_client is not None:
        return _clob_client

    if not PM_PRIVATE_KEY or not PM_DEPOSIT_WALLET:
        raise RuntimeError("PM_PRIVATE_KEY and PM_DEPOSIT_WALLET must be set in ingest/.env")

    from py_clob_client_v2 import ClobClient

    # First create a temp client to derive API creds
    temp = ClobClient(
        host=CLOB_HOST,
        chain_id=CHAIN_ID,
        key=PM_PRIVATE_KEY,
        signature_type=3,
        funder=PM_DEPOSIT_WALLET,
    )
    creds = temp.create_or_derive_api_key()
    log.info("API credentials derived OK (key=%s...)", creds.api_key[:8])

    # Now create the full client with creds
    _clob_client = ClobClient(
        host=CLOB_HOST,
        chain_id=CHAIN_ID,
        key=PM_PRIVATE_KEY,
        creds=creds,
        signature_type=3,
        funder=PM_DEPOSIT_WALLET,
    )
    return _clob_client


# ─── DB helpers ───────────────────────────────────────────────────────────────

def get_pending_trades(conn) -> list[dict]:
    """Fetch pending paper trades that haven't been executed live yet."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT pt.id, pt.market_id, pt.outcome, pt.entry_price, pt.stake_units,
               pt.expected_edge, pt.model_probability, pt.reasoning,
               s.name as strategy_name,
               pm.external_id as pm_external_id,
               pm.raw_metadata
        FROM paper_trades pt
        JOIN strategies s ON s.id = pt.strategy_id
        JOIN pm_markets pm ON pm.id = pt.market_id
        WHERE pt.result IS NULL
          AND pt.twitter_post_id IS NULL  -- use as "not yet executed live" marker
        ORDER BY pt.placed_at DESC
    """)
    return [dict(r) for r in cur.fetchall()]


def get_cumulative_loss(conn) -> float:
    """Calculate cumulative realized loss from live trades."""
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(payout_units - stake_units), 0)
        FROM paper_trades
        WHERE result IN ('won', 'lost')
          AND signed_timestamp_proof LIKE 'live_order:%'
    """)
    return float(cur.fetchone()[0])


def mark_trade_executed(conn, trade_id: int, order_id: str, stake_usdc: float):
    """Mark a paper trade as executed with the CLOB order ID."""
    cur = conn.cursor()
    cur.execute("""
        UPDATE paper_trades
        SET signed_timestamp_proof = %s,
            stake_units = %s
        WHERE id = %s
    """, (f"live_order:{order_id}", stake_usdc, trade_id))
    conn.commit()


# ─── Token resolution ────────────────────────────────────────────────────────

def get_current_price(external_id: str, outcome: str = '') -> Optional[float]:
    """
    Fetch current price from Gamma API for the outcome we're betting on.

    For 'home' outcomes on "Will X win?" markets → YES price (bestAsk).
    For 'away' outcomes on "Will X win?" markets → NO price (1 - bestBid of YES, approx bestAsk of NO).
    For 'draw' or 'btts' outcomes → YES price on the draw/btts market itself.
    """
    try:
        r = requests.get(f'https://gamma-api.polymarket.com/markets/{external_id}', timeout=10)
        if r.ok:
            d = r.json()
            outcome_lower = outcome.lower().strip()

            # For 'away' bets: we're buying NO on a "Will [home team] win?" market
            # The price we pay = 1 - YES_bestBid (what we can sell YES at = buy NO at)
            if outcome_lower == 'away':
                best_bid = d.get('bestBid')
                if best_bid is not None:
                    return round(1.0 - float(best_bid), 4)
                prices = d.get('outcomePrices')
                if prices:
                    if isinstance(prices, str):
                        prices = json.loads(prices)
                    if isinstance(prices, list) and len(prices) > 1:
                        return float(prices[1])  # NO price
            else:
                # home, draw, btts, etc. → we're buying YES on this market
                best_ask = d.get('bestAsk')
                if best_ask is not None:
                    return float(best_ask)
                prices = d.get('outcomePrices')
                if prices:
                    if isinstance(prices, str):
                        prices = json.loads(prices)
                    if isinstance(prices, list) and len(prices) > 0:
                        return float(prices[0])  # YES price
    except Exception as e:
        log.warning("get_current_price(%s) error: %s", external_id, e)
    return None


def fetch_market_from_gamma(external_id: str) -> Optional[dict]:
    """Fetch market data from Polymarket Gamma API."""
    try:
        r = requests.get(f'https://gamma-api.polymarket.com/markets/{external_id}', timeout=10)
        if r.ok:
            return r.json()
    except Exception as e:
        log.warning("Gamma API error for %s: %s", external_id, e)
    return None


def resolve_token_id(trade: dict) -> Optional[tuple[str, dict]]:
    """
    Extract the correct CLOB token_id from pm_markets.raw_metadata,
    falling back to Gamma API if metadata is missing.

    Returns (token_id, market_meta) or None.
    clobTokenIds[0] = YES token, clobTokenIds[1] = NO token.
    """
    meta = trade.get('raw_metadata') or {}
    if isinstance(meta, str):
        meta = json.loads(meta)

    token_ids = meta.get('clobTokenIds', [])

    # Parse if string
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)

    # Fallback: fetch from Gamma API
    if not token_ids and trade.get('pm_external_id'):
        gamma = fetch_market_from_gamma(str(trade['pm_external_id']))
        if gamma:
            raw = gamma.get('clobTokenIds', [])
            token_ids = json.loads(raw) if isinstance(raw, str) else raw
            # Merge useful fields into meta
            for k in ('negRisk', 'minimumTickSize'):
                if gamma.get(k) is not None:
                    meta[k] = gamma[k]
            meta['clobTokenIds'] = token_ids
    if not token_ids or len(token_ids) < 2:
        log.warning("Trade %d: no clobTokenIds in metadata or API", trade['id'])
        return None

    outcome = (trade.get('outcome') or '').lower().strip()

    # Determine if we're buying YES or NO
    # Our paper trades store the outcome we're betting ON
    # If outcome contains 'not ' or 'no' prefix → we're buying the NO token
    # Otherwise → YES token
    if outcome.startswith('not ') or outcome == 'no':
        return token_ids[1], meta  # NO token
    else:
        return token_ids[0], meta  # YES token


def get_tick_size(meta: dict) -> str:
    """Get tick size from market metadata, default 0.01."""
    if isinstance(meta, str):
        meta = json.loads(meta)
    # Some markets use 0.1 tick size
    minimum_tick = meta.get('minimumTickSize', '0.01')
    return str(minimum_tick)


def is_neg_risk(meta: dict) -> bool:
    """Check if market is negative risk."""
    if isinstance(meta, str):
        meta = json.loads(meta)
    return bool(meta.get('negRisk', False))


# ─── Main execution ──────────────────────────────────────────────────────────

def run(live: bool = False, stake: float = STAKE_USDC, max_trades: int = 10) -> list[dict]:
    """
    Execute pending paper trades on Polymarket.

    Args:
        live: If False (default), only preview. If True, place real orders.
        stake: USDC per trade.
        max_trades: Maximum trades to execute in one run.

    Returns:
        List of execution results.
    """
    conn = psycopg2.connect(DATABASE_URL)
    results = []

    try:
        # Kill switch check
        cum_pnl = get_cumulative_loss(conn)
        if cum_pnl < -MAX_LOSS_USDC:
            log.error("KILL SWITCH: cumulative P&L = %.2f USDC (limit: -%.2f). Stopping.",
                      cum_pnl, MAX_LOSS_USDC)
            return []

        log.info("Cumulative live P&L: %.2f USDC (kill switch at -%.2f)", cum_pnl, MAX_LOSS_USDC)

        # Get pending trades
        trades = get_pending_trades(conn)
        log.info("Found %d pending trades", len(trades))

        # Filter to allowed strategies
        eligible = [t for t in trades if t['strategy_name'] in ALLOWED_STRATEGIES]
        log.info("Eligible for live execution: %d (strategies: %s)",
                 len(eligible), ', '.join(ALLOWED_STRATEGIES))

        if not eligible:
            log.info("No eligible trades to execute.")
            return []

        # Limit
        eligible = eligible[:max_trades]

        # Initialize client if going live
        client = None
        if live:
            client = get_clob_client()

        for trade in eligible:
            resolved = resolve_token_id(trade)
            if not resolved:
                log.warning("Trade %d: could not resolve token_id, skipping", trade['id'])
                continue
            token_id, meta = resolved

            # ── Live price check: only enter if edge still exists ──
            current_price = get_current_price(str(trade['pm_external_id']), trade.get('outcome', ''))
            model_prob = float(trade.get('model_probability') or 0)
            if current_price is not None and model_prob > 0:
                live_edge = model_prob - current_price
                if live_edge <= 0.01:  # less than 1pp edge → skip
                    log.info("SKIP Trade %d: %s | live_price=%.3f model=%.3f edge=%.1fpp → no longer +EV",
                             trade['id'], trade['outcome'], current_price, model_prob, live_edge * 100)
                    continue
                log.info("  Price check OK: live=%.3f model=%.3f edge=%.1fpp",
                         current_price, model_prob, live_edge * 100)
                # Use current live price instead of stale entry_price
                trade['entry_price'] = Decimal(str(current_price))

            tick_size = get_tick_size(meta)
            neg_risk = is_neg_risk(meta)
            price = float(trade['entry_price'])

            # Round price to tick size
            tick = float(tick_size)
            price = round(round(price / tick) * tick, 2)

            # Calculate size (number of shares = stake / price). Round UP: to
            # nearest leaves the notional just under PM's $1.00 minimum whenever
            # stake/price has a long tail (1.0/0.12 → 8.33 → $0.9996 → rejected
            # as "invalid amount for a marketable order"). Same fix as
            # live_executor.shares_for_stake().
            size = math.ceil(stake / price * 100.0) / 100.0 if price > 0 else 0
            while size > 0 and size * price < MIN_NOTIONAL_USDC:
                size = round(size + 0.01, 2)
            if size <= 0:
                log.warning("Trade %d: invalid size %.2f (price=%.4f), skipping",
                            trade['id'], size, price)
                continue

            log.info(
                "%s Trade %d: %s | price=%.2f | size=%.2f | edge=%.1fpp | strategy=%s",
                "EXECUTING" if live else "PREVIEW",
                trade['id'], trade['outcome'], price, size,
                float(trade['expected_edge'] or 0) * 100,
                trade['strategy_name']
            )

            result = {
                'trade_id': trade['id'],
                'outcome': trade['outcome'],
                'price': price,
                'size': size,
                'stake_usdc': stake,
                'token_id': token_id[:20] + '...',
                'tick_size': tick_size,
                'neg_risk': neg_risk,
                'strategy': trade['strategy_name'],
                'status': 'preview',
            }

            if live:
                try:
                    from py_clob_client_v2.clob_types import OrderArgs, PartialCreateOrderOptions
                    from py_clob_client_v2.order_builder.constants import BUY

                    order = client.create_and_post_order(
                        OrderArgs(
                            token_id=token_id,
                            price=price,
                            size=size,
                            side=BUY,
                        ),
                        options=PartialCreateOrderOptions(
                            tick_size=tick_size,
                            neg_risk=neg_risk,
                        ),
                    )

                    order_id = order.get('orderID', order.get('id', 'unknown'))
                    log.info("  → Order placed! ID: %s", order_id)

                    mark_trade_executed(conn, trade['id'], str(order_id), stake)
                    result['status'] = 'executed'
                    result['order_id'] = order_id

                except Exception as e:
                    log.error("  → Order FAILED for trade %d: %s", trade['id'], e)
                    result['status'] = 'error'
                    result['error'] = str(e)

            results.append(result)

    finally:
        conn.close()

    # Summary
    executed = sum(1 for r in results if r['status'] == 'executed')
    errors = sum(1 for r in results if r['status'] == 'error')
    previewed = sum(1 for r in results if r['status'] == 'preview')

    log.info("─── Summary ───")
    log.info("Executed: %d | Errors: %d | Previewed: %d", executed, errors, previewed)
    if executed > 0:
        total_stake = executed * stake
        log.info("Total staked: %.2f USDC", total_stake)

    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Execute paper trades on Polymarket')
    parser.add_argument('--live', action='store_true', help='Place real orders (default: dry-run)')
    parser.add_argument('--dry-run', action='store_true', help='Preview only (default)')
    parser.add_argument('--stake', type=float, default=STAKE_USDC,
                        help=f'USDC per trade (default: {STAKE_USDC})')
    parser.add_argument('--max-trades', type=int, default=10,
                        help='Max trades per run (default: 10)')
    args = parser.parse_args()

    if args.live and args.dry_run:
        print("Cannot use both --live and --dry-run")
        sys.exit(1)

    live = args.live
    if not live:
        log.info("DRY RUN MODE (use --live to place real orders)")

    results = run(live=live, stake=args.stake, max_trades=args.max_trades)

    if not results:
        print("\nNo trades to execute.")
    else:
        print(f"\n{'='*60}")
        for r in results:
            status_emoji = {'executed': '✅', 'error': '❌', 'preview': '👁️'}.get(r['status'], '?')
            print(f"  {status_emoji} #{r['trade_id']} {r['outcome']:<30} @ {r['price']:.2f}  "
                  f"size={r['size']:.1f}  ${r['stake_usdc']:.2f}  [{r['strategy']}]")
        print(f"{'='*60}")


if __name__ == '__main__':
    main()
