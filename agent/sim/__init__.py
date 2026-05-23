"""
Possession-based Monte Carlo match simulator.

The successor to the closed-form Dixon-Coles pricer. One engine prices
every market (1X2, totals, BTTS, HT/FT, exact scores, handicaps) from
one simulated distribution, with full support for state-dependent
dynamics (red cards, score momentum, late-game push).
"""

from .simulator import simulate, SimConfig, SimResult
from .state import MatchState
from .rates import BaseRates, from_lambdas
from .pricer import price_markets

__all__ = [
    "simulate",
    "SimConfig",
    "SimResult",
    "MatchState",
    "BaseRates",
    "from_lambdas",
    "price_markets",
]
