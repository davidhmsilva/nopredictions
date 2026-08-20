"""
favourite_ht_table.py — empirical fair value for "the pre-match favourite is
ahead at half time", from a goalless scoreline.

P(favourite leads at HT | 0-0 at `minute`, favourite's pre-match win probability,
favourite playing home or away), estimated from goal_events (Understat, for the
state) and the half-time score in `matches` (for the outcome), bucketed on
vig-free Pinnacle 1X2.

WHY THIS TABLE EXISTS
---------------------
The market being traded is PM's "<Team> leading at halftime?", and there is no
history of its price anywhere — `match_odds` has never carried a half-time 1X2
line from any book. So the only defensible fair value is the frequency itself,
measured on the same universe convention as the other two pressure tables.

WHAT IT SAYS
------------
Both dimensions carry real information from a goalless 15th minute:

    0-0 at 15', favourite home, P(win) < 0.45   ->  23.5%   (4.26)
    0-0 at 15', favourite home, P(win) >= 0.65  ->  45.3%   (2.21)

Venue matters far less than strength — an away favourite of the same price is
within a couple of points of a home one — but it is kept because it is free and
because "favourite" is a different animal on the road.

n ~ 270-2,500 per cell. Minute and bucket are both monotone.

⚠️ A fair value is not an edge. Nothing here says PM misprices this market; it
says what the frequency has been. The agent records the price against it.

USAGE
-----
    cd agent && source ../ingest/.venv/bin/activate
    python favourite_ht_table.py                 # rebuild favourite_ht_table.json
    python favourite_ht_table.py --show          # print the table, no write
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [favourite_ht_table] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("favourite_ht_table")

TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "favourite_ht_table.json")

MINUTES = list(range(10, 45))

# Buckets on the favourite's vig-free win probability. The first one is a
# "favourite" only in the arithmetic sense — under 45% in a three-way market is
# a coin flip with a draw attached — and the agent's own gate sits above it. It
# is kept so the observation rows have somewhere to land.
BUCKETS = [("lt45", 0.0, 0.45), ("p45_55", 0.45, 0.55),
           ("p55_65", 0.55, 0.65), ("p65plus", 0.65, 1.0)]

MIN_CELL_N = 200

QUERY = """
WITH cov AS (
    SELECT DISTINCT m.season_id
    FROM matches m JOIN goal_events ge ON ge.match_id = m.id
),
u AS (
    SELECT m.id,
           m.home_score + m.away_score AS tot,
           m.home_score_ht            AS hh,
           m.away_score_ht            AS ah,
           (SELECT count(*) FROM goal_events ge WHERE ge.match_id = m.id) AS ge_n
    FROM matches m
    WHERE m.season_id IN (SELECT season_id FROM cov)
      AND m.status = 'finished'
      AND m.home_score IS NOT NULL
      AND m.home_score_ht IS NOT NULL
),
clean AS (SELECT id, hh, ah FROM u WHERE ge_n = tot),
o AS (
    SELECT mo.match_id,
           (1 / mo.home_odds) / ((1 / mo.home_odds) + (1 / mo.draw_odds)
                                 + (1 / mo.away_odds)) AS ph,
           (1 / mo.away_odds) / ((1 / mo.home_odds) + (1 / mo.draw_odds)
                                 + (1 / mo.away_odds)) AS pa
    FROM match_odds mo
    JOIN bookmakers b ON b.id = mo.bookmaker_id
    WHERE mo.home_odds IS NOT NULL
      AND mo.draw_odds IS NOT NULL
      AND mo.away_odds IS NOT NULL
      AND b.name = 'Pinnacle (legacy)'
),
f AS (
    SELECT c.id, c.hh, c.ah,
           (o.ph >= o.pa)        AS fav_home,
           greatest(o.ph, o.pa)  AS pfav
    FROM clean c JOIN o ON o.match_id = c.id
),
t AS (SELECT unnest(%(minutes)s::int[]) AS tm),
g AS (
    SELECT f.*, t.tm,
           (SELECT count(*) FROM goal_events ge
             WHERE ge.match_id = f.id AND ge.minute <= t.tm) AS gs
    FROM f CROSS JOIN t
)
SELECT tm,
       fav_home,
       CASE WHEN pfav < %(b1)s THEN 'lt45'
            WHEN pfav < %(b2)s THEN 'p45_55'
            WHEN pfav < %(b3)s THEN 'p55_65'
            ELSE 'p65plus' END AS bucket,
       count(*) AS n,
       avg((CASE WHEN fav_home THEN hh > ah ELSE ah > hh END)::int)::float8 AS p
