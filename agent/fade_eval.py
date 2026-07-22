"""
fade_eval.py — out-of-sample scorer for H-FADE-LIVE-FAV (research_hypotheses id=23).

THE HYPOTHESIS (frozen 2026-07-21, do not re-fit)
-------------------------------------------------
Polymarket in-play tokens priced 0.80-0.90 realise only ~0.783. So buy the NO
side at (1 - best_bid) and HOLD TO SETTLEMENT.

  entry     : in-play tick with best_bid in [0.80, 0.90], spread <= 6pp
  one bet   : first qualifying tick per (event_title, question)
  excluded  : over_2_5 (-25% in discovery), over_4_5 (n=5 noise)
  gate      : n >= 200 match-clustered positions AND match-clustered bootstrap
              95% CI lower bound > 0 AFTER a +2pp entry-cost haircut

Discovery was +16.1% on 114 matches with CI [-19.3%, +55.3%] — NOT significant.
This script exists to answer the question honestly as OOS data accumulates, and
it reports the gate verdict rather than a yield number in isolation.

Reads pm_ticks (written by tick_recorder.py) and resolves each market via the
CLOB API. Read-only: places no orders and writes no trades.

    python fade_eval.py              # score everything recorded so far
    python fade_eval.py --verbose    # list individual settled positions
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np
import psycopg2
import requests

DATABASE_URL = os.getenv("DATABASE_URL")
CLOB = "https://clob.polymarket.com"

BAND_LO, BAND_HI = 0.80, 0.90
MAX_SPREAD = 0.06
COST_HAIRCUT = 0.02          # frozen gate haircut
MIN_POSITIONS = 200
EXCLUDED = ("over_2_5", "over 2.5", "o/u 2.5", "over_4_5", "over 4.5", "o/u 4.5")


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _excluded(question: str) -> bool:
    q = (question or "").lower()
    return any(tok in q for tok in EXCLUDED)


def _entries() -> list[dict]:
    """First qualifying in-play tick per (event, question).

    'In-play' = observed at or after the market's end_date minus 3h. PM end_date
    is a kickoff proxy, not an exact kickoff, so this is deliberately loose; the
    band filter does the real work (a token only reaches 0.80-0.90 once the
    match state has moved).
    """
    sql = """
        SELECT DISTINCT ON (event_title, question)
               event_title, question, token_id, condition_id, outcome, observed_at,
               best_bid, best_ask, bid_depth_usd
        FROM pm_ticks
        WHERE best_bid BETWEEN %s AND %s
          AND best_ask > best_bid
          AND (best_ask - best_bid) <= %s
          AND end_date IS NOT NULL
          AND observed_at >= end_date - interval '3 hours'
        ORDER BY event_title, question, observed_at
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (BAND_LO, BAND_HI, MAX_SPREAD))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return [r for r in rows if not _excluded(r["question"])]


_resolution_cache: dict[str, float | None] = {}


def _resolved_price(condition_id: str, token_id: str) -> float | None:
    """Final price of this token (1.0 won / 0.0 lost), or None if unresolved.

    Uses CLOB /markets/<condition_id>, NOT Gamma. Gamma has no working
    token/condition filter: `clob_token_ids` and `condition_ids` return 0 rows,
    while `token_id`/`conditionId` silently IGNORE the filter and hand back the
    default 20 markets — which would resolve positions against the wrong market.
    """
    if not condition_id:
        return None
    if condition_id in _resolution_cache:
        cached = _resolution_cache[condition_id]
        return None if cached is None else cached.get(token_id)

    winners: dict[str, float] | None = None
    try:
        resp = requests.get(f"{CLOB}/markets/{condition_id}", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get("tokens") or []
            # `closed` alone is not enough — a market can be closed before UMA
            # writes the winner, which would score every leg as a loss.
            if data.get("closed") and any(t.get("winner") for t in tokens):
                winners = {t.get("token_id"): (1.0 if t.get("winner") else 0.0)
                           for t in tokens}
    except Exception:
        winners = None

    _resolution_cache[condition_id] = winners
    return None if winners is None else winners.get(token_id)


def _bootstrap(by_match: dict, haircut: float, n_boot: int = 4000) -> tuple:
    keys = list(by_match)
    if not keys:
        return 0.0, 0.0, 0.0, 0.0

    def yield_pct(sample):
        stake = sum(min(c + haircut, 1.0) for k in sample for c, _ in by_match[k])
        prof = sum(p - haircut for k in sample for _, p in by_match[k])
        return 100 * prof / stake if stake else 0.0

    point = yield_pct(keys)
    rng = np.random.default_rng(7)
    boots = np.array([yield_pct(list(rng.choice(keys, len(keys), replace=True)))
                      for _ in range(n_boot)])
    return point, float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)), float((boots > 0).mean())


def main() -> None:
    ap = argparse.ArgumentParser(description="Score H-FADE-LIVE-FAV out of sample")
    ap.add_argument("--verbose", action="store_true", help="list settled positions")
    args = ap.parse_args()

    if not DATABASE_URL:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    entries = _entries()
    print(f"qualifying entries in pm_ticks: {len(entries)}")

    by_match: dict[str, list] = defaultdict(list)
    settled = unresolved = 0
    for e in entries:
        final = _resolved_price(e["condition_id"], e["token_id"])
        if final is None:
            unresolved += 1
            continue
        settled += 1
        cost = 1.0 - float(e["best_bid"])          # NO costs 1 - best YES bid
        no_won = final < 0.5                        # YES lost => our NO won
        pnl = (1.0 - cost) if no_won else -cost
        by_match[e["event_title"]].append((cost, pnl))
        if args.verbose:
            print(f"  {'NO WON ' if no_won else 'NO LOST'} cost={cost:.3f} "
                  f"({1/cost:.2f} dec)  {e['question'][:56]}")

    n_pos = sum(len(v) for v in by_match.values())
    print(f"settled: {settled}   awaiting resolution: {unresolved}   matches: {len(by_match)}")
    if not n_pos:
        print("\nNo settled positions yet — let tick_recorder.py accumulate.")
        return

    raw, lo, hi, p_pos = _bootstrap(by_match, 0.0)
    hc, hlo, hhi, hp = _bootstrap(by_match, COST_HAIRCUT)
    avg_cost = np.mean([c for v in by_match.values() for c, _ in v])

    print(f"\navg NO cost {avg_cost:.3f}  ({1/avg_cost:.2f} decimal)")
    print(f"raw           yield {raw:+6.1f}%   CI [{lo:+.1f}, {hi:+.1f}]   P(>0)={p_pos:.2f}")
    print(f"+2pp haircut  yield {hc:+6.1f}%   CI [{hlo:+.1f}, {hhi:+.1f}]   P(>0)={hp:.2f}")

    ok_n, ok_ci = n_pos >= MIN_POSITIONS, hlo > 0
    print(f"\nGATE  positions >= {MIN_POSITIONS}: {'PASS' if ok_n else f'FAIL ({n_pos})'}"
          f"   |   haircut CI lower > 0: {'PASS' if ok_ci else f'FAIL ({hlo:+.1f})'}")
    print("VERDICT:", "PROMOTE to micro-stake live" if (ok_n and ok_ci)
          else "KEEP PAPER — gate not met, no real money")


if __name__ == "__main__":
    main()
