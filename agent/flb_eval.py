"""
flb_eval.py — out-of-sample scorer for H-FLB (research_hypotheses id=25).

Resolves the 'FLB Pre-Match' paper book against real Polymarket settlement and
reports a GATE VERDICT rather than a yield in isolation — a yield without a
match-clustered CI is how this project has fooled itself before.

Match-clustered bootstrap, because one match throws up to 3 correlated
positions (O/U 0.5, 1.5, BTTS all resolve off the same goals). Treating those
as independent would understate the CI by roughly sqrt(3).

    python flb_eval.py             # gate verdict
    python flb_eval.py --verbose   # list settled positions
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys

import numpy as np
import psycopg2
import requests

DATABASE_URL = os.getenv("DATABASE_URL")
CLOB = "https://clob.polymarket.com"
STRATEGY_NAME = "FLB Pre-Match"

MIN_POSITIONS = 200          # the project's 200-selection rule
COST_HAIRCUT = 0.02          # frozen: discovery died at this haircut, so it is the bar

_cache: dict[str, dict | None] = {}


def _resolved(condition_id: str, token_id: str) -> float | None:
    """1.0 if this token won, 0.0 if it lost, None if not yet resolved.

    CLOB /markets/<condition_id>, never Gamma — Gamma's `clob_token_ids` and
    `condition_ids` filters return 0 rows while `token_id`/`conditionId` are
    silently IGNORED and hand back the default 20 markets, which would resolve
    positions against unrelated markets.
    """
    if not condition_id:
        return None
    if condition_id not in _cache:
        winners = None
        try:
            r = requests.get(f"{CLOB}/markets/{condition_id}", timeout=15)
            if r.status_code == 200:
                d = r.json()
                toks = d.get("tokens") or []
                # `closed` alone is not enough: a market can close before UMA
                # writes the winner, which would score every leg as a loss.
                if d.get("closed") and any(t.get("winner") for t in toks):
                    winners = {t.get("token_id"): (1.0 if t.get("winner") else 0.0)
                               for t in toks}
        except Exception:
            winners = None
        _cache[condition_id] = winners
    w = _cache[condition_id]
    return None if w is None else w.get(token_id)


def _bootstrap(by_match: dict, haircut: float, n_boot: int = 4000):
    keys = list(by_match)
    if not keys:
        return None

    def y(sample):
        stake = sum(min(c + haircut, 0.99) for k in sample for c, _ in by_match[k])
        prof = sum(p - (c + haircut) for k in sample for c, p in by_match[k])
        return 100 * prof / stake if stake else 0.0

    rng = np.random.default_rng(11)
    b = np.array([y(list(rng.choice(keys, len(keys), replace=True))) for _ in range(n_boot)])
    return y(keys), np.percentile(b, 2.5), np.percentile(b, 97.5), (b > 0).mean()


def main() -> None:
    ap = argparse.ArgumentParser(description="Score H-FLB out of sample")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="persist settlement back to paper_trades (for the site)")
    args = ap.parse_args()

    if not DATABASE_URL:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.pm_token_id, t.entry_price, t.stake_units, t.reasoning, t.result
            FROM paper_trades t JOIN strategies s ON s.id = t.strategy_id
            WHERE s.name = %s AND t.pm_token_id IS NOT NULL
        """, (STRATEGY_NAME,))
        rows = cur.fetchall()

        print(f"{STRATEGY_NAME}: {len(rows)} paper positions")

        by_match: dict[str, list] = collections.defaultdict(list)
        settled = pending = written = 0
        for tid, token_id, entry, stake, reasoning, existing in rows:
            cond = (re.search(r"cond=(\S+)", reasoning or "") or [None, None])[1]
            match = (reasoning or "").split("|")[1].strip() if "|" in (reasoning or "") else "?"
            final = _resolved(cond, token_id)
            if final is None:
                pending += 1
                continue
            settled += 1
            cost = float(entry)
            by_match[match].append((cost, final))

            if args.write and existing is None:
                # payout_units is GROSS by project convention: lost = 0,
                # won = stake x odds. A NET-style -1 here is the recurring bug.
                won = final >= 0.5
                payout = (float(stake) / cost) if won else 0.0
                cur.execute("""UPDATE paper_trades
                               SET result = %s, payout_units = %s, resolved_at = now()
                               WHERE id = %s""",
                            ("won" if won else "lost", payout, tid))
                written += 1
            if args.verbose:
                print(f"  {'WON ' if final else 'LOST'} @ {cost:.3f} ({1/cost:.2f} dec)  {match[:46]}")

        if args.write:
            conn.commit()
            print(f"persisted {written} settlements to paper_trades")

    n = sum(len(v) for v in by_match.values())
    print(f"settled: {settled}   pending: {pending}   matches: {len(by_match)}")
    if not n:
        print("\nNothing settled yet — let flb_scanner.py accumulate.")
        return

    raw = _bootstrap(by_match, 0.0)
    hc = _bootstrap(by_match, COST_HAIRCUT)
    print(f"\nraw           yield {raw[0]:+6.1f}%   CI [{raw[1]:+.1f}, {raw[2]:+.1f}]   P(>0)={raw[3]:.2f}")
    print(f"+2pp haircut  yield {hc[0]:+6.1f}%   CI [{hc[1]:+.1f}, {hc[2]:+.1f}]   P(>0)={hc[3]:.2f}")

    ok_n, ok_ci = n >= MIN_POSITIONS, hc[1] > 0
    print(f"\nGATE  positions >= {MIN_POSITIONS}: {'PASS' if ok_n else f'FAIL ({n})'}"
          f"   |   haircut CI lower > 0: {'PASS' if ok_ci else f'FAIL ({hc[1]:+.1f})'}")
    print("VERDICT:", "gate met — a micro-stake live test is justified" if (ok_n and ok_ci)
          else "KEEP PAPER — gate not met")


if __name__ == "__main__":
    main()
