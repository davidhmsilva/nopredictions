"""
soccer_line_model.py — price any full-match soccer line from the SHARP main line.

Not a forecast, the soccer twin of nfl_model.py. Pinnacle says what a match is
worth through three numbers — the de-vigged 1X2 and the main total — and says
nothing about Over 3.5, "Spain (-1.5)" or both teams to score. Polymarket lists
all of those. This file translates the first into the second.

Model: two Poisson goal counts with the Dixon-Coles low-score correction
(τ on 0-0, 1-0, 0-1, 1-1). Three parameters — λ_home, λ_away, ρ — solved per
match against the book's three independent numbers: P(home), P(away) (P(draw)
is their complement) and P(over the main total). Three equations, three
unknowns, so a well-posed book is reproduced exactly; whatever residual is left
is recorded, and a match whose markets cannot all be reproduced is one where
alternates should not be priced off this model.

With no total quoted, ρ is held at RHO_DEFAULT and only the two λ are fitted.

Pure Python on purpose: 11×11 score grid, Nelder-Mead on three parameters —
no scipy, so it runs in the cron interpreter and in any test container.

    python soccer_line_model.py --demo
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

MAX_GOALS = 10                # grid is 0..MAX_GOALS per side
RHO_DEFAULT = -0.06           # dc_model_params fits -0.09 on club football; a
                              # softer value where the book gives nothing to fit
RHO_LO, RHO_HI = -0.25, 0.10
LAM_LO, LAM_HI = 0.05, 6.0


def _pmf(lam: float) -> list[float]:
    out, p = [], math.exp(-lam)
    for k in range(MAX_GOALS + 1):
        out.append(p)
        p *= lam / (k + 1)
    return out


def grid(lh: float, la: float, rho: float) -> list[list[float]]:
    """P(home = i, away = j), normalised (the tail past MAX_GOALS is dropped)."""
    ph, pa = _pmf(lh), _pmf(la)
    g = [[ph[i] * pa[j] for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
    g[0][0] *= max(1 - lh * la * rho, 0.0)
    g[1][0] *= max(1 + la * rho, 0.0)
    g[0][1] *= max(1 + lh * rho, 0.0)
    g[1][1] *= max(1 - rho, 0.0)
    s = sum(map(sum, g))
    return [[x / s for x in row] for row in g]


def outcome_probs(g) -> tuple[float, float, float]:
    h = d = a = 0.0
    for i, row in enumerate(g):
        for j, p in enumerate(row):
            if i > j:
                h += p
            elif i == j:
                d += p
            else:
                a += p
    return h, d, a


def over_prob(g, line: float) -> float:
    """P(total > line). Half lines only in practice; a whole line pushes, and
    that mass is simply not counted here (callers never price whole lines)."""
    return sum(p for i, row in enumerate(g) for j, p in enumerate(row) if i + j > line)


def cover_prob(g, line: float, team_is_home: bool) -> float:
    """P(team's goal margin + line > 0) — 'Spain (-1.5)' is Spain by 2+."""
    s = 0.0
    for i, row in enumerate(g):
        for j, p in enumerate(row):
            m = (i - j) if team_is_home else (j - i)
            if m + line > 0:
                s += p
    return s


def btts_prob(g) -> float:
    return sum(p for i, row in enumerate(g) for j, p in enumerate(row) if i > 0 and j > 0)


# ── fitting ──────────────────────────────────────────────────────────────────

def _nelder_mead(f, x0: list[float], step: float = 0.2, iters: int = 400, tol: float = 1e-12):
    n = len(x0)
    pts = [list(x0)] + [[x0[j] + (step if j == i else 0.0) for j in range(n)] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(n + 1), key=lambda k: vals[k])
        pts, vals = [pts[k] for k in order], [vals[k] for k in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        c = [sum(p[j] for p in pts[:-1]) / n for j in range(n)]
        xr = [c[j] + (c[j] - pts[-1][j]) for j in range(n)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [c[j] + 2 * (c[j] - pts[-1][j]) for j in range(n)]
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = [c[j] + 0.5 * (pts[-1][j] - c[j]) for j in range(n)]
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                for k in range(1, n + 1):
                    pts[k] = [pts[0][j] + 0.5 * (pts[k][j] - pts[0][j]) for j in range(n)]
                    vals[k] = f(pts[k])
    k = min(range(n + 1), key=lambda i: vals[i])
    return pts[k], vals[k]


@dataclass
class Anchor:
    lh: float                  # expected goals, home
    la: float                  # expected goals, away
    rho: float
    resid_pp: float            # worst miss on the book's own numbers, pp
    rho_fitted: bool

    def grid(self):
        return grid(self.lh, self.la, self.rho)


def _clip(x, lo, hi):
    return min(max(x, lo), hi)


def fit(p_home: float, p_away: float, total_line: float | None = None,
        p_over: float | None = None) -> Anchor:
    """λ_h, λ_a (and ρ when a total is given) reproducing the de-vigged book."""
    has_total = total_line is not None and p_over is not None
    targets = [p_home, p_away] + ([p_over] if has_total else [])

    def model(x):
        lh = _clip(math.exp(x[0]), LAM_LO, LAM_HI)
        la = _clip(math.exp(x[1]), LAM_LO, LAM_HI)
        rho = _clip(x[2], RHO_LO, RHO_HI) if has_total else RHO_DEFAULT
        g = grid(lh, la, rho)
        h, _d, a = outcome_probs(g)
        out = [h, a] + ([over_prob(g, total_line)] if has_total else [])
        return lh, la, rho, out

    def loss(x):
        *_, out = model(x)
        pen = 0.0
        if has_total and not (RHO_LO <= x[2] <= RHO_HI):
            pen = (x[2] - _clip(x[2], RHO_LO, RHO_HI)) ** 2
        return sum((o - t) ** 2 for o, t in zip(out, targets)) + pen

    # start: total split by the favourite's share
    T = (total_line + 0.1) if has_total else 2.6
    share = 0.5 + 0.8 * (p_home - p_away) / 2
    share = _clip(share, 0.15, 0.85)
    x0 = [math.log(T * share), math.log(T * (1 - share))] + ([RHO_DEFAULT] if has_total else [])
    if not has_total:
        x0.append(RHO_DEFAULT)                     # carried, never moved (see model)
    best, _ = _nelder_mead(loss, x0)
    lh, la, rho, out = model(best)
    resid = max(abs(o - t) for o, t in zip(out, targets)) * 100
    return Anchor(lh, la, rho, resid, has_total)


def _demo() -> None:
    # Spain v Switzerland-ish: 0.62 / 0.23 / 0.15, O/U 2.5 over 0.52
    a = fit(0.62, 0.15, 2.5, 0.52)
    g = a.grid()
    h, d, aw = outcome_probs(g)
    print(f"λ {a.lh:.3f}-{a.la:.3f} ρ {a.rho:+.3f} resid {a.resid_pp:.3f}pp")
    print(f"1X2 {h:.3f} {d:.3f} {aw:.3f}  O2.5 {over_prob(g, 2.5):.3f}  O3.5 {over_prob(g, 3.5):.3f}"
          f"  home -1.5 {cover_prob(g, -1.5, True):.3f}  BTTS {btts_prob(g):.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.parse_args()
    _demo()
