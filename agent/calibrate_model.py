"""
calibrate_model.py — fit a probability-calibration layer for the DC model.

The live P&L audit showed the model is systematically OVER-CONFIDENT: in the
0.4–0.6 band it predicts ~0.50 but the outcomes land ~0.25–0.35, and it is worst
on internationals (it hands ~52% to the home side of almost every national-team
match regardless of the gap). That manufactures fake edges. This fits an
isotonic recalibration map  raw_prob → empirical_frequency  per outcome
(home_win / draw / away_win) and per segment (club vs international), so the
scanners can shrink the model's probabilities toward what actually happens
before computing any edge.

Method: for every historical match where both teams are in the model, take the
DC 1X2 probabilities and the realised result; fit a monotone (isotonic) map of
predicted→observed per (segment, outcome). The international over-confidence is
a STRUCTURAL bias (52% given to teams that win ~42%), so it shows up even in the
training matches — isotonic captures it.

Output: agent/dc_calibration.json  (knots + the set of international teams, so
the calibrator can auto-detect a match's segment at apply time).

Usage:
  cd agent && source ../ingest/.venv/bin/activate
  python calibrate_model.py            # fit + write + print reliability
  python calibrate_model.py --dry-run  # fit + print, no write
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np
import psycopg2
from dotenv import load_dotenv
from scipy.optimize import isotonic_regression

from dixon_coles import DixonColesModel
from dc_scanner import _norm, PARAMS_PATH

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

OUT_PATH = os.path.join(os.path.dirname(__file__), "dc_calibration.json")
OUTCOMES = ["home_win", "draw", "away_win"]
MIN_SEG_N = 500       # don't fit a segment/outcome with fewer points than this
KNOTS = 400           # thinned (x,y) knots stored per map


def _fetch_matches(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ht.canonical_name, at.canonical_name, m.home_score, m.away_score,
               CASE WHEN l.code LIKE 'INT%%' THEN 'intl' ELSE 'club' END AS seg
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        JOIN seasons s ON s.id = m.season_id
        JOIN leagues l ON l.id = s.league_id
        WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
        """
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def _fit_isotonic(preds: np.ndarray, hits: np.ndarray) -> dict:
    """Monotone map predicted→observed. Returns thinned knots {x, y, n}."""
    order = np.argsort(preds)
    xs = preds[order]
    ys = hits[order]
    fit = isotonic_regression(ys).x          # non-decreasing fit in x order
    # Thin to KNOTS evenly-spaced indices (keep first + last).
    n = len(xs)
    if n > KNOTS:
        idx = np.linspace(0, n - 1, KNOTS).round().astype(int)
        xs, fit = xs[idx], fit[idx]
    return {"x": [round(v, 5) for v in xs.tolist()],
            "y": [round(v, 5) for v in fit.tolist()], "n": int(n)}


def _reliability(preds: np.ndarray, hits: np.ndarray, apply_fn=None) -> str:
    """One-line reliability table over 5 buckets (pred vs actual)."""
    out = []
    p = preds if apply_fn is None else np.array([apply_fn(v) for v in preds])
    for lo, hi in [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)]:
        m = (p >= lo) & (p < hi)
        if m.sum() == 0:
            continue
        out.append(f"{lo:.1f}-{hi:.1f}: n={m.sum():>5} pred {p[m].mean():.2f} "
                   f"act {hits[m].mean():.2f} ({hits[m].mean()-p[m].mean():+.2f})")
    return "  |  ".join(out)


def build(dry_run: bool = False) -> dict:
    model = DixonColesModel.load(PARAMS_PATH)
    norm_idx = {_norm(t): t for t in model.teams}

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    rows = _fetch_matches(conn)
    conn.close()

    # segment -> outcome -> (preds[], hits[]); plus the set of intl teams.
    data = {s: {o: ([], []) for o in OUTCOMES} for s in ("club", "intl")}
    intl_teams: set[str] = set()
    n_used = n_skip = 0
    for h, a, hs, as_, seg in rows:
        hn = norm_idx.get(_norm(h))
        an = norm_idx.get(_norm(a))
        if not hn or not an:
            n_skip += 1
            continue
        try:
            p = model.predict(hn, an)
        except Exception:
            n_skip += 1
            continue
        actual = "home_win" if hs > as_ else ("away_win" if as_ > hs else "draw")
        for o in OUTCOMES:
            data[seg][o][0].append(p[o])
            data[seg][o][1].append(1.0 if actual == o else 0.0)
        if seg == "intl":
            intl_teams.add(hn)
            intl_teams.add(an)
        n_used += 1

    calib = {"created": datetime.now(timezone.utc).isoformat(),
             "n_matches": n_used, "segments": {}, "intl_teams": sorted(intl_teams)}

    print(f"\ncalibration set: {n_used} matches used, {n_skip} skipped (team not in model)\n")
    for seg in ("club", "intl"):
        calib["segments"][seg] = {}
        for o in OUTCOMES:
            preds = np.array(data[seg][o][0])
            hits = np.array(data[seg][o][1])
            if len(preds) < MIN_SEG_N:
                print(f"[{seg:4}] {o:9} n={len(preds)} < {MIN_SEG_N} — SKIP (model-only)")
                continue
            knots = _fit_isotonic(preds, hits)
            calib["segments"][seg][o] = knots
            # reliability before vs after (in-sample sanity)
            fn = _make_apply(knots)
            print(f"[{seg:4}] {o:9} n={len(preds)}")
            print(f"        raw : {_reliability(preds, hits)}")
            print(f"        cal : {_reliability(preds, hits, fn)}")

    if not dry_run:
        with open(OUT_PATH, "w") as f:
            json.dump(calib, f)
        print(f"\nwrote {OUT_PATH}")
    else:
        print("\n[dry-run] not written")
    return calib


def _make_apply(knots: dict):
    x = np.array(knots["x"])
    y = np.array(knots["y"])
    return lambda v: float(np.interp(v, x, y))


def main():
    ap = argparse.ArgumentParser(description="Fit DC probability calibration")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    build(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
