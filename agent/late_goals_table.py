"""
late_goals_table.py — build the empirical late-goal fair-value table.

P(at least one more goal | minute, goals so far, pre-match total bucket),
estimated directly from goal_events (Understat, minute-level).

WHY EMPIRICAL AND NOT THE SIM
-----------------------------
The MC sim puts 49.5% of goals in the first half against 44.1% in reality, so it
systematically underprices late overs — exactly the quantity this strategy trades.
Using it here would manufacture edge out of a known model bias. The table below
has no model in it at all: it is a conditional frequency over 16,479 matches.

CLEAN UNIVERSE
--------------
Only matches where count(goal_events) == home_score + away_score. 3,358 matches
in the covered league-seasons have partial goal coverage; including them silently
biases every late-goal rate downward.

WHAT THE TABLE SAYS
-------------------
Conditional on the goals already scored, the pre-match total still carries real
information at minute 75 — it does not wash out:

    1 goal at 75',  P(over 2.5) pre-match < 0.475  ->  44.9%   (2.23)
    1 goal at 75',  P(over 2.5) pre-match >= 0.575 ->  53.0%   (1.89)

n ~ 1,000-1,900 per cell, monotone in both minute and bucket.

USAGE
-----
    cd agent && source ../ingest/.venv/bin/activate
    python late_goals_table.py                 # rebuild late_goals_table.json
    python late_goals_table.py --show          # print the table, no write
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [late_goals_table] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("late_goals_table")

TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "late_goals_table.json")
WIDE_TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "pressure_baseline.json")

# Minute grid. Below 68' the over line is rarely cheap enough to matter and the
# state is still volatile; past 88' there is no time for the book to reprice.
MINUTES = list(range(68, 90, 2))
# The pressure agent acts on in-game pressure rather than on the clock, so it
# needs the same baseline from the point stats become meaningful (~20') onward.
WIDE_MINUTES = list(range(20, 90, 2))
# The strategy only ever acts on 1-2 goals, but the observer prices every state
# it sees. Leaving 4+ off the grid writes NULL fair values into rows we may want
# later, and the cells are well populated anyway.
GOALS = [0, 1, 2, 3, 4, 5]

# Pre-match bucket edges on vig-free Pinnacle P(over 2.5). Three buckets, not
# ten: the effect is monotone and roughly linear, and thin buckets are how you
# talk yourself into noise.
BUCKETS = [("lo", 0.0, 0.475), ("mid", 0.475, 0.575), ("hi", 0.575, 1.0)]

MIN_CELL_N = 200        # below this the cell falls back to the all-bucket rate


QUERY = """
WITH cov AS (
    SELECT DISTINCT m.season_id
    FROM matches m JOIN goal_events ge ON ge.match_id = m.id
),
u AS (
    SELECT m.id,
           m.home_score + m.away_score AS tot,
           (SELECT count(*) FROM goal_events ge WHERE ge.match_id = m.id) AS ge_n
    FROM matches m
    WHERE m.season_id IN (SELECT season_id FROM cov)
      AND m.status = 'finished'
      AND m.home_score IS NOT NULL
),
clean AS (SELECT id FROM u WHERE ge_n = tot),
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
    SELECT c.id, o.p_over, t.tm,
           (SELECT count(*) FROM goal_events ge
             WHERE ge.match_id = c.id AND ge.minute <= t.tm) AS gs,
           (SELECT count(*) FROM goal_events ge
             WHERE ge.match_id = c.id AND ge.minute >  t.tm) AS gaft
    FROM clean c
    JOIN o ON o.match_id = c.id
    CROSS JOIN t
)
SELECT tm,
       gs,
       CASE WHEN p_over < %(lo_hi)s THEN 'lo'
            WHEN p_over < %(mid_hi)s THEN 'mid'
            ELSE 'hi' END AS bucket,
       count(*)                        AS n,
       avg((gaft >= 1)::int)::float8   AS p1,
       avg((gaft >= 2)::int)::float8   AS p2
