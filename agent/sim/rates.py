"""
Per-minute base rates from match-level inputs.

`from_lambdas` is the standard entry point: pass DC-style xG lambdas
(expected goals over a full 90-minute match), get back the per-minute
mean rates the simulator samples from.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BaseRates:
    """Mean events per minute. Used as Poisson / Bernoulli rates per tick."""

    goal_h: float
    goal_a: float
    # Red cards: ~3-4% of matches per side (per Opta, top 5 leagues).
    # 0.0004/min * 90 min ≈ 0.036 expected reds per side per match.
    red_h: float = 0.0004
    red_a: float = 0.0004


def from_lambdas(lambda_h: float, lambda_a: float, T: int = 90) -> BaseRates:
    """
    Convert match-level expected goals to per-minute Poisson rates.

    Inputs are DC-style lambdas (full-match xG). T is the regulation
    minute count used to normalise (default 90).
    """
    return BaseRates(
        goal_h=lambda_h / T,
        goal_a=lambda_a / T,
    )
