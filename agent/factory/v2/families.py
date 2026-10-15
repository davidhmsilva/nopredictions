"""
families.py — test a CLASS of information once, with every row, before
testing any filter built from it.

v1 asked "does form ≥ +6 at 1.6-2.2 pay?" hundreds of times on ~200 bets each.
A family asks "does form carry anything the price does not?" once, on ~100k
rows, with the price as a control. ~50 family tests replace thousands of spec
tests, so the multiplicity bill is small and each test has real power.

Two targets, two different questions:

    move         logit(close) − logit(pre-close): does the information predict
                 where the price GOES? (what a CLV edge is made of)
    close_bias   won − close: is the CLOSE wrong along this dimension? (what a
                 result edge is made of — the favourite–longshot bias lives here)

Controls are the price itself (the entry price for `move`, the close for
`close_bias`, linear and squared) and is_home on the side view, so a family is
credited only with what the price does not already say. `price_level` is the
exception: it IS the price terms, tested against a constant.

Validation is out of sample in the strict sense: the family's fitted
component from TRAIN coefficients, projected onto validation rows, must earn a
positive coefficient there. Nothing is refitted on validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import stats

TARGETS = ("move", "close_bias")
SIDE_TEAM = ["x_gd_f", "x_gd_s", "x_sot_f", "x_sot_s", "x_sh_s", "x_mres", "x_rest"]
MATCH_TEAM = ["ax_gd_s", "ax_sot_s", "ax_mres"]


@dataclass
class Family:
    name: str
    info: str
    views: dict = field(default_factory=dict)       # view -> feature names (see _features)
    extra: dict = field(default_factory=dict)       # view -> extra CONTROLS (an interaction's main effects)


FAMILIES = [
    Family("price_level", "the price level itself (calibration of the close / drift by price)",
           {"1x2_sides": ["price", "price2"], "1x2_draw": ["price", "price2"], "ou25_over": ["price", "price2"]}),
    Family("form", "points in the last 5 (side − opponent; |difference| where sides do not apply)",
           {"1x2_sides": ["form_diff"], "1x2_draw": ["abs_form_diff"], "ou25_over": ["abs_form_diff"]}),
    Family("rest", "days since the previous match",
           {"1x2_sides": ["rest_diff"], "1x2_draw": ["abs_rest_diff"], "ou25_over": ["abs_rest_diff"]}),
    Family("goals_avg", "average total goals in both teams' last 5",
           {"1x2_draw": ["tg5_sum"], "ou25_over": ["tg5_sum"]}),
    Family("team_state", "close_model's EWMAs: goal difference, shots on target, shots, points vs the price, rest",
           {"1x2_sides": SIDE_TEAM, "1x2_draw": MATCH_TEAM, "ou25_over": MATCH_TEAM}),
    Family("model_score", "close_model's walk-forward predicted move (2015+, home/away only)",
           {"1x2_sides": ["mv_hat"]}),
    Family("tier", "division (tier 2, tier 3+ vs tier 1)",
           {"1x2_sides": ["tier2", "tier3"], "1x2_draw": ["tier2", "tier3"], "ou25_over": ["tier2", "tier3"]}),
    Family("season_phase", "early season (Aug-Sep) and run-in (Apr-May)",
           {"1x2_sides": ["early", "late"], "1x2_draw": ["early", "late"], "ou25_over": ["early", "late"]}),
    Family("league", "league identity (one dummy per league)",
           {"1x2_sides": ["league*"], "1x2_draw": ["league*"], "ou25_over": ["league*"]}),
    # added 2026-10-10 after an exploratory look at train+validation by entry time;
    # its first test is the holdout (ledger round 2)
    Family("entry_timing", "does the model's predicted move predict MORE when the entry is earlier? "
           "(mv_hat × days before kick-off, with both main effects and league as controls)",
           {"1x2_sides": ["mv_x_hb"]}, extra={"1x2_sides": ["mv_hat", "hb_days", "league*"]}),
]


def _target(d: pd.DataFrame, target: str) -> np.ndarray:
    if target == "move":
        return d.move.to_numpy(float)
    if target == "close_bias":
        return d.won.to_numpy(float) - d.p1.to_numpy(float)
    raise ValueError(target)


def _price(d: pd.DataFrame, target: str) -> np.ndarray:
    return (d.l0 if target == "move" else d.l1).to_numpy(float)


def _features(d: pd.DataFrame, names: list, target: str, leagues: list) -> tuple[np.ndarray, list]:
    cols, out = [], []
    for n in names:
        if n == "price":
            cols.append(_price(d, target)); out.append("price")
        elif n == "price2":
            cols.append(_price(d, target) ** 2); out.append("price²")
        elif n == "tier2":
            cols.append((d.tier.to_numpy(float) == 2).astype(float)); out.append("tier2")
        elif n == "tier3":
            cols.append((d.tier.to_numpy(float) >= 3).astype(float)); out.append("tier3+")
        elif n == "early":
            cols.append(d.month.isin([8, 9]).to_numpy(float)); out.append("early")
        elif n == "late":
            cols.append(d.month.isin([4, 5]).to_numpy(float)); out.append("late")
        elif n == "league*":
            for lg in leagues[1:]:                   # the commonest league is the baseline
                cols.append((d.league_code.to_numpy() == lg).astype(float)); out.append(lg)
        else:
            cols.append(d[n].to_numpy(float)); out.append(n)
    return np.column_stack(cols), out


def _controls(d: pd.DataFrame, view: str, target: str, fam: Family, leagues: list) -> np.ndarray:
    cols = [np.ones(len(d))]
    if fam.name != "price_level":
        p = _price(d, target)
        cols += [p, p ** 2]
    if view == "1x2_sides":
        cols.append(d.is_home.to_numpy(float))
    if fam.extra.get(view):
        cols.append(_features(d, fam.extra[view], target, leagues)[0])
    return np.column_stack(cols)


def _design(d: pd.DataFrame, view: str, target: str, fam: Family, leagues: list):
    y = _target(d, target)
    Xc = _controls(d, view, target, fam, leagues)
    Xf, names = _features(d, fam.views[view], target, leagues)
    ok = np.isfinite(y) & np.isfinite(Xc).all(1) & np.isfinite(Xf).all(1)
    return y[ok], Xc[ok], Xf[ok], d.match_id.to_numpy()[ok], names, ok


def fit_family(d: pd.DataFrame, view: str, target: str, fam: Family, leagues: list) -> dict:
    """In-sample: the family's coefficients and the joint Wald test."""
    y, Xc, Xf, g, names, _ = _design(d, view, target, fam, leagues)
    if len(y) < 200:
        return {"n": int(len(y))}
    X = np.column_stack([Xc, Xf])
    beta, V = stats.ols_cluster(y, X, g)
    idx = list(range(Xc.shape[1], X.shape[1]))
    chi2, df, p = stats.wald(beta, V, idx)
    sst = float(((y - y.mean()) ** 2).sum())
    bc = np.linalg.lstsq(Xc, y, rcond=None)[0]
    r2_gain = (float(((y - Xc @ bc) ** 2).sum()) - float(((y - X @ beta) ** 2).sum())) / sst if sst else 0.0
    return {"n": int(len(y)), "matches": int(len(np.unique(g))), "chi2": chi2, "df": df, "p": p,
            "r2_gain": r2_gain, "beta_f": beta[idx], "names": names}


