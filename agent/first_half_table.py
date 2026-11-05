"""
first_half_table.py — empirical fair value for "a goal before half time".

P(at least one goal in (minute, HT] | 0-0 at `minute`, pre-match total),
from ESPN's goal timelines (espn_matches / espn_match_events, Stage L) since
2022-07, with Pinnacle's closing Over/Under 2.5 as the pre-match total.

REBUILT 2026-11-05 — WHY THE OLD TABLE WAS REPLACED
---------------------------------------------------
The first table (2026-08-19) came from Understat: the Big 5 and Russia, 2014
onward. On 2,208 PM fixtures between 2026-10-10 and 11-05 it read 4-5pp below
what happened at every minute band (0.439 against 0.493 realised, Brier 0.2419),
for two measured reasons:

  - Era. First-half stoppage time has grown: the share of first-half goals
    scored in added time doubled, 5.4% in 2017 to 10.8% in 2026, and the rate
    from 0-0 rose 1-3pp, most in the late minutes (0-0 at 35': 0.262 -> 0.279,
    at 40': 0.162 -> 0.183, 2017-22 against 2022-26).
  - The bucket was almost never used. The pre-match total was read from PM's
    board only when seen before kick-off, and the agent first sees a fixture
    after it, so 48 of ~16,000 rows had one: every fair value was the pooled
    cell. The pre-match total is the biggest single input (0-0 at 15': 0.52 in
    a low-total game, 0.66 in a high one), so ht_pressure_agent now also reads
    PM's last pre-KO quote from venue_market_quotes (db/071).

On the same 2,208 fixtures this table reads 0.476 (Brier 0.2334), the same as
PM's own in-play mid (0.476, Brier 0.2325). Out of sample on ESPN 2025 (fit on
2022-07..2024) it is within 1pp of the realised rate at every minute.

⚠️ A FAIR VALUE IS NOT AN EDGE
------------------------------
Where this table and PM's mid disagree, the realised rate does not follow the
table: the slope of (realised - mid) on (table - mid) is -0.08 +/- 0.72 on the
2,208. PM already prices the minute and the pre-match total. The table is the
measuring stick that says whether a price is rich; it is not a reason to buy.

HOW THE TABLE IS BUILT
----------------------
  - State: still 0-0 after `minute` (a goal recorded AT that minute counts as
    already scored, as in the first table). Outcome: the half-time score > 0,
    which is settlement truth and includes 45+n goals.
  - Clean universe: completed, timeline_ok, no extra time, the timeline's goal
    count equal to the final score AND its first-half count equal to the
    half-time score.
  - `model`: per minute, a logistic curve logit(P) = a + b * logit(p_over), fit
    on the matches with a Pinnacle close. Pinnacle only: a market average
    carries a different margin and shifts the scale (mixing them put a 2026
    holdout 2pp off). PM's pre-KO mid sits on Pinnacle's scale near the close.
  - `cells`: the same states in the three late_goals_table buckets plus the
    pooled 'all' cell over every clean match, with or without odds. 'all' is
    what a fixture with no pre-match total gets; the buckets are kept for
    --show and for comparison with the late-goals table.

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
import math
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

# Every minute of the first half the agent could plausibly look at.
MINUTES = list(range(10, 45))

# The current stoppage-time era. Earlier seasons read 1-3pp low from 0-0.
ERA_START = "2022-07-01"

# Pinnacle only, see the docstring.
TOTAL_SOURCE = "Pinnacle (closing)"

BUCKETS = lgt.BUCKETS
MIN_CELL_N = lgt.MIN_CELL_N

# p_over is clipped before the logit, at fit and at lookup alike: a 0.99 total
# is a data error, not a match.
P_CLIP = (0.05, 0.95)

# One row per clean match: when the first first-half goal came, whether there
# was one, and Pinnacle's close. The minute states are counted in Python; a
# cross join of every match with every minute timed out on the database.
QUERY = """
WITH g AS (
    SELECT e.event_id,
           count(*) FILTER (WHERE e.period BETWEEN 1 AND 2) AS ng,
           count(*) FILTER (WHERE e.period = 1)             AS ng1,
           min(e.minute) FILTER (WHERE e.period = 1)        AS fg1
    FROM espn_match_events e
    WHERE e.kind IN ('goal', 'own_goal', 'penalty_goal')
    GROUP BY 1
),
clean AS (
    SELECT em.match_id, g.fg1,
           em.home_score_ht + em.away_score_ht AS ht
    FROM espn_matches em
    LEFT JOIN g USING (event_id)
    WHERE em.completed AND em.timeline_ok
      AND em.kickoff_utc >= %(era)s AND em.kickoff_utc < now()
      AND em.home_score IS NOT NULL AND em.home_score_ht IS NOT NULL
      AND NOT coalesce(em.went_to_et, false)
      AND coalesce(g.ng, 0)  = em.home_score + em.away_score
      AND coalesce(g.ng1, 0) = em.home_score_ht + em.away_score_ht
)
SELECT c.fg1, c.ht > 0 AS scored,
       (SELECT (1 / mo.over_2_5_odds)
                 / ((1 / mo.over_2_5_odds) + (1 / mo.under_2_5_odds))
          FROM match_odds mo
          JOIN bookmakers b ON b.id = mo.bookmaker_id
         WHERE mo.match_id = c.match_id AND b.name = %(book)s
           AND mo.over_2_5_odds > 1 AND mo.under_2_5_odds > 1
         ORDER BY mo.id DESC LIMIT 1)::float8 AS p_over
