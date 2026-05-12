"""
Dixon-Coles football pricing model with xG integration and time decay.

References: Dixon & Coles (1997), extended with xG signal.

Model:
  λ_home = exp(attack[h] + defense[a] + home_adv)
  λ_away = exp(attack[a] + defense[h])

Low-score correction τ(x, y, λ_h, λ_a, ρ) for {0-0, 1-0, 0-1, 1-1}.
Fit via weighted MLE (scipy L-BFGS-B), weights = time decay * xG multiplier.

Usage:
  from agent.dixon_coles import DixonColesModel

  model = DixonColesModel.load('agent/dc_model_params.json')
  probs = model.predict('Arsenal', 'Chelsea')
  # → {'home_win': 0.45, 'draw': 0.27, 'away_win': 0.28, 'over_2_5': 0.61, ...}
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln


class DixonColesModel:
    """
    Dixon-Coles bivariate Poisson model with:
    - xG as training signal (more stable than goals)
    - Exponential time decay (recent matches weighted more)
    - Dixon-Coles low-score correction (rho)
    """

    def __init__(self, half_life_days: float = 90.0, xg_multiplier: float = 1.5):
        self.half_life_days = half_life_days
        self.xg_multiplier = xg_multiplier

        # Filled after fit()
        self.teams: list[str] = []
        self.team_idx: dict[str, int] = {}
        self.attack: np.ndarray = np.array([])
        self.defense: np.ndarray = np.array([])
        self.home_adv: float = 0.0
        self.rho: float = 0.0
        self.fitted: bool = False
        self.fit_date: str = ""
        self.n_matches: int = 0
        self.log_loss: float = 0.0

    # ── Core math ─────────────────────────────────────────────────────────────

    def _decay(self, days_ago: float) -> float:
        return math.exp(-math.log(2) * days_ago / self.half_life_days)

    def _lambdas(self, h: int, a: int) -> tuple[float, float]:
        lh = math.exp(self.attack[h] + self.defense[a] + self.home_adv)
        la = math.exp(self.attack[a] + self.defense[h])
        return lh, la

    @staticmethod
    def _tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
        if x == 0 and y == 0:
            return 1.0 - lh * la * rho
        if x == 1 and y == 0:
            return 1.0 + la * rho
        if x == 0 and y == 1:
            return 1.0 + lh * rho
        if x == 1 and y == 1:
            return 1.0 - rho
        return 1.0

    # ── Fit ───────────────────────────────────────────────────────────────────

    def fit(self, matches: list[dict], ref_date: Optional[datetime] = None) -> "DixonColesModel":
        """
        Fit the model from a list of match dicts.

        Each dict must have:
          home_team: str
          away_team: str
          home_goals: int   (actual goals, or rounded xG)
          away_goals: int
          kickoff_utc: datetime (tz-aware preferred)
          has_xg: bool      (if True → apply xg_multiplier)
        """
        if ref_date is None:
            ref_date = datetime.now(timezone.utc)

        # Build team index
        teams = sorted({m["home_team"] for m in matches} | {m["away_team"] for m in matches})
        self.teams = teams
        self.team_idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        # Vectorise match data
        h_idx = np.array([self.team_idx[m["home_team"]] for m in matches], dtype=np.int32)
        a_idx = np.array([self.team_idx[m["away_team"]] for m in matches], dtype=np.int32)
        hg = np.array([m["home_goals"] for m in matches], dtype=np.float64)
        ag = np.array([m["away_goals"] for m in matches], dtype=np.float64)

        # Precompute log-factorial terms (Poisson log-PMF constant part)
        lg_h = gammaln(hg + 1)
        lg_a = gammaln(ag + 1)

        # Compute time-decay weights
        weights = np.empty(len(matches))
        for i, m in enumerate(matches):
            ko = m["kickoff_utc"]
            if ko.tzinfo is None:
                ko = ko.replace(tzinfo=timezone.utc)
            days_ago = max(0.0, (ref_date - ko).total_seconds() / 86400.0)
            w = self._decay(days_ago)
            if m.get("has_xg"):
                w *= self.xg_multiplier
            weights[i] = w

        # Boolean masks for DC correction
        m00 = (hg == 0) & (ag == 0)
        m10 = (hg == 1) & (ag == 0)
        m01 = (hg == 0) & (ag == 1)
        m11 = (hg == 1) & (ag == 1)

        def neg_ll(params: np.ndarray) -> float:
            atk = params[:n]
            atk = atk - atk.mean()  # zero-sum constraint
            dfn = params[n : 2 * n]
            hadv = params[2 * n]
            rho = params[2 * n + 1]

            log_lh = atk[h_idx] + dfn[a_idx] + hadv
            log_la = atk[a_idx] + dfn[h_idx]
            lh = np.exp(log_lh)
            la = np.exp(log_la)

            # Poisson log-PMF: k·log(λ) − λ − log(k!)
            log_ph = hg * log_lh - lh - lg_h
            log_pa = ag * log_la - la - lg_a

            # DC τ correction (vectorised)
            tau = np.ones(len(matches))
            tau[m00] = 1.0 - lh[m00] * la[m00] * rho
            tau[m10] = 1.0 + la[m10] * rho
            tau[m01] = 1.0 + lh[m01] * rho
            tau[m11] = 1.0 - rho

            if np.any(tau <= 0):
                return 1e12

            ll = np.log(tau) + log_ph + log_pa
            return -float(np.dot(weights, ll))

        # Initialise: attack/defense = 0, home_adv = 0.1, rho = -0.1
        x0 = np.zeros(2 * n + 2)
        x0[2 * n] = 0.1
        x0[2 * n + 1] = -0.1

        bounds = [(None, None)] * (2 * n + 1) + [(-0.9, 0.4)]

        result = minimize(
            neg_ll,
            x0,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 2000, "ftol": 1e-10, "gtol": 1e-7},
        )

        params = result.x
        atk = params[:n]
        atk -= atk.mean()

        self.attack = atk
        self.defense = params[n : 2 * n]
        self.home_adv = float(params[2 * n])
        self.rho = float(params[2 * n + 1])
        self.fitted = True
        self.fit_date = ref_date.isoformat()
        self.n_matches = len(matches)
        self.log_loss = float(result.fun / weights.sum())

        return self

    # ── Predict ───────────────────────────────────────────────────────────────

    def predict(self, home_team: str, away_team: str, max_goals: int = 8) -> dict:
        """
        Return outcome probabilities for a match.

        Raises ValueError for unknown teams — use predict_or_none() if you want None instead.
        """
        if not self.fitted:
            raise RuntimeError("Model not fitted yet. Call fit() or load() first.")

        h = self.team_idx.get(home_team)
        a = self.team_idx.get(away_team)
        if h is None or a is None:
            unknown = [t for t in (home_team, away_team) if t not in self.team_idx]
            raise ValueError(f"Unknown team(s): {unknown}")

        lh, la = self._lambdas(h, a)
        rho = self.rho

        # Build (max_goals+1) × (max_goals+1) score probability matrix
        g = max_goals + 1
        ph = np.exp(-lh) * np.array([lh**k / math.factorial(k) for k in range(g)])
        pa = np.exp(-la) * np.array([la**k / math.factorial(k) for k in range(g)])
        matrix = np.outer(ph, pa)

        # Apply DC correction to low scores
        for x in range(2):
            for y in range(2):
                matrix[x, y] *= max(0.0, self._tau(x, y, lh, la, rho))

        # Normalise
        total = matrix.sum()
        if total > 0:
            matrix /= total

        home_win = float(np.tril(matrix, -1).sum())   # home goals > away goals
        draw = float(np.trace(matrix))
        away_win = float(np.triu(matrix, 1).sum())

        rows, cols = np.mgrid[0:g, 0:g]
        total_goals = rows + cols

        def _mkt(condition: np.ndarray) -> float:
            return float(matrix[condition].sum())

        return {
            "home_win":  home_win,
            "draw":      draw,
            "away_win":  away_win,
            "over_2_5":  _mkt(total_goals > 2.5),
            "under_2_5": _mkt(total_goals <= 2.5),
            "over_1_5":  _mkt(total_goals > 1.5),
            "under_1_5": _mkt(total_goals <= 1.5),
            "over_3_5":  _mkt(total_goals > 3.5),
            "under_3_5": _mkt(total_goals <= 3.5),
            "btts":      _mkt((rows >= 1) & (cols >= 1)),
            "no_btts":   _mkt((rows == 0) | (cols == 0)),
            "lambda_home": float(lh),
            "lambda_away": float(la),
        }

    def predict_or_none(self, home_team: str, away_team: str) -> dict | None:
        """Like predict() but returns None for unknown teams."""
        try:
            return self.predict(home_team, away_team)
        except ValueError:
            return None

    # ── Team strength helpers ─────────────────────────────────────────────────

    def team_ratings(self) -> list[dict]:
        """Return all teams sorted by overall strength (attack − defense_conceded)."""
        if not self.fitted:
            return []
        rows = []
        for i, team in enumerate(self.teams):
            rows.append({
                "team": team,
                "attack": round(float(self.attack[i]), 4),
                "defense": round(float(self.defense[i]), 4),
                "strength": round(float(self.attack[i] - self.defense[i]), 4),
            })
        return sorted(rows, key=lambda r: -r["strength"])

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        data = {
            "teams": self.teams,
            "attack": self.attack.tolist(),
            "defense": self.defense.tolist(),
            "home_adv": self.home_adv,
            "rho": self.rho,
            "half_life_days": self.half_life_days,
            "xg_multiplier": self.xg_multiplier,
            "fit_date": self.fit_date,
            "n_matches": self.n_matches,
            "log_loss": self.log_loss,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "DixonColesModel":
        with open(path) as f:
            data = json.load(f)
        model = cls(
            half_life_days=data["half_life_days"],
            xg_multiplier=data.get("xg_multiplier", 1.5),
        )
        model.teams = data["teams"]
        model.team_idx = {t: i for i, t in enumerate(data["teams"])}
        model.attack = np.array(data["attack"])
        model.defense = np.array(data["defense"])
        model.home_adv = data["home_adv"]
        model.rho = data["rho"]
        model.fitted = True
        model.fit_date = data.get("fit_date", "")
        model.n_matches = data.get("n_matches", 0)
        model.log_loss = data.get("log_loss", 0.0)
        return model