FROM g
WHERE gs = 0
GROUP BY 1, 2, 3
"""

# Pooled over strength: the fallback for a thin cell, and the only cell available
# when the pre-match price was never captured before kickoff.
QUERY_ALL = QUERY.replace(
    """CASE WHEN pfav < %(b1)s THEN 'lt45'
            WHEN pfav < %(b2)s THEN 'p45_55'
            WHEN pfav < %(b3)s THEN 'p55_65'
            ELSE 'p65plus' END AS bucket""",
    "'all'::text AS bucket",
)


def bucket_of(p_fav: float | None) -> str:
    if p_fav is None:
        return "all"
    for name, lo, hi in BUCKETS:
        if lo <= p_fav < hi:
            return name
    return "p65plus"


def build(conn, minutes: list[int] | None = None) -> dict:
    minutes = MINUTES if minutes is None else minutes
    params = {"minutes": minutes, "b1": BUCKETS[0][2],
              "b2": BUCKETS[1][2], "b3": BUCKETS[2][2]}
    cells: dict[str, dict] = {}
    with conn.cursor() as cur:
        for q in (QUERY, QUERY_ALL):
            cur.execute(q, params)
            for tm, fav_home, bucket, n, p in cur.fetchall():
                key = f"{tm}|{'H' if fav_home else 'A'}|{bucket}"
                cells[key] = {"n": int(n), "p": round(float(p), 4)}

    thin = sum(1 for k, v in cells.items() if not k.endswith("|all") and v["n"] < MIN_CELL_N)
    log.info(f"{len(cells)} cells built ({thin} below MIN_CELL_N={MIN_CELL_N}, will fall back)")

    return {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "goal_events (understat) for the state x matches.*_score_ht for the "
                  "outcome x Pinnacle 1X2 (legacy/opening) for the favourite — clean "
                  "universe only",
        "state": "0-0 at `minute`",
        "outcome": "favourite ahead at half time",
        "minutes": minutes,
        "buckets": [{"name": b[0], "lo": b[1], "hi": b[2]} for b in BUCKETS],
        "min_cell_n": MIN_CELL_N,
        "cells": cells,
    }


def lookup(table: dict, minute: int, fav_home: bool,
           p_fav: float | None) -> tuple[float | None, int]:
    """Fair P(favourite leads at HT) from 0-0, and the cell support behind it.

    Falls back from the strength bucket to the pooled cell, never across venue:
    a home favourite and an away one are different populations, and the venue is
    always known while the price sometimes is not.
    """
    grid = table["minutes"]
    tm = min(grid, key=lambda m: abs(m - minute))
    if abs(tm - minute) > 2:
        return None, 0

    venue = "H" if fav_home else "A"
    for key in (f"{tm}|{venue}|{bucket_of(p_fav)}", f"{tm}|{venue}|all"):
        cell = table["cells"].get(key)
        if cell and cell["n"] >= table.get("min_cell_n", MIN_CELL_N):
            return cell["p"], cell["n"]
    return None, 0


def load(path: str = TABLE_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


def _show(table: dict) -> None:
    print(f"\nbuilt_at: {table['built_at']}")
    print("P(favourite leads at HT | 0-0 at minute), as decimal odds\n")
    names = [b["name"] for b in table["buckets"]] + ["all"]
    hdr = f"{'min':>4} {'fav':>4} " + "".join("{:>17}".format(n) for n in names)
    print(hdr)
    print("-" * len(hdr))
    for venue in ("H", "A"):
        for tm in table["minutes"][::5]:
            row = f"{tm:>4} {venue:>4} "
            for name in names:
                c = table["cells"].get(f"{tm}|{venue}|{name}")
                cell = "-" if not c or not c["p"] else f"{1 / c['p']:.2f} n={c['n']}"
                row += "{:>17}".format(cell)
            print(row)
        print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build the favourite-leads-at-half-time fair-value table")
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
