"""
Sim output → market probabilities.

Reads any market off the empirical score distribution. Adds binomial
standard errors so callers can stake-size on edge AND confidence.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from .simulator import SimResult


def _se(p: float, n: int) -> float:
    """Binomial standard error for a probability estimate."""
    return float(np.sqrt(p * (1.0 - p) / max(n, 1)))


def price_markets(result: SimResult, top_k_scores: int = 6) -> Dict[str, Any]:
    h = result.home_score
    a = result.away_score
    ht_h = result.ht_home
    ht_a = result.ht_away
    n = len(h)

    total = h + a
    diff = h - a

    # ── 1X2 ──────────────────────────────────────────────────────────
    home_win = float((diff > 0).mean())
    draw = float((diff == 0).mean())
    away_win = float((diff < 0).mean())

    out: Dict[str, Any] = {
        "n_sims": n,
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "se_home_win": _se(home_win, n),
        "se_draw": _se(draw, n),
        "se_away_win": _se(away_win, n),
    }

    # ── Totals ───────────────────────────────────────────────────────
    for line in (0, 1, 2, 3, 4, 5):
        p_over = float((total > line).mean())
        out[f"over_{line}_5"] = p_over
        out[f"under_{line}_5"] = 1.0 - p_over

    # ── BTTS ─────────────────────────────────────────────────────────
    btts = float(((h >= 1) & (a >= 1)).mean())
    out["btts"] = btts
    out["no_btts"] = 1.0 - btts

    # ── Asian handicaps ──────────────────────────────────────────────
    # "wins_by_N_plus" matches PM's "Spread: X (-N.5)" — yes side = covers.
    out["home_wins_by_2plus"] = float((diff > 1).mean())   # home -1.5 yes
    out["home_wins_by_3plus"] = float((diff > 2).mean())   # home -2.5 yes
    out["away_wins_by_2plus"] = float((diff < -1).mean())  # away -1.5 yes
    out["away_wins_by_3plus"] = float((diff < -2).mean())  # away -2.5 yes

    # ── Expected values ──────────────────────────────────────────────
    out["exp_home_goals"] = float(h.mean())
    out["exp_away_goals"] = float(a.mean())
    out["exp_total_goals"] = float(total.mean())

    # ── Top exact scores ─────────────────────────────────────────────
    # Cap at a sensible grid to avoid huge unique() calls in rare blowouts.
    cap = 8
    hc = np.clip(h, 0, cap)
    ac = np.clip(a, 0, cap)
    pairs, counts = np.unique(np.stack([hc, ac], axis=1), axis=0, return_counts=True)
    top_idx = np.argsort(-counts)[:top_k_scores]
    top_scores: List[Tuple[int, int, float]] = [
        (int(pairs[i, 0]), int(pairs[i, 1]), float(counts[i] / n))
        for i in top_idx
    ]
    out["top_scores"] = top_scores

    # ── Half-time ────────────────────────────────────────────────────
    ht_diff = ht_h - ht_a
    out["ht_home_win"] = float((ht_diff > 0).mean())
    out["ht_draw"] = float((ht_diff == 0).mean())
    out["ht_away_win"] = float((ht_diff < 0).mean())

    # ── HT/FT 3×3 grid ───────────────────────────────────────────────
    ht_res = np.where(ht_diff > 0, 1, np.where(ht_diff < 0, 2, 0))
    ft_res = np.where(diff > 0, 1, np.where(diff < 0, 2, 0))
    labels = {0: "D", 1: "H", 2: "A"}
    htft: Dict[str, float] = {}
    for i in (0, 1, 2):
        for j in (0, 1, 2):
            htft[f"{labels[i]}/{labels[j]}"] = float(
                ((ht_res == i) & (ft_res == j)).mean()
            )
    out["htft"] = htft

    # ── Reds (informational) ─────────────────────────────────────────
    out["exp_red_h"] = float(result.red_h.mean())
    out["exp_red_a"] = float(result.red_a.mean())

    return out
