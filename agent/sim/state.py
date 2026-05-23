"""
Vectorized match state for the Monte Carlo simulator.

Each field is an np.ndarray of shape (n_sims,), so the full batch of
simulated matches advances together at each minute tick. Scalar `minute`
is shared across the batch (time advances uniformly).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class MatchState:
    n_sims: int
    minute: int = 0
    home_score: Optional[np.ndarray] = field(default=None)
    away_score: Optional[np.ndarray] = field(default=None)
    red_h: Optional[np.ndarray] = field(default=None)
    red_a: Optional[np.ndarray] = field(default=None)

    def __post_init__(self) -> None:
        if self.home_score is None:
            self.home_score = np.zeros(self.n_sims, dtype=np.int32)
        if self.away_score is None:
            self.away_score = np.zeros(self.n_sims, dtype=np.int32)
        if self.red_h is None:
            self.red_h = np.zeros(self.n_sims, dtype=np.int32)
        if self.red_a is None:
            self.red_a = np.zeros(self.n_sims, dtype=np.int32)

    @classmethod
    def fresh(cls, n_sims: int) -> "MatchState":
        """Start of match: 0-0, minute 0, no reds."""
        return cls(n_sims=n_sims)

    @classmethod
    def at(
        cls,
        n_sims: int,
        minute: int,
        home: int,
        away: int,
        red_h: int = 0,
        red_a: int = 0,
    ) -> "MatchState":
        """Start the sim mid-match (for in-play pricing)."""
        s = cls(n_sims=n_sims, minute=minute)
        s.home_score[:] = home
        s.away_score[:] = away
        s.red_h[:] = red_h
        s.red_a[:] = red_a
        return s
