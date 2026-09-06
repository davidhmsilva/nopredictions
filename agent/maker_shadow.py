"""
maker_shadow.py — would posting at the bid have beaten crossing to the ask?

Zero-risk reconstruction from `market_observations` (which already stores pm_bid /
pm_ask alongside our model prob and the refined engine's verdict) joined to
`pm_resolutions` for the actual outcome.

The question this answers is ADVERSE SELECTION, not spread arithmetic. Saving the
spread is trivially good *if you fill at random*. You don't: a resting bid is only
hit when someone wants to sell to you, which skews toward the moments our fair
value is about to fall. The 2026-07-21 HT-over-0.5 test found exactly this shape —
a constant resting bid bought elapsed time, not value (median fill minute 16 for a
min-10 quote, ROI -0.3%).

Fill model (deliberately conservative-but-generous, stated so it can be argued with):
  we post a BUY at `post_price` at t0 and treat it as filled at the first later
  observation whose ASK has fallen to <= post_price, i.e. the market traded down
  through our level. We cannot see trades, only quotes, so this over-states fills
  when the book merely flickers and under-states them when a seller crosses
  between two 10/30-minute snapshots. Both arms are priced off the same series.

Usage:  python maker_shadow.py [--phase pre|live|all] [--offset-ticks N]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge_engine  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingest", ".env"))

TICK = 0.01


def fetch(conn, phase: str):
    q = """
        SELECT o.pm_token_id, o.observed_at, o.model_prob, o.pm_bid, o.pm_ask,
               o.refined_bet_ok, o.phase, o.question, r.resolved
          FROM market_observations o
          JOIN pm_resolutions r ON r.pm_external_id = o.pm_external_id
         WHERE o.pm_bid IS NOT NULL AND o.pm_ask IS NOT NULL
           AND o.pm_ask > 0 AND o.pm_bid > 0 AND o.pm_ask > o.pm_bid
           AND r.resolved IN (0, 1)
    """
    if phase in ("pre", "live"):
        q += " AND o.phase = %s"
    q += " ORDER BY o.pm_token_id, o.observed_at"
    cur = conn.cursor()
    cur.execute(q, (phase,) if phase in ("pre", "live") else None)
    series = defaultdict(list)
    for tok, ts, mp, bid, ask, ok, ph, qn, res in cur.fetchall():
        series[tok].append(dict(ts=ts, mp=float(mp) if mp is not None else None,
                                bid=float(bid), ask=float(ask), ok=bool(ok),
                                phase=ph, q=qn, res=int(res)))
    return series


def ret(price: float, won: int) -> float:
    """Per-unit-staked return of a BUY at `price`, net of the taker fee when we cross."""
    return (1.0 / price - 1.0) if won else -1.0


def stats(xs):
    n = len(xs)
    if n == 0:
        return 0, 0.0, 0.0, 0.0
    m = sum(xs) / n
    if n < 2:
        return n, m, 0.0, 0.0
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    se = sd / math.sqrt(n)
    return n, m, m - 1.96 * se, m + 1.96 * se


def run(phase: str, offset_ticks: int):
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    series = fetch(conn, phase)

    taker, maker, unfilled = [], [], 0
    drift, waits, spreads = [], [], []
    fills_won = filled = 0
    won_filled, won_unfilled = [], []      # outcome-based adverse-selection test

    for tok, rows in series.items():
        # One entry per token: the first moment the refined engine said BET.
        i0 = next((i for i, r in enumerate(rows) if r["ok"]), None)
        if i0 is None:
            continue
        r0 = rows[i0]
        won = r0["res"]

        # --- taker arm: cross to the ask now, pay the fee.
        ask = r0["ask"]
        fee = edge_engine.taker_fee_pp(ask) / 100.0
        taker.append(ret(ask + fee, won))
        spreads.append((ask - r0["bid"]) * 100.0)

        # --- maker arm: rest a bid, no fee, fill only if the market comes to us.
        post = round(r0["bid"] + offset_ticks * TICK, 4)
        if post >= ask:            # our quote would cross => we'd be the taker again
            post = round(ask - TICK, 4)
        if post <= 0:
            continue

        hit = next((r for r in rows[i0 + 1:] if r["ask"] <= post), None)
        if hit is None:
            unfilled += 1
            won_unfilled.append(won)
            continue
        filled += 1
        fills_won += won
        won_filled.append(won)
        maker.append(ret(post, won))
        waits.append((hit["ts"] - r0["ts"]).total_seconds() / 60.0)
        if hit["mp"] is not None and r0["mp"] is not None:
            drift.append((hit["mp"] - r0["mp"]) * 100.0)

    opps = filled + unfilled
    print(f"phase={phase}  post at bid+{offset_ticks} tick(s)   opportunities={opps}")
    if not opps:
        print("  no data")
        return

    print(f"  avg quoted spread {sum(spreads)/len(spreads):5.2f}pp   "
          f"fill rate {filled/opps:.1%} ({filled} filled / {unfilled} never filled)")
    if waits:
        waits.sort()
        print(f"  time to fill: median {waits[len(waits)//2]:6.1f} min   "
              f"p90 {waits[int(0.9*len(waits))]:7.1f} min")
    if drift:
        n, m, lo, hi = stats(drift)
        print(f"  adverse selection (model view): fair moved {m:+.2f}pp between quote "
              f"and fill CI[{lo:+.2f},{hi:+.2f}]")
        print("    ^ weak evidence pre-match: our fair value is near-static before "
              "kickoff, so it cannot detect what it does not react to.")

    # The test that does not depend on the model reacting: did the tickets the
    # market chose to sell us win less often than the ones it left alone?
    if won_filled and won_unfilled:
        wf, wu = sum(won_filled) / len(won_filled), sum(won_unfilled) / len(won_unfilled)
        sef = math.sqrt(wf * (1 - wf) / len(won_filled))
        seu = math.sqrt(wu * (1 - wu) / len(won_unfilled))
        print(f"  ADVERSE SELECTION (outcomes): filled win {wf:.1%} "
              f"CI[{wf-1.96*sef:.1%},{wf+1.96*sef:.1%}]  vs  "
              f"never-filled win {wu:.1%} CI[{wu-1.96*seu:.1%},{wu+1.96*seu:.1%}]"
              f"   gap {(wf-wu)*100:+.1f}pp")

    for lbl, xs in (("TAKER (cross the ask, pay fee)", taker),
                    ("MAKER (rest at bid, no fee)  ", maker)):
        n, m, lo, hi = stats(xs)
        print(f"  {lbl}  n={n:5d}  yield {m*100:+7.2f}%  CI[{lo*100:+7.1f},{hi*100:+7.1f}]")

    # Per-opportunity comparison: an unfilled maker quote stakes nothing.
    if taker:
        t_total = sum(taker) / len(taker)
        m_total = sum(maker) / opps if opps else 0.0
        print(f"  per OPPORTUNITY (unfilled maker = 0 staked):  "
              f"taker {t_total*100:+.2f}%   maker {m_total*100:+.2f}%")
        print(f"  win rate when filled: {fills_won/filled:.1%}" if filled else "")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["pre", "live", "all"])
    ap.add_argument("--offset-ticks", type=int, default=0)
    a = ap.parse_args()
    run(a.phase, a.offset_ticks)
