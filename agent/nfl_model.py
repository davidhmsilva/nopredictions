"""
nfl_model.py — price any NFL spread / total / moneyline line from the SHARP main line.

Not a forecast. Pinnacle already says what a game is worth; what it does not say is
what "Eagles -4.5" is worth when it only quotes Eagles -6. Polymarket lists ~30
alternate spreads and ~40 alternate totals per game, always on half-points, while
Pinnacle's main line is often a whole number — so even Polymarket's MAIN spread is
often not directly comparable to Pinnacle's. That translation is this file.

The one NFL fact it has to get right is KEY NUMBERS. Final margins are not normal:
3 and 7 are several times likelier than their neighbours, because points come in
3s and 7s (fitted: m(3) = 2.67, m(7) = 1.74, m(1) = 0.82). Buying -2.5 instead of
-3.5 is worth ~8pp on a close game and ~1pp between 5 and 6. A smooth normal
prices those two moves the same, and a scanner built on it would find its biggest
"edges" exactly where it is most wrong.

Model: P(margin = k | μ, σ) ∝ Normal_disc(k; μ, σ) × m(k), with the key-number
factors m(k) fitted by IPF to the empirical frequency of every final margin
(nflverse closing lines, 2012+, mirrored so home/away asymmetry cannot leak in).
Totals use the same construction on total points.

Anchoring — per game, from the sharp book:
  * μ and σ are solved TOGETHER from the de-vigged spread and moneyline. One
    global σ was measured running 1.3-2.1pp short of Pinnacle's own moneyline on
    every favourite of 6.5+ (2026-09-13, 14 games): a fixed σ is too wide at big
    spreads. With two markets there are two unknowns, so the book's own
    ML/spread relationship sets the dispersion. Near pick'em the moneyline says
    nothing about σ and the historical value is kept.
  * T from the de-vigged total, σ_T historical.
Whatever moneyline residual is left after the fit is recorded: a game where the
book's two markets cannot both be reproduced is one where alternates should not
be priced off this model.

    python nfl_model.py --validate     # history + consistency checks
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.stats import norm

GAMES_CSV = os.path.join(os.path.dirname(__file__), "../ingest/.cache/nfl/games.csv")
GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

MIN_SEASON = 2012
K_MAX = 70                 # margins are clipped to [-K_MAX, K_MAX], totals to [0, 2K_MAX]
IPF_ITERS = 6
SHRINK_N = 60.0            # m(k) shrinks toward 1 where few games landed on k
SIGMA_LO, SIGMA_HI = 10.0, 16.0
SIGMA_SOLVE_MIN_MU = 1.5   # below this |μ| the moneyline cannot identify σ


def load_games(path: str = GAMES_CSV) -> pd.DataFrame:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import requests
        r = requests.get(GAMES_URL, timeout=60)
        r.raise_for_status()
        with open(path, "wb") as fh:
            fh.write(r.content)
    g = pd.read_csv(path, low_memory=False)
    return g[(g["season"] >= MIN_SEASON) & g["result"].notna() & g["spread_line"].notna()
             & g["total"].notna() & g["total_line"].notna()]


def _disc_normal(mu: float, sigma: float, support: np.ndarray) -> np.ndarray:
    """P(X = k) for integer k: a normal integrated over [k-0.5, k+0.5]."""
    return norm.cdf((support + 0.5 - mu) / sigma) - norm.cdf((support - 0.5 - mu) / sigma)


@dataclass
class KeyedDist:
    support: np.ndarray        # integer points
    sigma: float               # historical σ; any call may override it
    factor: np.ndarray         # m(k)

    def pmf(self, mu: float, sigma: float | None = None) -> np.ndarray:
        p = _disc_normal(mu, sigma or self.sigma, self.support) * self.factor
        return p / p.sum()

    def p_over(self, mu: float, line: float, sigma: float | None = None) -> float:
        """Value of a token paying 1 on k > line and 0.5 on k == line — a push
        that Polymarket resolves 50-50."""
        p = self.pmf(mu, sigma)
        return float(p[self.support > line].sum() + 0.5 * p[self.support == line].sum())

    def p_over_no_push(self, mu: float, line: float, sigma: float | None = None) -> float:
        """P(k > line | k != line) — how a bookmaker's whole-number line is priced:
        a push refunds, so the two quoted sides only price the non-push mass."""
        p = self.pmf(mu, sigma)
        over, push = p[self.support > line].sum(), p[self.support == line].sum()
        return float(over / (1.0 - push)) if push < 1 else 0.5


def _fit(values: np.ndarray, means: np.ndarray, support: np.ndarray,
         symmetric: bool) -> KeyedDist:
    """σ from the residuals, then the key-number factors by IPF."""
    sigma = float(np.sqrt(np.mean((values - means) ** 2)))
    factor = np.ones_like(support, dtype=float)
    idx = {int(k): i for i, k in enumerate(support)}
    obs = np.zeros_like(factor)
    for v in values:
        i = idx.get(int(v))
        if i is not None:
            obs[i] += 1
    mu_vals, mu_counts = np.unique(np.round(means * 2) / 2, return_counts=True)
    for _ in range(IPF_ITERS):
        dist = KeyedDist(support, sigma, factor)
        exp = np.zeros_like(factor)
        for mu, n in zip(mu_vals, mu_counts):
            exp += n * dist.pmf(float(mu))
        factor = factor * (obs + SHRINK_N) / (exp + SHRINK_N)
        if symmetric:
            factor = 0.5 * (factor + factor[::-1])
    return KeyedDist(support, sigma, factor)


@lru_cache(maxsize=1)
def margin_dist() -> KeyedDist:
    g = load_games()
    # spread_line is the HOME expected margin (positive = home favoured) and
    # result is home - away; mirrored so the fit is side-free.
    s = np.concatenate([g["spread_line"].values, -g["spread_line"].values]).astype(float)
    r = np.concatenate([g["result"].values, -g["result"].values]).astype(float)
    return _fit(r, s, np.arange(-K_MAX, K_MAX + 1), symmetric=True)


@lru_cache(maxsize=1)
def total_dist() -> KeyedDist:
    g = load_games()
    return _fit(g["total"].values.astype(float), g["total_line"].values.astype(float),
                np.arange(0, 2 * K_MAX + 1), symmetric=False)


# ── pricing ──────────────────────────────────────────────────────────────────

def _solve(f, target: float, lo: float, hi: float, iters: int = 50) -> float:
    """Bisection for an increasing f."""
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def win_prob(mu: float, sigma: float | None = None) -> float:
    """P(team wins), a tie paid 50-50 — Polymarket's NFL moneyline rule."""
    return margin_dist().p_over(mu, 0.0, sigma)


