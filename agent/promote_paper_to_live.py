"""
Promote open paper_trades (DC Pre-Match + No Bias) to live by:
  1. Looking up the current PM market via Gamma API
  2. Recomputing edge against the stored model_probability
  3. If current edge >= MIN_EDGE_PP, submit a real BUY order
  4. Updating the paper_trades row with pm_live=true + order details

Usage:
  python promote_paper_to_live.py                # dry-run (default, no orders)
  python promote_paper_to_live.py --execute      # actually place orders
  python promote_paper_to_live.py --min-edge 5   # tighter edge filter
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
import live_executor

GAMMA = "https://gamma-api.polymarket.com"
log = logging.getLogger("promote")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")


def fetch_open_paper_trades(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pt.id, s.name, pt.outcome, pt.entry_price, pt.model_probability,
               pmm.external_id, pmm.title,
               split_part(pt.reasoning, '—', 1) AS match_name
        FROM paper_trades pt
        JOIN strategies s ON s.id = pt.strategy_id
        JOIN pm_markets pmm ON pmm.id = pt.market_id
        WHERE pt.result IS NULL
          AND pt.pm_live = FALSE
          AND s.name IN ('DC Model Pre-Match', 'No Bias (DC Model)')
        ORDER BY pt.id
        """
    )
    return cur.fetchall()


def fetch_pm_market(ext_id: str) -> dict | None:
    """ext_id stored in pm_markets is the gamma market id (or sometimes conditionId)."""
    for url in (f"{GAMMA}/markets/{ext_id}", f"{GAMMA}/markets?condition_ids={ext_id}"):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    data = data[0] if data else None
                if data:
                    return data
        except Exception as e:
            log.debug(f"  fetch err {url}: {e}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually submit orders (default: dry-run)")
    ap.add_argument("--min-edge", type=float, default=3.0, help="minimum edge in pp to still execute")
    args = ap.parse_args()

    if not args.execute:
        log.info("=== DRY RUN — no orders will be submitted (pass --execute to send) ===")

    conn = get_conn()
    rows = fetch_open_paper_trades(conn)
    log.info(f"{len(rows)} open paper trades found")

    plan = []
    for r in rows:
        tid, strat, outcome, entry_p, model_p, ext_id, title, match_name = r
        if not ext_id:
            log.warning(f"#{tid} no external_id, skipping")
            continue
        pm = fetch_pm_market(ext_id)
        if not pm:
            log.warning(f"#{tid} could not fetch PM market {ext_id}")
            continue

        # Pull current yes_p + token ids
        try:
            prices = pm.get("outcomePrices")
            tokens = pm.get("clobTokenIds")
            if isinstance(prices, str):
                prices = json.loads(prices)
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if not prices or not tokens or len(prices) < 2 or len(tokens) < 2:
                log.warning(f"#{tid} bad market structure")
                continue
            yes_p = float(prices[0])
            no_p = round(1.0 - yes_p, 6)
            yes_tok, no_tok = str(tokens[0]), str(tokens[1])
        except Exception as e:
            log.warning(f"#{tid} parse err: {e}")
            continue

        # Edge recompute. Side depends on strategy:
        # DC Strategy 1 → BUY Yes at yes_p; edge = model_p - yes_p
        # No Bias       → BUY No at no_p;   edge = model_p - no_p  (model_p was stored as dc_no_prob)
        is_no_bias = strat == "No Bias (DC Model)"
        current_p = no_p if is_no_bias else yes_p
        token_id = no_tok if is_no_bias else yes_tok
        edge_pp = round((float(model_p) - current_p) * 100, 1)

        stake = live_executor.LIVE_STAKE_USD
        size = live_executor.shares_for_stake(stake, current_p)
        notional = round(size * current_p, 4)
        cap_ok = notional <= live_executor.MAX_NOTIONAL_PER_ORDER

        status = "✅"
        skip_reason = None
        if current_p < 0.01:
            status = "❌"
            skip_reason = f"price collapsed to {current_p:.4f} (market likely settled/very late)"
        elif edge_pp < args.min_edge:
            status = "❌"
            skip_reason = f"edge {edge_pp:.1f}pp < {args.min_edge}pp"
        elif not cap_ok:
            status = "❌"
            skip_reason = f"notional ${notional:.2f} > cap ${live_executor.MAX_NOTIONAL_PER_ORDER:.2f}"

        side = "BUY"  # always buy: Yes for DC, No for No-Bias
        log.info(
            f"{status} #{tid} [{strat[:18]:18s}] {match_name.strip()[:45]:45s} | "
            f"entry={float(entry_p):.4f} now={current_p:.4f} edge={edge_pp:+.1f}pp | "
            f"{side} {size:.2f}x = ${notional:.2f}"
            + (f"   ({skip_reason})" if skip_reason else "")
        )

        plan.append({
            "tid": tid, "token_id": token_id, "side": side, "price": current_p,
            "size": size, "notional": notional, "edge_pp": edge_pp,
            "skip_reason": skip_reason, "match": match_name.strip(),
        })

    placeable = [p for p in plan if not p["skip_reason"]]
    # Sort by edge desc — place highest-EV trades first so the daily cap doesn't
    # eat the best edges by starvation order.
    placeable.sort(key=lambda p: -p["edge_pp"])
    total_notional = sum(p["notional"] for p in placeable)
    log.info(f"\n== Summary: {len(placeable)} placeable / {len(plan)} total, total notional ${total_notional:.2f} ==")
    log.info(f"Per-order cap: ${live_executor.MAX_NOTIONAL_PER_ORDER:.2f}")

    if not args.execute:
        log.info("\nOrdered execution plan (best edges first):")
        for p in placeable:
            log.info(f"  #{p['tid']} {p['match'][:50]:50s} edge={p['edge_pp']:+.1f}pp  ${p['notional']:.2f}")
        log.info("\n(dry-run) Pass --execute to submit.")
        return

    # Execute
    placed = 0
    for p in placeable:
        log.info(f"  ⏳ #{p['tid']} {p['match']}…")
        r = live_executor.try_execute(
            conn,
            trade_id=p["tid"],
            token_id=p["token_id"],
            side=p["side"],
            price=p["price"],
        )
        log.info(f"     status={r.pm_order_status} order={r.pm_order_id} err={r.pm_order_error}")
        if r.pm_order_id:
            placed += 1
    log.info(f"\n✅ {placed} placed / {len(placeable)} attempted")


if __name__ == "__main__":
    main()
