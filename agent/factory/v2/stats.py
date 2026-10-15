"""
stats.py — the measurement layer of factory v2.

Three ideas, each a fix for a number the v1 audit measured:

  * CLV before yield. Valued at the de-vigged close, a bet's per-unit return
    has ~15x less noise than its realised result (median over the v1 open
    grid), so a question needs ~240x fewer bets. Yield stays the confirmation.
  * Shrink before ranking. The top-10 v1 specs by train z went +11.6pp →
    −12.6pp on test. Ranking on raw means picks the luckiest; ranking on an
    empirical-Bayes posterior pulls each mean toward its family by its noise.
  * Refuse what cannot be resolved. A spec whose minimum detectable effect is
    larger than any plausible edge is not tested at all: testing it adds to the
    multiplicity bill and cannot add a discovery.

How a bet is bought is part of the question, never an afterthought:

    pin_raw          Pinnacle's own pre-close odds, margin in, no fee
    pm_taker         de-vigged pre-close + PM half-spread, plus the 0.05·p·(1−p) fee
    pm_maker_loose   a bid at de-vigged pre-close − half-spread that ALWAYS fills
    pm_maker_strict  the same bid, filled only when the close reaches it

The two maker fills bracket the truth, and neither may be judged on CLV: loose
is +half-spread/p by construction (every spec positive, longshots most of all —
the first v2 run "validated" ten away longshots at +10% exactly this way) and
strict is ≤ 0 by construction (a bid the close traded through). On YIELD,
strict is the adverse-selection question: do prices that drifted down to the
bid still win often enough? `check()` refuses the meaningless pairs.

Objectives: `clv` = expected return if the de-vigged close is the truth (what
a bet is worth); `yield` = realised return; `beat_close` = o0/o1 − 1, raw odds
against raw odds, pin_raw only. beat_close is how close_model reported its
+1.19%: the price moved that much, but the bet still paid the close's margin,
so it is a diagnostic, not a value.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy import stats as sps

PM_HALF_SPREAD = 0.006        # PM ask vs de-vigged Pinnacle, before fee: +0.58pp (n=272)
PM_FEE = 0.05
EXECUTIONS = ("pin_raw", "pm_taker", "pm_maker_loose", "pm_maker_strict")
OBJECTIVES = ("clv", "yield", "beat_close")


def check(objective: str, execution: str) -> None:
    """Refuse a question whose answer is fixed by construction."""
    if objective not in OBJECTIVES or execution not in EXECUTIONS:
        raise ValueError(f"objective in {OBJECTIVES}, execution in {EXECUTIONS}")
    if objective == "clv" and execution.startswith("pm_maker"):
        raise ValueError("clv on a maker fill is fixed by construction (loose: +half-spread/p for every spec; "
                         "strict: <= 0) — judge maker executions on yield")
    if objective == "beat_close" and execution != "pin_raw":
        raise ValueError("beat_close compares raw odds with raw odds: pin_raw only")


# ── prices ──────────────────────────────────────────────────────────────────

def cost(p0: np.ndarray, o0: np.ndarray, execution: str) -> np.ndarray:
    """Cash per share (a share pays 1 if the bet wins)."""
    p0, o0 = np.asarray(p0, float), np.asarray(o0, float)
    if execution == "pin_raw":
        return 1.0 / o0
    if execution == "pm_taker":
        ask = p0 + PM_HALF_SPREAD
        return ask * (1.0 + PM_FEE * (1.0 - ask))
    if execution in ("pm_maker_loose", "pm_maker_strict"):
        return p0 - PM_HALF_SPREAD
    raise ValueError(f"execution must be one of {EXECUTIONS}, not {execution!r}")


def per_bet(objective: str, execution: str, p0, o0, p1, won, o1=None) -> np.ndarray:
    """Return per unit staked. NaN where the inputs are missing, the price is
    unusable, or (pm_maker_strict) the bid never filled."""
    check(objective, execution)
    c = cost(p0, o0, execution)
    p1 = np.asarray(p1, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(c) & (c > 0) & (c < 1)
        if execution == "pm_maker_strict":
            ok &= p1 <= c
        if objective == "clv":
            v = p1 / c - 1.0
        elif objective == "yield":
            v = np.asarray(won, float) / c - 1.0
        else:
            v = np.asarray(o0, float) / np.asarray(o1, float) - 1.0
    return np.where(ok, v, np.nan)


# ── one sample ──────────────────────────────────────────────────────────────

def mean_test(x: np.ndarray) -> dict:
    """Mean, standard error and one-sided p (H1: mean > 0) of per-bet returns."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = int(x.size)
    if n < 3:
        return {"n": n, "mean": float(x.mean()) if n else None, "se": None, "p": 1.0}
    mu = float(x.mean())
    se = float(x.std(ddof=1) / math.sqrt(n))
    p = float(sps.norm.sf(mu / se)) if se > 0 else (0.0 if mu > 0 else 1.0)
    return {"n": n, "mean": mu, "se": se, "p": p}


