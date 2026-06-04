#!/usr/bin/env python3
"""
World Cup model backtest — Phase 0 gate.

Honest walk-forward calibration of our national-team Elo line against the
Dixon-Coles baseline on past internationals. We have international RESULTS but
NO historical odds, so this measures *predictive calibration* (Brier / log-loss
/ reliability), not betting yield. Betting edge is only measurable live.

  - Ratings update online (each prediction uses only pre-match info).
  - The Elo->goals calibration (beta, baseline total) is fit on the TRAIN
    period only, then frozen for the TEST period — no lookahead.
  - Elo and DC are scored on the SAME matched set (both must know both teams)
    so the head-to-head is paired and fair.

Also reports model-free base rates (draw / favorite / over 2.5) with Wilson
95% CIs and n, honouring the project's sample-sufficiency rule.

  cd agent && source ../ingest/.venv/bin/activate
  python wc_backtest.py                      # default split 2021-06-01
  python wc_backtest.py --split 2022-01-01 --min-games 10
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

import national_elo as ne  # noqa: E402
from wc_pricer import analytical_markets  # noqa: E402

DC_PARAMS = Path(__file__).parent / "dc_model_params.json"
EPS = 1e-15


# ── metrics ───────────────────────────────────────────────────────────

def brier_1x2(p: Tuple[float, float, float], outcome: str) -> float:
    y = {"H": (1, 0, 0), "D": (0, 1, 0), "A": (0, 0, 1)}[outcome]
    return sum((p[i] - y[i]) ** 2 for i in range(3))


def logloss(p_actual: float) -> float:
    return -math.log(min(max(p_actual, EPS), 1.0))


def brier_bin(p: float, y: int) -> float:
    return (p - y) ** 2


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half, centre + half)


def outcome_1x2(hs: int, as_: int) -> str:
    return "H" if hs > as_ else ("A" if hs < as_ else "D")


# ── backtest ──────────────────────────────────────────────────────────

def run(split: datetime, min_games: int, home_adv: float) -> int:
    from dixon_coles import DixonColesModel

    matches = ne.load_international_matches()
    print(f"Loaded {len(matches)} international matches.")
    if not matches:
        print("No matches — is Stage G ingested?")
        return 1

    dc = DixonColesModel.load(str(DC_PARAMS)) if DC_PARAMS.exists() else None
    if dc is None:
        print("Warning: no DC params — running Elo-only (no baseline).")

    ratings: Dict[str, float] = {}
    games: Dict[str, int] = {}
    train_samples: List[dict] = []
    calib: Optional[dict] = None

    # accumulators (paired set: both models must be able to predict)
    n_eval = 0
    sums = {
        "elo_brier": 0.0, "elo_ll": 0.0,
        "dc_brier": 0.0, "dc_ll": 0.0,
        "elo_ou_brier": 0.0, "elo_ou_ll": 0.0,
        "dc_ou_brier": 0.0, "dc_ou_ll": 0.0,
    }
    # over-2.5 reliability bins for the Elo model
    rel_bins = [[0, 0] for _ in range(10)]   # [sum_pred, n] -> compare to empirical
    rel_emp = [0 for _ in range(10)]
    # model-free base rates over the test set
    base = {"n": 0, "draw": 0, "fav_win": 0, "over25": 0, "fav_known": 0}

    for m in matches:
        ko = m["kickoff_utc"]
        home, away = m["home"], m["away"]
        hs, as_ = int(m["home_score"]), int(m["away_score"])
        code = m["league_code"]

        in_test = ko >= split
        pre_min = min(games.get(home, 0), games.get(away, 0))

        # Freeze the goals calibration at the train/test boundary.
        if in_test and calib is None:
            calib = ne.fit_calibration(train_samples)
            print(f"Calibration frozen on {len(train_samples)} train samples: "
                  f"beta={calib['beta_goals_per_elo']:.5f}, "
                  f"mu_finals={calib['mu_total_finals']:.3f}")

        # Score BEFORE updating (pre-match info only).
        if in_test and calib is not None and pre_min >= min_games:
            rh = ratings.get(home, ne.DEFAULT_RATING)
            ra = ratings.get(away, ne.DEFAULT_RATING)
            adv = 0.0 if ne.is_neutral(code) else home_adv
            beta, mu = calib["beta_goals_per_elo"], calib["mu_total_finals"]
            sup = beta * ((rh + adv) - ra)
            lh = min(max((mu + sup) / 2.0, 0.15), 5.0)
            la = min(max((mu - sup) / 2.0, 0.15), 5.0)
            em = analytical_markets(lh, la)

            dcp = dc.predict_or_none(home, away) if dc else None

            outc = outcome_1x2(hs, as_)
            over = 1 if (hs + as_) > 2 else 0

            # model-free base rates (independent of DC availability)
            base["n"] += 1
            if outc == "D":
                base["draw"] += 1
            base["over25"] += over
            if abs(rh - ra) > 1e-9:
                base["fav_known"] += 1
                fav_home = rh >= ra
                if (fav_home and outc == "H") or ((not fav_home) and outc == "A"):
                    base["fav_win"] += 1

            # over-2.5 reliability (Elo)
            b = min(int(em["over_2_5"] * 10), 9)
            rel_bins[b][0] += em["over_2_5"]
            rel_bins[b][1] += 1
            rel_emp[b] += over

            # paired head-to-head only where DC can also predict
            if dcp is not None:
                n_eval += 1
                ep = (em["home_win"], em["draw"], em["away_win"])
                dp = (dcp.get("home_win", 0.0), dcp.get("draw", 0.0),
                      dcp.get("away_win", 0.0))
                # normalise DC trio defensively
                ssum = sum(dp) or 1.0
                dp = tuple(x / ssum for x in dp)

                idx = {"H": 0, "D": 1, "A": 2}[outc]
                sums["elo_brier"] += brier_1x2(ep, outc)
                sums["dc_brier"] += brier_1x2(dp, outc)
                sums["elo_ll"] += logloss(ep[idx])
                sums["dc_ll"] += logloss(dp[idx])

                eo, do = em["over_2_5"], dcp.get("over_2_5", 0.5)
                sums["elo_ou_brier"] += brier_bin(eo, over)
                sums["dc_ou_brier"] += brier_bin(do, over)
                sums["elo_ou_ll"] += logloss(eo if over else 1 - eo)
                sums["dc_ou_ll"] += logloss(do if over else 1 - do)

        # advance the ratings + collect train calibration samples
        ctx = ne.match_update(ratings, games, home, away, hs, as_, code,
                              home_adv=home_adv)
        if (not in_test) and ctx["pre_games_min"] >= 3:
            train_samples.append(ctx)

    # ── report ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f" HEAD-TO-HEAD  (test = matches on/after {split.date()},"
          f" both teams ≥{min_games} games, DC knows both)")
    print("=" * 60)
    if n_eval == 0:
        print("  No eligible paired matches — loosen --min-games or --split.")
    else:
        print(f"  Paired matches: n = {n_eval}\n")
        print(f"  {'Metric':<22}{'Elo':>10}{'DC':>10}{'winner':>10}")
        print("  " + "-" * 52)

        def line(label, e, d, lower_better=True):
            win = "Elo" if (e < d) == lower_better else "DC"
            if abs(e - d) < 1e-9:
                win = "tie"
            print(f"  {label:<22}{e:>10.4f}{d:>10.4f}{win:>10}")

        line("1X2 Brier", sums["elo_brier"] / n_eval, sums["dc_brier"] / n_eval)
        line("1X2 log-loss", sums["elo_ll"] / n_eval, sums["dc_ll"] / n_eval)
        line("O/U2.5 Brier", sums["elo_ou_brier"] / n_eval, sums["dc_ou_brier"] / n_eval)
        line("O/U2.5 log-loss", sums["elo_ou_ll"] / n_eval, sums["dc_ou_ll"] / n_eval)

        elo_better = (sums["elo_brier"] < sums["dc_brier"]) and \
                     (sums["elo_ll"] < sums["dc_ll"])
        verdict = ("✅ Elo line beats DC on internationals (1X2)."
                   if elo_better else
                   "⚠️  Elo does NOT clearly beat DC on 1X2 — review before trusting.")
        print(f"\n  Gate: {verdict}")

    # over-2.5 reliability (Elo)
    print("\n  Elo O/U2.5 reliability (pred bin → empirical over-rate):")
    print(f"  {'bin':<10}{'pred':>8}{'emp':>8}{'n':>8}")
    print("  " + "-" * 34)
    for i in range(10):
        n = rel_bins[i][1]
        if n == 0:
            continue
        pred = rel_bins[i][0] / n
        emp = rel_emp[i] / n
        print(f"  {i/10:.1f}-{(i+1)/10:.1f}  {pred:>8.3f}{emp:>8.3f}{n:>8}")

    # model-free base rates (with CIs + sample-sufficiency note)
    print("\n" + "=" * 60)
    print(f" MODEL-FREE BASE RATES  (test set, n = {base['n']})")
    print("=" * 60)

    def rate(label, k, n):
        if n == 0:
            print(f"  {label:<22} n=0")
            return
        lo, hi = wilson(k, n)
        flag = "" if n >= 200 else "  ⚠️ n<200"
        print(f"  {label:<22}{k/n*100:6.2f}%   95% CI [{lo*100:5.2f},"
              f" {hi*100:5.2f}]   n={n}{flag}")

    rate("Draw rate", base["draw"], base["n"])
    rate("Higher-Elo team wins", base["fav_win"], base["fav_known"])
    rate("Over 2.5 goals", base["over25"], base["n"])
    print("  (base rates inform the structural priors in wc_structural.py;\n"
          "   any sliced claim still needs its own n≥200 + CI.)\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="WC model calibration backtest")
    ap.add_argument("--split", default="2021-06-01",
                    help="train/test boundary (YYYY-MM-DD)")
    ap.add_argument("--min-games", type=int, default=8,
                    help="min prior games per team to evaluate a match")
    ap.add_argument("--home-adv", type=float, default=ne.DEFAULT_HOME_ADV)
    args = ap.parse_args()

    split = datetime.strptime(args.split, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return run(split, args.min_games, args.home_adv)


if __name__ == "__main__":
    raise SystemExit(main())
