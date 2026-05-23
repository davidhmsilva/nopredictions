"""
Vectorized Monte Carlo match simulator.

Simulates N matches in parallel, minute by minute, with state-dependent
event rates. Output: empirical distribution of all match outcomes,
from which every market is priced.

Pre-match: simulate(lambda_h, lambda_a)
In-play:   simulate(lambda_h, lambda_a, initial_state=MatchState.at(...))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .adjustments import adjusted_goal_rates
from .rates import BaseRates, from_lambdas
from .state import MatchState


# Type for an adjustment function: (base, state) -> (rh_array, ra_array)
Adjuster = Callable[[BaseRates, MatchState], tuple]


@dataclass
class SimConfig:
    n_sims: int = 20_000
    total_minutes: int = 90          # regulation only; stoppage added on top
    first_half_stoppage: int = 2
    second_half_stoppage: int = 4
    seed: Optional[int] = None


@dataclass
class SimResult:
    home_score: np.ndarray   # (n_sims,) int
    away_score: np.ndarray   # (n_sims,) int
    ht_home: np.ndarray      # (n_sims,) int — score at end of 1H
    ht_away: np.ndarray
    red_h: np.ndarray        # (n_sims,) int
    red_a: np.ndarray
    config: SimConfig
    n_minutes_simulated: int = 0


def simulate(
    lambda_h: float,
    lambda_a: float,
    initial_state: Optional[MatchState] = None,
    config: Optional[SimConfig] = None,
    adjuster: Optional[Adjuster] = adjusted_goal_rates,
    base_rates: Optional[BaseRates] = None,
) -> SimResult:
    """
    Run a batch of Monte Carlo match simulations.

    Args:
      lambda_h, lambda_a: full-match xG lambdas (e.g. from DC).
      initial_state: optional mid-match starting state (in-play pricing).
      config: SimConfig (n_sims, stoppage, seed).
      adjuster: state-dep rate function; pass None for pure Poisson.
      base_rates: override BaseRates (e.g. to inject custom corner/red rates).

    Returns:
      SimResult with empirical score distribution.
    """
    cfg = config or SimConfig()
    rng = np.random.default_rng(cfg.seed)
    base = base_rates or from_lambdas(lambda_h, lambda_a, T=cfg.total_minutes)

    # Initial state (defaults to fresh 0-0 at minute 0)
    state = initial_state if initial_state is not None else MatchState.fresh(cfg.n_sims)
    if state.n_sims != cfg.n_sims:
        raise ValueError(
            f"initial_state.n_sims ({state.n_sims}) != cfg.n_sims ({cfg.n_sims})"
        )

    last_minute_1H = 45 + cfg.first_half_stoppage          # exclusive
    last_minute = cfg.total_minutes + cfg.second_half_stoppage  # exclusive

    # If we start at or after HT, freeze the current score as HT.
    if state.minute >= last_minute_1H:
        ht_home = state.home_score.copy()
        ht_away = state.away_score.copy()
    else:
        ht_home = None
        ht_away = None

    start_minute = state.minute
    n_minutes = 0

    for minute in range(start_minute, last_minute):
        state.minute = minute

        if adjuster is not None:
            rh, ra = adjuster(base, state)
        else:
            rh = np.full(cfg.n_sims, base.goal_h, dtype=np.float64)
            ra = np.full(cfg.n_sims, base.goal_a, dtype=np.float64)

        # Sample goals (vectorized Poisson per minute)
        state.home_score += rng.poisson(rh).astype(np.int32)
        state.away_score += rng.poisson(ra).astype(np.int32)

        # Sample red cards (rare Bernoulli per side)
        if base.red_h > 0:
            state.red_h += (rng.random(cfg.n_sims) < base.red_h).astype(np.int32)
        if base.red_a > 0:
            state.red_a += (rng.random(cfg.n_sims) < base.red_a).astype(np.int32)

        # Capture HT at end of 1H stoppage
        if ht_home is None and minute == last_minute_1H - 1:
            ht_home = state.home_score.copy()
            ht_away = state.away_score.copy()

        n_minutes += 1

    # Safety: if we never entered the 1H window (start_minute already past),
    # fall back to the entry score.
    if ht_home is None:
        ht_home = state.home_score.copy()
        ht_away = state.away_score.copy()

    return SimResult(
        home_score=state.home_score,
        away_score=state.away_score,
        ht_home=ht_home,
        ht_away=ht_away,
        red_h=state.red_h,
        red_a=state.red_a,
        config=cfg,
        n_minutes_simulated=n_minutes,
    )