def mde(se: Optional[float], alpha: float = 0.05, power: float = 0.80) -> float:
    """Smallest true mean a one-sided test at `alpha` detects with `power`."""
    if se is None or not np.isfinite(se):
        return float("inf")
    return float((sps.norm.isf(alpha) + sps.norm.isf(1 - power)) * se)


# ── regression ──────────────────────────────────────────────────────────────

def ols_cluster(y: np.ndarray, X: np.ndarray, groups: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """OLS with a cluster-robust (CR1) covariance. Two sides of one match share
    a result and a close, so they are one observation's worth of evidence, not
    two — clustering on the match is what keeps a p-value honest."""
    y, X = np.asarray(y, float), np.asarray(X, float)
    n, k = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    u = y - X @ beta
    codes, inv = np.unique(np.asarray(groups), return_inverse=True)
    G = len(codes)
    S = np.zeros((G, k))
    np.add.at(S, inv, X * u[:, None])
    meat = S.T @ S
    corr = (G / (G - 1)) * ((n - 1) / (n - k)) if G > 1 and n > k else 1.0
    V = corr * XtX_inv @ meat @ XtX_inv
    return beta, V


def wald(beta: np.ndarray, V: np.ndarray, idx) -> tuple[float, int, float]:
    """Joint test that beta[idx] are all zero: (chi², df, p)."""
    idx = list(idx)
    b = beta[idx]
    Vi = V[np.ix_(idx, idx)]
    chi2 = float(b @ np.linalg.pinv(Vi) @ b)
    df = len(idx)
    return chi2, df, float(sps.chi2.sf(chi2, df))


def coef_test(beta: np.ndarray, V: np.ndarray, j: int) -> tuple[float, float, float]:
    """One coefficient: (estimate, se, one-sided p for > 0)."""
    b, se = float(beta[j]), float(math.sqrt(max(V[j, j], 0.0)))
    p = float(sps.norm.sf(b / se)) if se > 0 else (0.0 if b > 0 else 1.0)
    return b, se, p


# ── many tests ──────────────────────────────────────────────────────────────

def bh_qvalues(p: list) -> list:
    """Benjamini-Hochberg q-values, same order as p."""
    m = len(p)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p[i])
    q = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        q[i] = prev
    return q


def eb_shrink(means: np.ndarray, ses: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Normal-normal empirical Bayes: each spec's true mean ~ N(mu, tau²),
    its estimate ~ N(true, se²). mu and tau² by the method of moments.
    Returns posterior means, posterior sds, mu, tau²."""
    m, s = np.asarray(means, float), np.asarray(ses, float)
    w = 1.0 / s ** 2
    mu = float(np.sum(w * m) / np.sum(w))
    tau2 = float(max(0.0, np.var(m, ddof=1) - np.mean(s ** 2))) if m.size > 1 else 0.0
    if tau2 == 0.0:
        return np.full_like(m, mu), np.zeros_like(m), mu, 0.0
    k = tau2 / (tau2 + s ** 2)
    return mu + k * (m - mu), np.sqrt(k * s ** 2), mu, tau2