def project(d: pd.DataFrame, view: str, target: str, fam: Family, leagues: list, beta_f: np.ndarray) -> dict:
    """Out of sample: does the family component fitted elsewhere predict here?
    γ > 0 means the direction holds; γ ≈ 1 means the size holds too."""
    y, Xc, Xf, g, _, _ = _design(d, view, target, fam, leagues)
    if len(y) < 200:
        return {"n": int(len(y)), "p": 1.0}
    c = Xf @ beta_f
    X = np.column_stack([Xc, c])
    beta, V = stats.ols_cluster(y, X, g)
    gam, se, p = stats.coef_test(beta, V, X.shape[1] - 1)
    sst = float(((y - y.mean()) ** 2).sum())
    bc = np.linalg.lstsq(Xc, y, rcond=None)[0]
    r2_gain = (float(((y - Xc @ bc) ** 2).sum()) - float(((y - X @ beta) ** 2).sum())) / sst if sst else 0.0
    # the spread of the component, in the target's units (pp for close_bias, logit for move)
    return {"n": int(len(y)), "matches": int(len(np.unique(g))), "gamma": gam, "gamma_se": se, "p": p,
            "r2_gain": r2_gain, "component_sd": float(np.std(c))}


def tests() -> list:
    """Every (family, view, target) the protocol runs."""
    return [(f, v, t) for f in FAMILIES for v in f.views for t in TARGETS]


def leagues_of(d: pd.DataFrame) -> list:
    """League dummies by frequency, fixed from the data the coefficients are fitted on."""
    return d.league_code.value_counts().index.tolist()
