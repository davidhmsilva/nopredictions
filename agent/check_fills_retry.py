"""
Poll all open live PM orders. For each:
  - MATCHED → mark `matched`, done.
  - LIVE in book → check current PM price. If edge still ≥ MIN_EDGE_PP, and
    the market has moved past our bid, cancel + replace at the new price.
    If edge gone or attempt cap hit, cancel + mark `expired-edge-gone`.
  - CANCELED / EXPIRED → re-evaluate edge; resubmit if still ≥ threshold.
  - Market closed / accepting_orders=False → cancel, mark `expired-market-closed`.

Recommended to cron every 15-30 min while there are open live orders.

Usage:
  python check_fills_retry.py                  # dry-run (default)
  python check_fills_retry.py --execute        # actually update + retry
  python check_fills_retry.py --min-edge 3.0 --max-attempts 5
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / "ingest" / ".env")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "agent"))

from agent.tools.db import get_conn
import live_executor  # subprocess gateway

GAMMA = "https://gamma-api.polymarket.com"
log = logging.getLogger("check_fills")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


# ── PM subprocess helpers ──────────────────────────────────────────────────────

def _get_order(order_id: str) -> dict | None:
    r = live_executor._run_pm(["get_order_json", order_id])
    if not r.get("ok"):
        log.warning(f"  get_order error: {r.get('error')}")
        return None
    return r.get("order")


def _cancel(order_id: str) -> bool:
    r = live_executor._run_pm(["cancel_json", order_id])
    if not r.get("ok"):
        log.warning(f"  cancel error: {r.get('error')}")
        return False
    return True


# ── data fetchers ──────────────────────────────────────────────────────────────

def fetch_open_live_trades(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pt.id, s.name, pt.outcome, pt.entry_price, pt.model_probability,
               pmm.external_id,
               pt.pm_order_id, pt.pm_order_status, pt.pm_order_size, pt.pm_order_price,
               pt.pm_token_id, pt.pm_attempts,
               split_part(pt.reasoning, '—', 1) AS match_name
        FROM paper_trades pt
        JOIN strategies s ON s.id = pt.strategy_id
        JOIN pm_markets pmm ON pmm.id = pt.market_id
        WHERE pt.pm_live = TRUE
          AND pt.result IS NULL
          AND COALESCE(pt.pm_order_status, '') NOT IN ('matched', 'expired-edge-gone',
                                                       'expired-market-closed', 'expired-max-attempts')
        ORDER BY pt.id
        """
    )
    return cur.fetchall()


def fetch_pm_market(ext_id: str) -> dict | None:
    for url in (f"{GAMMA}/markets/{ext_id}", f"{GAMMA}/markets?condition_ids={ext_id}"):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    data = data[0] if data else None
                if data:
                    return data
        except Exception:
            pass
    return None


# ── per-row processor ──────────────────────────────────────────────────────────