FROM g
WHERE gs = ANY(%(goals)s::int[])
GROUP BY 1, 2, 3
"""

# Same thing collapsed over the pre-match bucket — the fallback for thin cells
# and for fixtures where no pre-match total was captured.
QUERY_ALL = QUERY.replace(
    """CASE WHEN p_over < %(lo_hi)s THEN 'lo'
            WHEN p_over < %(mid_hi)s THEN 'mid'
            ELSE 'hi' END AS bucket""",
    "'all'::text AS bucket",
)


def bucket_of(p_over: float | None) -> str:
    """Pre-match bucket for a vig-free P(over 2.5). None -> the pooled cell."""
    if p_over is None:
        return "all"
    for name, lo, hi in BUCKETS:
        if lo <= p_over < hi:
            return name
    return "hi"


def build(conn, minutes: list[int] | None = None) -> dict:
    """Fair-value cells over `minutes` (default: the late-goals grid).

    The grid is a parameter because the pressure agent prices states from the
    first half onward and needs the same empirical baseline over a wider window.
    Nothing else about the cell definition changes, so both tables stay directly
    comparable.
    """
    minutes = MINUTES if minutes is None else minutes
    params = {
        "minutes": minutes,
        "goals": GOALS,
        "lo_hi": BUCKETS[0][2],
        "mid_hi": BUCKETS[1][2],
    }
    cells: dict[str, dict] = {}
    with conn.cursor() as cur:
        for q in (QUERY, QUERY_ALL):
            cur.execute(q, params)
            for tm, gs, bucket, n, p1, p2 in cur.fetchall():
                # p1 prices the one-more-goal line, p2 the leveraged one. Both
                # are needed to ask whether PM's error compounds across rungs —
                # the question that decides which line the strategy should buy.
                cells[f"{tm}|{gs}|{bucket}"] = {
                    "n": int(n),
                    "p": round(float(p1), 4),      # kept as `p`: P(>=1 more)
                    "p2": round(float(p2), 4),     # P(>=2 more)
                }

    thin = sum(1 for k, v in cells.items() if not k.endswith("|all") and v["n"] < MIN_CELL_N)
    log.info(f"{len(cells)} cells built ({thin} below MIN_CELL_N={MIN_CELL_N}, will fall back)")

    return {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "goal_events (understat) x match_odds (pinnacle) — clean universe only",
        "minutes": minutes,
        "goals": GOALS,
        "buckets": [{"name": b[0], "lo": b[1], "hi": b[2]} for b in BUCKETS],
        "min_cell_n": MIN_CELL_N,
        "cells": cells,
    }


def lookup(table: dict, minute: int, goals: int, p_over: float | None,
           needed: int = 1) -> tuple[float | None, int]:
    """Fair P(>= `needed` more goals) and the cell support behind it.

    Snaps the minute to the grid, then falls back from the bucketed cell to the
    pooled one when support is thin. Returns (None, 0) when the state is off the
    grid entirely — the caller must record that rather than guess.
    """
    if needed not in (1, 2):
        return None, 0

    grid = table["minutes"]
    tm = min(grid, key=lambda m: abs(m - minute))
    if abs(tm - minute) > 3:
        return None, 0

    field = "p" if needed == 1 else "p2"
    for key in (f"{tm}|{goals}|{bucket_of(p_over)}", f"{tm}|{goals}|all"):
        cell = table["cells"].get(key)
        if cell and cell["n"] >= table.get("min_cell_n", MIN_CELL_N) and field in cell:
            return cell[field], cell["n"]
    return None, 0


def implied_lambda(p1: float) -> float | None:
    """Remaining goal rate implied by P(>=1 more goal), assuming Poisson.

    The point of carrying both p and p2 is that a Poisson process ties them
    together: P(>=2) = 1 - e^-L (1+L) for the same L. If PM's quotes on two rungs
    do NOT resolve to one lambda, its error is per-line rather than in the rate —
    and that flips which line the strategy should buy.
    """
    if p1 is None or not (0 < p1 < 1):
        return None
    return -math.log(1 - p1)


def poisson_p2(lam: float) -> float:
    """P(>=2 more goals) for a Poisson rate, for comparison against empirical p2."""
    return 1 - math.exp(-lam) * (1 + lam)


def load(path: str = TABLE_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


def _show(table: dict) -> None:
    print(f"\nbuilt_at: {table['built_at']}")
    print("cells show P(>=1 more) / P(>=2 more), as decimal odds\n")
    hdr = f"{'min':>4} {'goals':>6} " + "".join("{:>22}".format(b["name"]) for b in table["buckets"])
    print(hdr)
    print("-" * len(hdr))
    for gs in table["goals"]:
        for tm in table["minutes"]:
            row = f"{tm:>4} {gs:>6} "
            for b in table["buckets"]:
                c = table["cells"].get(f"{tm}|{gs}|" + b["name"])
                if not c:
                    cell = "-"
                else:
                    o1 = "{:.2f}".format(1 / c["p"]) if c["p"] else "-"
                    o2 = "{:.1f}".format(1 / c["p2"]) if c.get("p2") else "-"
                    cell = f"{o1} / {o2}  n={c['n']}"
                row += "{:>22}".format(cell)
            print(row)
        print()


def _calibration(table: dict) -> None:
    """How far the empirical P(>=2) sits from what a Poisson rate would imply.

    Football is mildly overdispersed relative to Poisson, so a gap is expected —
    what matters is that it is stable, otherwise the two-goal line cannot be
    priced by extrapolating from the one-goal line.
    """
    print("Poisson check — empirical P(>=2) vs the value implied by P(>=1):\n")
    print(f"{'min':>4} {'goals':>6} {'emp p1':>8} {'emp p2':>8} {'poisson p2':>11} {'ratio':>7}")
    print("-" * 50)
    for gs in (1, 2):
        for tm in table["minutes"][::3]:
            c = table["cells"].get(f"{tm}|{gs}|all")
            if not c or not c.get("p2"):
                continue
            lam = implied_lambda(c["p"])
            pp2 = poisson_p2(lam)
            print(f"{tm:>4} {gs:>6} {c['p']:>8.3f} {c['p2']:>8.3f} {pp2:>11.3f} "
                  f"{c['p2']/pp2:>7.2f}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the empirical late-goal fair-value table")
    ap.add_argument("--show", action="store_true", help="print the table without writing it")
    ap.add_argument("--calibration", action="store_true",
                    help="empirical P(>=2) vs the Poisson value implied by P(>=1)")
    ap.add_argument("--wide", action="store_true",
                    help=f"build the pressure-agent grid ({WIDE_MINUTES[0]}'-{WIDE_MINUTES[-1]}') "
                         f"into {os.path.basename(WIDE_TABLE_PATH)} instead")
    args = ap.parse_args()

    if args.calibration:
        _calibration(load())
        return

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")

    out_path = WIDE_TABLE_PATH if args.wide else TABLE_PATH
    conn = psycopg2.connect(url)
    try:
        table = build(conn, minutes=WIDE_MINUTES if args.wide else None)
    finally:
        conn.close()

    if args.show:
        _show(table)
        return

    with open(out_path, "w") as fh:
        json.dump(table, fh, indent=1)
    log.info(f"wrote {out_path}")
    _show(table)


if __name__ == "__main__":
    main()
