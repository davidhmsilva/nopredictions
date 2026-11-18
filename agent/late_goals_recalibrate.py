"""
late_goals_recalibrate.py — correct the late-goal table for the matches the
Game Center actually shows.

The table (late_goals_table.py) is a frequency over Understat's Big-5 + RFPL
history. On the fixtures Polymarket lists, it runs LOW: measured on
pressure_observations, the real rate of "one more goal" beat the table by
+2.1pp and "two more goals" by +3.4pp out of sample, in every minute band.
So every Under the Game Center called cheap, and every Over it called rich,
was partly the table.

The correction is a logit shift, fitted on PM-listed fixtures only:

    logit(p') = logit(p_table) + a + b * (minute - 55) / 35

one (a, b) per rung (needed = 1, 2). A free slope on logit(p_table) was tried
and came out at 0.96-0.98 with no gain, so it is left out.

Data: one row per fixture per even minute (19'-89'), the final from
api-football only (never the poll tape, see db/038), live goals as recorded.
Validated walk-forward: fitted on the oldest 60% of fixtures, scored on the
rest. The shipped coefficients are refitted on everything.

The output is keyed to the table's `built_at`. The site applies it only when
the two match, so rebuilding the table without refitting this drops the
correction instead of applying it to numbers it was never fitted on.

USAGE
    cd agent && source ../ingest/.venv/bin/activate
    python late_goals_recalibrate.py --dry-run   # fit + report, nothing written
    python late_goals_recalibrate.py             # writes site/app/lib/late_goals_calibration.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from dotenv import load_dotenv

import db_txn

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(HERE, "../ingest/.env"))

TABLE = os.path.join(HERE, "../site/app/lib/late_goals.json")
OUT = os.path.join(HERE, "../site/app/lib/late_goals_calibration.json")

MIN_MINUTE, MAX_MINUTE = 19, 89
CENTRE, SCALE = 55.0, 35.0
TRAIN_SHARE = 0.6
BOOTSTRAP = 200


def _bucket(table: dict, p):
    if p is None:
        return "all"
    for b in table["buckets"]:
        if b["lo"] <= p < b["hi"]:
            return b["name"]
    return "hi"


def table_rate(table: dict, minute: int, goals: int, p_over25, needed: int):
    """The site's lookup (gamecenter.lateGoalRate), before any correction."""
    tm = min(table["minutes"], key=lambda x: abs(x - minute))
    if abs(tm - minute) > 3:
        return None
    field = "p" if needed == 1 else "p2"
    for b in (_bucket(table, p_over25), "all"):
        cell = table["cells"].get(f"{tm}|{goals}|{b}")
        if cell and cell["n"] >= table["min_cell_n"]:
            return cell[field]
    return None


def load_rows(table: dict) -> dict[int, list[tuple]]:
    """(fixture, first_seen, minute, p_table, outcome) per rung."""
    url = os.environ["DATABASE_URL"].replace(":5432/", ":6543/")
    conn = db_txn.connect(url)
    cur = conn.cursor()
    cur.execute("set statement_timeout = '300s'")
    cur.execute(
        """
        select fixture_id, minute, goals_total, pre_over25, final_goals,
               min(observed_at) over (partition by fixture_id)
          from pressure_observations
         where token_id is not null
           and final_goals is not null
           and final_goals_source <> 'poll'
           and goals_total is not null
           and final_goals >= goals_total
           and minute between %s and %s
        """,
        (MIN_MINUTE, MAX_MINUTE),
    )
    seen = set()
    out: dict[int, list[tuple]] = {1: [], 2: []}
    for fx, minute, goals, p25, final, first in cur.fetchall():
        key = (fx, minute - minute % 2)
        if key in seen:
            continue
        seen.add(key)
        for needed in (1, 2):
            p = table_rate(table, minute, goals, p25, needed)
            if p is None or not 0 < p < 1:
                continue
            out[needed].append((fx, first, minute, p, 1 if final - goals >= needed else 0))
    conn.close()
    return out


def _design(rows):
    m = np.array([r[2] for r in rows], float)
    p = np.array([r[3] for r in rows], float)
    return np.column_stack([np.ones(len(rows)), (m - CENTRE) / SCALE]), np.log(p / (1 - p))