def cover_prob(mu: float, give: float, sigma: float | None = None) -> float:
    """Token value of 'team -give' (give > 0 = laying points; a +3.5 dog is
    give = -3.5): 1 when margin > give, 0.5 on exactly give."""
    return margin_dist().p_over(mu, give, sigma)


def over_prob(T: float, line: float) -> float:
    return total_dist().p_over(T, line)


def mu_from_spread(give: float, p_cover: float, sigma: float | None = None) -> float:
    """Expected margin μ at which a bookmaker's 'team -give' (push refunded)
    is worth p_cover."""
    d = margin_dist()
    return _solve(lambda m: d.p_over_no_push(m, give, sigma), p_cover, -40, 40)


def mu_from_ml(p_win: float, sigma: float | None = None) -> float:
    return _solve(lambda m: win_prob(m, sigma), p_win, -40, 40)


def T_from_total(line: float, p_over: float) -> float:
    d = total_dist()
    return _solve(lambda t: d.p_over_no_push(t, line), p_over, 5, 100)


@dataclass
class Anchor:
    mu: float | None           # expected margin of team A
    sigma: float               # margin dispersion used for this game
    T: float | None            # expected total
    ml_resid_pp: float         # model ML − book ML after the fit, pp
    sigma_solved: bool         # False = historical σ kept (near pick'em, or no ML)


def anchor(*, give_a: float | None, p_cover_a: float | None, p_win_a: float | None,
           total_line: float | None, p_over: float | None) -> Anchor:
    """give_a: team A's handicap as points laid (bookmaker 'A -6.0' → 6.0,
    'A +3.0' → -3.0). Probabilities are de-vigged, for team A / the over."""
    d = margin_dist()
    sigma, solved, mu = d.sigma, False, None
    if give_a is not None and p_cover_a is not None:
        mu = mu_from_spread(give_a, p_cover_a)
        if p_win_a is not None and abs(mu) >= SIGMA_SOLVE_MIN_MU:
            best = None
            for s in np.arange(SIGMA_LO, SIGMA_HI + 1e-9, 0.25):
                m = mu_from_spread(give_a, p_cover_a, float(s))
                err = abs(win_prob(m, float(s)) - p_win_a)
                if best is None or err < best[0]:
                    best = (err, float(s), m)
            _, sigma, mu = best
            solved = True
    elif p_win_a is not None:
        mu = mu_from_ml(p_win_a)
    resid = 100.0 * (win_prob(mu, sigma) - p_win_a) if (mu is not None and p_win_a is not None) else 0.0
    T = T_from_total(total_line, p_over) if total_line is not None and p_over is not None else None
    return Anchor(mu=mu, sigma=sigma, T=T, ml_resid_pp=resid, sigma_solved=solved)


# ── validation ───────────────────────────────────────────────────────────────

def validate() -> None:
    g = load_games()
    d, td = margin_dist(), total_dist()
    print(f"games {len(g)} ({g.season.min()}-{g.season.max()})  margin σ={d.sigma:.2f}  "
          f"total σ={td.sigma:.2f}")
    sup = d.support
    print("  key factors: " + "  ".join(f"m({k})={d.factor[sup == k][0]:.2f}"
                                        for k in (1, 2, 3, 4, 6, 7, 8, 10, 14)))

    print("\nP(home margin > x) by closing spread — model/empirical:")
    for s_lo, s_hi in ((-0.5, 1.5), (2.5, 3.5), (5.5, 7.5), (9.5, 14.5)):
        sub = g[(g.spread_line >= s_lo) & (g.spread_line <= s_hi)]
        row = []
        for x in (-3.5, -0.5, 2.5, 3.5, 6.5, 7.5):
            emp = float((sub.result > x).mean())
            mod = float(np.mean([d.p_over(float(s), x) for s in sub.spread_line]))
            row.append(f"{x:+.1f}: {mod:.3f}/{emp:.3f}")
        print(f"  s∈[{s_lo},{s_hi}] n={len(sub):>4}  " + "  ".join(row))

    print("\nP(total > line) — model/empirical:")
    for T_lo, T_hi in ((37, 40), (43, 46), (49, 53)):
        sub = g[(g.total_line >= T_lo) & (g.total_line <= T_hi)]
        row = []
        for off in (-7, -3.5, -0.5, 0.5, 3.5, 7):
            emp = float((sub.total > sub.total_line + off).mean())
            mod = float(np.mean([td.p_over(float(T), float(T) + off) for T in sub.total_line]))
            row.append(f"{off:+.1f}: {mod:.3f}/{emp:.3f}")
        print(f"  T∈[{T_lo},{T_hi}] n={len(sub):>4}  " + "  ".join(row))


if __name__ == "__main__":
    argparse.ArgumentParser().add_argument("--validate", action="store_true")
    validate()
