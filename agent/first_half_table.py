"""
first_half_table.py — empirical fair value for "a goal before half time".

P(at least one goal in (minute, HT] | 0-0 at `minute`, pre-match total bucket),
estimated from goal_events (Understat, minute-level) for the STATE and from the
half-time score in `matches` for the OUTCOME.

WHY TWO SOURCES AND NOT ONE
---------------------------
goal_events carries the minute, so it is the only thing that can say a match was
still 0-0 at minute 22. But it cannot be trusted for the outcome: Understat folds
first-half stoppage into the surrounding minutes, and on this universe its
"goals with minute <= 45" disagrees with the recorded half-time score on 1.4% of
matches — 575 of them missing a goal the half-time score has. A 45+2 winner is
exactly the goal this market pays on, so the outcome comes from
matches.home_score_ht + away_score_ht, which is settlement truth.

CLEAN UNIVERSE
--------------
Same rule as late_goals_table: only matches where count(goal_events) equals the
final score. Partial coverage biases every rate down hard — on the uncleaned
universe the 10' base rate reads 0.478 against 0.608 clean, because a match whose
goals were never recorded looks like it stayed 0-0.

WHAT THE TABLE SAYS
-------------------
The pre-match total survives into the 0-0 state, the same way it survives into
the 75' state on the late-goals table — it does not wash out:

    0-0 at 15',  P(over 2.5) pre-match < 0.475  ->  49.6%   (2.02)
    0-0 at 15',  P(over 2.5) pre-match >= 0.575 ->  64.5%   (1.55)

n ~ 1,400-4,800 per cell, monotone in both minute and bucket.

⚠️ READ THIS BEFORE TRADING OFF IT
----------------------------------
A fair value is not an edge. PM's own price on this market sat ABOVE the realised
frequency at every minute tested from 5' to 40' (n=236 fixtures, ~4pp, CI crossing
zero) — i.e. the generic over is rich, and backing it blind is the losing side.
Anything buying this line has to beat that gap plus the fee plus the spread.

USAGE
-----
    cd agent && source ../ingest/.venv/bin/activate
    python first_half_table.py                 # rebuild first_half_table.json
    python first_half_table.py --show          # print the table, no write
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

import late_goals_table as lgt

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [first_half_table] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("first_half_table")

TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "first_half_table.json")

# Every minute of the first half the agent could plausibly look at. A finer grid
# costs nothing here: each minute is a separate pass over the same matches, so
# the support per cell does not thin out the way it would if we were splitting a
# fixed sample.
MINUTES = list(range(10, 45))

# Bucket edges and the thin-cell fallback are imported rather than restated, so
# this table and the late-goals one stay directly comparable.
BUCKETS = lgt.BUCKETS
MIN_CELL_N = lgt.MIN_CELL_N

QUERY = """
WITH cov AS (
    SELECT DISTINCT m.season_id
    FROM matches m JOIN goal_events ge ON ge.match_id = m.id
),
u AS (
    SELECT m.id,
           m.home_score + m.away_score       AS tot,
           m.home_score_ht + m.away_score_ht AS ht,
           (SELECT count(*) FROM goal_events ge WHERE ge.match_id = m.id) AS ge_n
    FROM matches m
    WHERE m.season_id IN (SELECT season_id FROM cov)
      AND m.status = 'finished'
      AND m.home_score IS NOT NULL
      AND m.home_score_ht IS NOT NULL
),
clean AS (SELECT id, ht FROM u WHERE ge_n = tot),
o AS (
    SELECT mo.match_id,
           (1 / mo.over_2_5_odds)
             / ((1 / mo.over_2_5_odds) + (1 / mo.under_2_5_odds)) AS p_over
    FROM match_odds mo
    JOIN bookmakers b ON b.id = mo.bookmaker_id
    WHERE mo.over_2_5_odds IS NOT NULL
      AND mo.under_2_5_odds IS NOT NULL
      AND b.name ILIKE 'pinnacle%%'
),
t AS (SELECT unnest(%(minutes)s::int[]) AS tm),
g AS (
    SELECT c.id, c.ht, o.p_over, t.tm,
           (SELECT count(*) FROM goal_events ge
             WHERE ge.match_id = c.id AND ge.minute <= t.tm) AS gs
    FROM clean c
    JOIN o ON o.match_id = c.id
    CROSS JOIN t
)
SELECT tm,
       CASE WHEN p_over < %(lo_hi)s THEN 'lo'
            WHEN p_over < %(mid_hi)s THEN 'mid'
            ELSE 'hi' END AS bucket,
       count(*)                     AS n,
       avg((ht > 0)::int)::float8   AS p