def process_trade(conn, row, *, min_edge_pp: float, max_attempts: int, execute: bool):
    (tid, strat, outcome, entry_p, model_p, ext_id,
     order_id, order_status, order_size, order_price,
     token_id, attempts, match_name) = row
    is_nb = strat == "No Bias (DC Model)"
    match_label = match_name.strip()[:50]
    cur = conn.cursor()

    # 1) No active order (e.g., previous retry cancelled but new submit failed).
    # Try to re-submit from scratch if edge still good.
    if not order_id:
        pm = fetch_pm_market(ext_id)
        if not pm:
            log.warning(f"  #{tid} {match_label:50s} no order, market unfetchable")
            return
        try:
            prices = pm.get("outcomePrices")
            if isinstance(prices, str): prices = json.loads(prices)
            yes_p = float(prices[0])
        except Exception:
            return
        current_p = round(1.0 - yes_p, 6) if is_nb else yes_p
        edge_pp = round((float(model_p) - current_p) * 100, 1)
        if edge_pp < min_edge_pp:
            log.info(f"  #{tid} {match_label:50s} no order, edge {edge_pp:+.1f}pp gone → mark expired")
            if execute:
                cur.execute(
                    "UPDATE paper_trades SET pm_order_status='expired-edge-gone' WHERE id=%s",
                    (tid,),
                )
                conn.commit()
            return
        if attempts >= max_attempts:
            log.info(f"  #{tid} {match_label:50s} no order, attempts={attempts} ≥ {max_attempts} → giving up")
            if execute:
                cur.execute("UPDATE paper_trades SET pm_order_status='expired-max-attempts' WHERE id=%s", (tid,))
                conn.commit()
            return
        log.info(f"  #{tid} {match_label:50s} ⤴ no live order, re-submitting @ {current_p:.4f} (edge {edge_pp:+.1f}pp)")
        if execute:
            r = live_executor.try_execute(
                conn, trade_id=tid, token_id=token_id, side="BUY", price=current_p,
            )
            log.info(f"      → {r.pm_order_status} order={r.pm_order_id} err={r.pm_order_error}")
        return

    o = _get_order(order_id)
    if o is None:
        log.warning(f"  #{tid} {match_label:50s} status fetch failed")
        return

    clob_status = (o.get("status") or "").lower()
    size_matched = float(o.get("size_matched") or 0)
    original_size = float(o.get("original_size") or order_size or 0)

    # Persist live status from CLOB
    if execute:
        cur.execute(
            """UPDATE paper_trades
               SET pm_size_matched   = %s,
                   pm_last_checked_at = NOW()
               WHERE id = %s""",
            (size_matched, tid),
        )
        conn.commit()

    # ── 2) Terminal states ────────────────────────────────────────────────────
    if clob_status == "matched":
        log.info(f"  #{tid} {match_label:50s} ✅ MATCHED ({size_matched:.2f}/{original_size:.2f})")
        if execute:
            cur.execute("UPDATE paper_trades SET pm_order_status='matched' WHERE id=%s", (tid,))
            conn.commit()
        return

    # ── 3) Still LIVE in book → re-evaluate edge ──────────────────────────────
    pm = fetch_pm_market(ext_id)
    if not pm:
        log.warning(f"  #{tid} {match_label:50s} could not fetch PM market")
        return

    if pm.get("closed") or not pm.get("accepting_orders", True):
        log.info(f"  #{tid} {match_label:50s} 🚫 market closed/locked → cancelling")
        if execute and _cancel(order_id):
            cur.execute(
                "UPDATE paper_trades SET pm_order_status='expired-market-closed' WHERE id=%s",
                (tid,),
            )
            conn.commit()
        return

    try:
        prices = pm.get("outcomePrices")
        tokens = pm.get("clobTokenIds")
        if isinstance(prices, str): prices = json.loads(prices)
        if isinstance(tokens, str): tokens = json.loads(tokens)
        yes_p = float(prices[0])
    except Exception as e:
        log.warning(f"  #{tid} {match_label:50s} parse err: {e}")
        return

    current_p = round(1.0 - yes_p, 6) if is_nb else yes_p
    edge_pp = round((float(model_p) - current_p) * 100, 1)

    # Edge gone → cancel
    if edge_pp < min_edge_pp:
        log.info(f"  #{tid} {match_label:50s} ❌ edge {edge_pp:+.1f}pp < {min_edge_pp}pp → cancelling")
        if execute and _cancel(order_id):
            cur.execute(
                "UPDATE paper_trades SET pm_order_status='expired-edge-gone', pm_order_error=%s WHERE id=%s",
                (f"edge {edge_pp:.1f}pp at price {current_p:.4f}", tid),
            )
            conn.commit()
        return

    # Attempt cap
    if attempts >= max_attempts:
        log.info(f"  #{tid} {match_label:50s} ⏱️ {attempts} attempts, capped → giving up")
        if execute and _cancel(order_id):
            cur.execute(
                "UPDATE paper_trades SET pm_order_status='expired-max-attempts' WHERE id=%s",
                (tid,),
            )
            conn.commit()
        return

    # Decide: replace at new price or leave alone
    # We're BUYING. Order fills when somebody asks ≤ our price.
    # If current market price > our bid → we're below market, no fill happening, replace at current price.
    # If current market price ≤ our bid → our bid is at or above market, may fill at any time, leave alone.
    our_price = float(order_price)
    if current_p > our_price + 0.005:  # market moved up by ≥ 0.5¢ — replace
        new_price = current_p
        log.info(
            f"  #{tid} {match_label:50s} ↻ market moved {our_price:.4f}→{current_p:.4f}, "
            f"edge still {edge_pp:+.1f}pp → cancel + retry @ {new_price:.4f}"
        )
        if execute:
            if not _cancel(order_id):
                return
            r = live_executor.try_execute(
                conn,
                trade_id=tid,
                token_id=token_id,
                side="BUY",
                price=new_price,
            )
            log.info(f"      → {r.pm_order_status} order={r.pm_order_id} err={r.pm_order_error}")
    else:
        log.info(
            f"  #{tid} {match_label:50s} ✓ resting @ {our_price:.4f} (market {current_p:.4f}, edge {edge_pp:+.1f}pp) — leaving"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--min-edge", type=float, default=3.0)
    ap.add_argument("--max-attempts", type=int, default=5)
    args = ap.parse_args()

    if not args.execute:
        log.info("=== DRY RUN (pass --execute to actually update) ===")

    conn = get_conn()
    rows = fetch_open_live_trades(conn)
    log.info(f"{len(rows)} open live orders to check")

    for r in rows:
        process_trade(conn, r,
                      min_edge_pp=args.min_edge,
                      max_attempts=args.max_attempts,
                      execute=args.execute)

    log.info("done")


if __name__ == "__main__":
    main()
