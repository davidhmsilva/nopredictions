"""
Calibration harness for the Monte Carlo simulator.

Two checks:
  1. Pure sim (no adjustments, no stoppage) MUST converge to the closed-form
     independent Poisson distribution within Monte Carlo noise. This is the
     non-negotiable sanity check — if it fails, the sim is wrong.
  2. Adjusted sim is compared to DC for typical lambdas, with the deviations
     reported per market. Differences here are *expected and sensible* (the
     sim has score-state dynamics DC doesn't); we just want them to point in
     the right direction.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .pricer import price_markets
from .simulator import SimConfig, simulate


def _analytical_poisson(lh: float, la: float, max_goals: int = 12) -> Dict[str, float]:
    """Closed-form independent Poisson — the ground truth for the sanity check."""
    ph = np.exp(-lh) * np.array([lh ** k / math.factorial(k) for k in range(max_goals + 1)])
    pa = np.exp(-la) * np.array([la ** k / math.factorial(k) for k in range(max_goals + 1)])
    mat = np.outer(ph, pa)
    mat = mat / mat.sum()

    rows, cols = np.mgrid[0 : max_goals + 1, 0 : max_goals + 1]
    total = rows + cols
    return {
        "home_win": float(mat[rows > cols].sum()),
        "draw": float(np.trace(mat)),
        "away_win": float(mat[rows < cols].sum()),
        "over_1_5": float(mat[total > 1].sum()),
        "over_2_5": float(mat[total > 2].sum()),
        "over_3_5": float(mat[total > 3].sum()),
        "btts": float(mat[(rows >= 1) & (cols >= 1)].sum()),
        "exp_total_goals": float(lh + la),
    }


DEFAULT_PAIRS: List[Tuple[float, float]] = [
    (1.6, 1.0),   # typical home favourite
    (1.2, 1.2),   # even
    (0.8, 1.5),   # away favourite
    (2.0, 0.7),   # strong home
    (1.0, 1.0),   # low-scoring
    (2.5, 2.0),   # high-scoring
]


def calibrate_pure_sim(
    lambda_pairs: Optional[List[Tuple[float, float]]] = None,
    n_sims: int = 100_000,
    tol_pp: float = 1.0,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Verify the simulator (with adjustments disabled and zero stoppage)
    converges to closed-form independent Poisson within ±tol_pp.

    This is the sanity check that the MC machinery is correct.
    """
    pairs = lambda_pairs or DEFAULT_PAIRS
    results = []
    max_dev = 0.0

    for lh, la in pairs:
        cfg = SimConfig(
            n_sims=n_sims,
            first_half_stoppage=0,
            second_half_stoppage=0,
            seed=seed,
        )
        sim_res = simulate(lh, la, config=cfg, adjuster=None)
        sim_p = price_markets(sim_res)
        poi = _analytical_poisson(lh, la)

        diffs = {
            k: (sim_p[k] - poi[k]) * 100.0
            for k in ("home_win", "draw", "away_win", "over_1_5", "over_2_5",
                      "over_3_5", "btts")
        }
        # Expected-goals deviation in absolute goals (not pp)
        exp_diff = sim_p["exp_total_goals"] - poi["exp_total_goals"]

        pair_max = max(abs(v) for v in diffs.values())
        max_dev = max(max_dev, pair_max)

        results.append({
            "lambdas": (lh, la),
            "diff_pp": diffs,
            "exp_goal_diff": exp_diff,
            "max_diff_pp": pair_max,
        })

    return {
        "max_deviation_pp": max_dev,
        "tolerance_pp": tol_pp,
        "passed": max_dev <= tol_pp,
        "n_sims": n_sims,
        "pairs": results,
    }


def compare_adjusted_vs_poisson(
    lambda_pairs: Optional[List[Tuple[float, float]]] = None,
    n_sims: int = 50_000,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Run the full adjusted simulator and report the (signed) deviation from
    independent Poisson per market. Differences here are the *effect* of
    state-dependent dynamics — we want them to be small but sensible.

    Expected directional effects:
      - leader sit-back / trailing push → modest ↑ in draws + total goals
        relative to pure Poisson, especially for one-sided lambdas.
    """
    pairs = lambda_pairs or DEFAULT_PAIRS
    results = []

    for lh, la in pairs:
        cfg = SimConfig(n_sims=n_sims, seed=seed)
        sim_res = simulate(lh, la, config=cfg)
        sim_p = price_markets(sim_res)
        poi = _analytical_poisson(lh, la)

        diffs = {
            k: (sim_p[k] - poi[k]) * 100.0
            for k in ("home_win", "draw", "away_win", "over_2_5", "btts")
        }
        results.append({
            "lambdas": (lh, la),
            "sim": {k: sim_p[k] for k in diffs},
            "poisson": {k: poi[k] for k in diffs},
            "diff_pp": diffs,
            "exp_total_goals_sim": sim_p["exp_total_goals"],
            "exp_total_goals_poi": poi["exp_total_goals"],
        })

    return {"n_sims": n_sims, "pairs": results}