FROM clean c
"""


def _logit(p: float, clip: tuple[float, float] = P_CLIP) -> float:
    p = min(max(p, clip[0]), clip[1])
    return math.log(p / (1.0 - p))


def fit_logistic(groups: list[tuple[float, int, int]], iters: int = 50) -> tuple[float, float]:
    """(a, b) of logit(P) = a + b*x by Newton's method on grouped binomial data
    [(x, n, k)]. Two parameters, so the 2x2 solve is written out: the build
    needs no numpy."""
    a, b = 0.0, 0.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, n, k in groups:
            p = 1.0 / (1.0 + math.exp(-(a + b * x)))
            r = k - n * p
            w = n * p * (1.0 - p)
            ga += r
            gb += r * x
            haa += w
            hab += w * x
            hbb += w * x * x
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a + da, b + db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return a, b


def build(conn, minutes: list[int] | None = None) -> dict:
    minutes = sorted(MINUTES if minutes is None else minutes)
    with conn.cursor() as cur:
        cur.execute(QUERY, {"era": ERA_START, "book": TOTAL_SOURCE})
        matches = cur.fetchall()

    # (minute, p_over to the cent) -> [n, k]; p_over None counts in 'all' only.
    counts: dict[tuple[int, float | None], list[int]] = {}
    for fg1, scored, p_over in matches:
        p = round(p_over, 2) if p_over is not None else None
        for tm in minutes:
            if fg1 is not None and fg1 <= tm:
                break                       # minutes ascend: scored by now
            s = counts.setdefault((tm, p), [0, 0])
            s[0] += 1
            s[1] += int(bool(scored))

    acc: dict[str, list[int]] = {}
    groups: dict[int, list[tuple[float, int, int]]] = {}
    for (tm, p_over), (n, k) in counts.items():
        keys = [f"{tm}|all"]
        if p_over is not None:
            keys.append(f"{tm}|{lgt.bucket_of(p_over)}")
            groups.setdefault(tm, []).append((_logit(p_over), n, k))
        for key in keys:
            s = acc.setdefault(key, [0, 0])
            s[0] += n
            s[1] += k
    cells = {key: {"n": n, "p": round(k / n, 4)} for key, (n, k) in acc.items()}

    model: dict[str, dict] = {}
    for tm in minutes:
        g = groups.get(tm) or []
        n = sum(x[1] for x in g)
        if n < MIN_CELL_N:
            continue
        a, b = fit_logistic(g)
        model[str(tm)] = {"a": round(a, 5), "b": round(b, 5), "n": n}

    thin = sum(1 for k, v in cells.items() if not k.endswith("|all") and v["n"] < MIN_CELL_N)
    log.info(f"{len(matches)} clean matches, {len(cells)} cells, {len(model)} minute curves "
             f"({thin} cells below MIN_CELL_N={MIN_CELL_N})")

    return {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": f"espn_match_events (Stage L) for the state x the half-time score for "
                  f"the outcome x match_odds '{TOTAL_SOURCE}' O/U 2.5 for the total; "
                  f"clean universe, kick-off >= {ERA_START}",
        "state": "0-0 at `minute`",
        "outcome": "half-time score > 0",
        "era_start": ERA_START,
        "minutes": minutes,
        "buckets": [{"name": b[0], "lo": b[1], "hi": b[2]} for b in BUCKETS],
        "min_cell_n": MIN_CELL_N,
        "p_clip": list(P_CLIP),
        "model": model,
        "cells": cells,
    }


def lookup(table: dict, minute: int, p_over: float | None) -> tuple[float | None, int]:
    """Fair P(goal before half time) from 0-0, and the support behind it.

    With a pre-match total: the minute's logistic curve. Without one: the pooled
    cell. (None, 0) when the minute is off the grid: the caller records that
    rather than guess, because a made-up fair value on this market would be
    indistinguishable from a real one in the observation table.
    """
    grid = table["minutes"]
    tm = min(grid, key=lambda m: abs(m - minute))
    if abs(tm - minute) > 2:
        return None, 0

    curve = (table.get("model") or {}).get(str(tm))
    if p_over is not None and curve:
        z = curve["a"] + curve["b"] * _logit(p_over, tuple(table.get("p_clip", P_CLIP)))
        return round(1.0 / (1.0 + math.exp(-z)), 4), curve["n"]

    # A table written before the curves existed still prices from its buckets.
    keys = [f"{tm}|all"] if curve else [f"{tm}|{lgt.bucket_of(p_over)}", f"{tm}|all"]
    for key in keys:
        cell = table["cells"].get(key)
        if cell and cell["n"] >= table.get("min_cell_n", MIN_CELL_N):
            return cell["p"], cell["n"]
    return None, 0


def load(path: str = TABLE_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


def _show(table: dict) -> None:
    print(f"\nbuilt_at: {table['built_at']}\n{table['source']}")
    print("P(goal before HT | 0-0 at minute), as decimal odds\n")
    names = [b["name"] for b in table["buckets"]] + ["all"]
    curve_at = (0.40, 0.50, 0.60)
    hdr = (f"{'min':>4} " + "".join("{:>16}".format(n) for n in names)
           + "".join("{:>10}".format(f"O2.5 {p:.2f}") for p in curve_at))
    print(hdr)
    print("-" * len(hdr))
    for tm in table["minutes"]:
        row = f"{tm:>4} "
        for name in names:
            c = table["cells"].get(f"{tm}|{name}")
            cell = "-" if not c or not c["p"] else f"{1 / c['p']:.2f} n={c['n']}"
            row += "{:>16}".format(cell)
        for p in curve_at:
            f, _ = lookup(table, tm, p)
            row += "{:>10}".format(f"{1 / f:.2f}" if f else "-")
        print(row)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the first-half goal fair-value table")
    ap.add_argument("--show", action="store_true", help="print the table without writing it")
    args = ap.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    # One long read: the transaction pooler, never the session pooler the
    # daemons hold (CLAUDE.md, "Where it runs").
    url = url.replace(":5432/", ":6543/")

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '300s'")
            table = build(conn)
    finally:
        conn.rollback()
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