def fit(rows) -> np.ndarray:
    X, off = _design(rows)
    y = np.array([r[4] for r in rows], float)
    w = np.zeros(2)
    for _ in range(50):
        q = 1 / (1 + np.exp(-(X @ w + off)))
        step = np.linalg.solve((X * (q * (1 - q))[:, None]).T @ X, X.T @ (y - q))
        w += step
        if np.abs(step).max() < 1e-10:
            break
    return w


def predict(w, rows) -> np.ndarray:
    X, off = _design(rows)
    return 1 / (1 + np.exp(-(X @ w + off)))


def log_loss(p, y) -> float:
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def bootstrap_ci(rows, seed=1):
    """Fixture-clustered CI on (a, b)."""
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    fxs = list(by)
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [r for f in (rng.choice(fxs) for _ in fxs) for r in by[f]]
        draws.append(fit(sample))
    draws = np.array(draws)
    return np.percentile(draws, 2.5, axis=0), np.percentile(draws, 97.5, axis=0)


def evaluate(rows) -> dict:
    """Walk-forward: oldest TRAIN_SHARE of fixtures fit, the rest score."""
    first = {r[0]: r[1] for r in rows}
    order = sorted(first, key=first.get)
    train_fx = set(order[: int(len(order) * TRAIN_SHARE)])
    train = [r for r in rows if r[0] in train_fx]
    test = [r for r in rows if r[0] not in train_fx]
    w = fit(train)
    y = np.array([r[4] for r in test], float)
    base = np.array([r[3] for r in test])
    cal = predict(w, test)
    bands = {}
    for lo, hi in ((19, 39), (40, 59), (60, 74), (75, 89)):
        s = np.array([lo <= r[2] <= hi for r in test])
        bands[f"{lo}-{hi}"] = {
            "table_bias_pp": round(100 * float((y[s] - base[s]).mean()), 2),
            "corrected_bias_pp": round(100 * float((y[s] - cal[s]).mean()), 2),
        }
    return {
        "train_fixtures": len(train_fx),
        "test_fixtures": len(order) - len(train_fx),
        "test_rows": len(test),
        "table_bias_pp": round(100 * float((y - base).mean()), 2),
        "corrected_bias_pp": round(100 * float((y - cal).mean()), 2),
        "table_log_loss": round(log_loss(base, y), 5),
        "corrected_log_loss": round(log_loss(cal, y), 5),
        "by_minute": bands,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(TABLE) as fh:
        table = json.load(fh)
    data = load_rows(table)

    out = {
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "table_built_at": table["built_at"],
        "form": "logit(p') = logit(p_table) + a + b * (minute - 55) / 35",
        "population": "Polymarket-listed fixtures in pressure_observations, api-football finals",
        "rungs": {},
    }
    for needed in (1, 2):
        rows = data[needed]
        w = fit(rows)
        lo, hi = bootstrap_ci(rows)
        ev = evaluate(rows)
        out["rungs"][str(needed)] = {
            "a": round(float(w[0]), 4),
            "b": round(float(w[1]), 4),
            "a_ci": [round(float(lo[0]), 4), round(float(hi[0]), 4)],
            "b_ci": [round(float(lo[1]), 4), round(float(hi[1]), 4)],
            "fixtures": len({r[0] for r in rows}),
            "rows": len(rows),
            "walk_forward": ev,
        }
        print(f"needed={needed}: a={w[0]:+.4f} CI[{lo[0]:+.4f},{hi[0]:+.4f}]  "
              f"b={w[1]:+.4f} CI[{lo[1]:+.4f},{hi[1]:+.4f}]  "
              f"fixtures={out['rungs'][str(needed)]['fixtures']}")
        print(f"   out of sample: bias {ev['table_bias_pp']:+.2f}pp -> {ev['corrected_bias_pp']:+.2f}pp, "
              f"log loss {ev['table_log_loss']} -> {ev['corrected_log_loss']}")
        for band, v in ev["by_minute"].items():
            print(f"     {band}: {v['table_bias_pp']:+.2f} -> {v['corrected_bias_pp']:+.2f}pp")

    if args.dry_run:
        return
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
