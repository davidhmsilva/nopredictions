"""
State-dependent rate modifiers — the simulator's source of edge over
closed-form Poisson.

Bakes into one source-of-truth the dynamics currently scattered across
poisson_trader.py v2:
  - Red card asymmetry
  - Trailing-team push / leader sit-back, scaled by minute
  - Late-game amplification past 70'

Every market priced by the simulator (1X2, BTTS, HT/FT, totals, exact
scores) inherits these dynamics automatically.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

from .rates import BaseRates
from .state import MatchState


def adjusted_goal_rates(
    base: BaseRates,
    state: MatchState,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return (rate_h, rate_a) of shape (n_sims,) for the current minute,
    given the current scoreline and red-card counts across the batch.
    """
    n = state.n_sims
    rh = np.full(n, base.goal_h, dtype=np.float64)
    ra = np.full(n, base.goal_a, dtype=np.float64)

    # ── Red card asymmetry ────────────────────────────────────────────
    # 10-man side ~0.80× lambda, opponent ~1.10× (in line with poisson_trader v2).
    delta_red = state.red_h - state.red_a  # >0: home short of men
    short_home = delta_red > 0
    short_away = delta_red < 0

    rh[short_home] *= 0.80
    ra[short_home] *= 1.10
    rh[short_away] *= 1.10
    ra[short_away] *= 0.80

    # ── Score-state dynamics ─────────────────────────────────────────
    # Leader sits back, trailing team pushes. Effect grows with minute:
    # negligible at kickoff, full strength near the end.
    minute_factor = min(state.minute, 90) / 90.0  # 0 → 1
    push = 1.0 + 0.15 * minute_factor  # trailing team
    sit = 1.0 - 0.08 * minute_factor   # leader

    diff = state.home_score - state.away_score
    home_leads = diff > 0
    away_leads = diff < 0

    rh[home_leads] *= sit
    ra[home_leads] *= push
    rh[away_leads] *= push
    ra[away_leads] *= sit

    # ── Late-game amplification (70'+) ───────────────────────────────
    # Extra kick on top of the score-state effect when chasing late.
    if state.minute >= 70:
        late_boost = 1.0 + 0.10 * min(state.minute - 70, 20) / 20.0
        rh[away_leads] *= late_boost
        ra[home_leads] *= late_boost

    return rh, ra