FROM g
WHERE gs = 0
GROUP BY 1, 2
"""

# Pooled over the pre-match bucket: the fallback for a thin cell, and the only
# cell available for a fixture whose pre-match total we never captured.
QUERY_ALL = QUERY.replace(
    """CASE WHEN p_over < %(lo_hi)s THEN 'lo'
            WHEN p_over < %(mid_hi)s THEN 'mid'
            ELSE 'hi' END AS bucket""",
    "'all'::text AS bucket",
)


def build(conn, minutes: list[int] | None = None) -> dict:
    minutes = MINUTES if minutes is None else minutes
    params = {
        "minutes": minutes,
        "lo_hi": BUCKETS[0][2],
        "mid_hi": BUCKETS[1][2],
    }
    cells: dict[str, dict] = {}
    with conn.cursor() as cur:
        for q in (QUERY, QUERY_ALL):
            cur.execute(q, params)
            for tm, bucket, n, p in cur.fetchall():
                cells[f"{tm}|{bucket}"] = {"n": int(n), "p": round(float(p), 4)}

    thin = sum(1 for k, v in cells.items() if not k.endswith("|all") and v["n"] < MIN_CELL_N)
    log.info(f"{len(cells)} cells built ({thin} below MIN_CELL_N={MIN_CELL_N}, will fall back)")

    return {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "goal_events (understat) for the state x matches.*_score_ht for "
                  "the outcome x match_odds (pinnacle) for the bucket — clean universe only",
        "state": "0-0 at `minute`",
        "outcome": "half-time score > 0",
        "minutes": minutes,
        "buckets": [{"name": b[0], "lo": b[1], "hi": b[2]} for b in BUCKETS],
        "min_cell_n": MIN_CELL_N,
        "cells": cells,
    }


def lookup(table: dict, minute: int, p_over: float | None) -> tuple[float | None, int]:
    """Fair P(goal before half time) from 0-0, and the cell support behind it.

    Falls back from the bucketed cell to the pooled one when support is thin,
    and returns (None, 0) when the minute is off the grid — the caller has to
    record that rather than guess, because a made-up fair value on this market
    would be indistinguishable from a real one in the observation table.
    """
    grid = table["minutes"]
    tm = min(grid, key=lambda m: abs(m - minute))
    if abs(tm - minute) > 2:
        return None, 0

    for key in (f"{tm}|{lgt.bucket_of(p_over)}", f"{tm}|all"):
        cell = table["cells"].get(key)
        if cell and cell["n"] >= table.get("min_cell_n", MIN_CELL_N):
            return cell["p"], cell["n"]
    return None, 0


def load(path: str = TABLE_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


def _show(table: dict) -> None:
    print(f"\nbuilt_at: {table['built_at']}")
    print("P(goal before HT | 0-0 at minute), as decimal odds\n")
    names = [b["name"] for b in table["buckets"]] + ["all"]
    hdr = f"{'min':>4} " + "".join("{:>18}".format(n) for n in names)
    print(hdr)
    print("-" * len(hdr))
    for tm in table["minutes"]:
        row = f"{tm:>4} "
        for name in names:
            c = table["cells"].get(f"{tm}|{name}")
            cell = "-" if not c or not c["p"] else f"{1 / c['p']:.2f}  n={c['n']}"
            row += "{:>18}".format(cell)
        print(row)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the first-half goal fair-value table")
    ap.add_argument("--show", action="store_true", help="print the table without writing it")
    args = ap.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")

    conn = psycopg2.connect(url)
    try:
        table = build(conn)
    finally:
        conn.close()

    if args.show:
        _show(table)
        return

    with open(TABLE_PATH, "w") as fh:
        json.dump(table, fh, indent=1)
    log.info(f"wrote {TABLE_PATH}")
    _show(table)


if __name__ == "__main__":
    main()
