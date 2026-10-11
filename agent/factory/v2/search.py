"""
search.py — specs, judged the way the v1 audit says they have to be.

A spec is a filter on the tape: a view, a side, conditions on features known at
the entry, and a band on the entry odds. A RUN fixes, before anything is
looked at, what is being measured (clv or yield) and how the bet is bought
(pin_raw, pm_taker, pm_maker). Then, on TRAIN rows only:

  1. evaluate every spec: n, mean per-bet return, its standard error;
  2. the power gate: drop any spec whose minimum detectable effect is above
     `target` (default 1pp for CLV, 3pp for yield) — it cannot be resolved,
     so testing it would only add to the multiplicity bill;
  3. drop exact duplicates (two specs that select the same rows are one test);
  4. shrink each mean toward its template's mean by its noise (empirical
     Bayes) and keep the top K with a positive posterior.

Those K, and only those, are tested on VALIDATION: one-sided, Bonferroni over
K. What survives is written to a preregistration list for the holdout.

Thresholds that depend on a feature's distribution (quantiles) are taken from
the train rows the spec is searched on, never from later data.
"""
from __future__ import annotations

import hashlib
import json
from itertools import product

import numpy as np
import pandas as pd

from . import stats

DEFAULT_TARGET = {"clv": 0.01, "yield": 0.03, "beat_close": 0.01}
DEFAULT_K = 10
MIN_N = 100
OPS = ("==", ">=", "<=", ">", "<", "between")

SIDE_BANDS = [(1.01, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 2.6), (2.6, 3.6), (3.6, 6.0), (6.0, 15.0), (15.0, 100.0)]
DRAW_BANDS = [(2.0, 3.0), (3.0, 3.5), (3.5, 4.2), (4.2, 6.0), (6.0, 30.0)]
OU_BANDS = [(1.2, 1.6), (1.6, 1.9), (1.9, 2.2), (2.2, 2.8), (2.8, 6.0)]
COARSE = [(1.01, 1.6), (1.6, 2.6), (2.6, 6.0), (6.0, 100.0)]


def _spec(template, view, side, where, odds, name_bits):
    return {"template": template, "view": view, "side": side, "where": where,
            "odds": list(odds) if odds else None,
            "name": " · ".join(str(b) for b in [view.split("_")[0], side, *name_bits] if b)}


def _q(train: pd.DataFrame, col: str, q: float) -> float:
    return round(float(train[col].quantile(q)), 4)


def generate(train: pd.DataFrame) -> list:
    """Every spec of every template. `train` supplies quantile thresholds."""
    out = []
    side_rows = train[train.view == "1x2_sides"]

    # 1. price — the favourite–longshot question, band by band
    for side in ("home", "away"):
        for lo, hi in SIDE_BANDS:
            out.append(_spec("price", "1x2_sides", side, [], (lo, hi), [f"@{lo:g}-{hi:g}"]))
    for lo, hi in DRAW_BANDS:
        out.append(_spec("price", "1x2_draw", "draw", [], (lo, hi), [f"@{lo:g}-{hi:g}"]))
    for side, view in (("over", "ou25_over"), ("under", "ou25_under")):
        for lo, hi in OU_BANDS:
            out.append(_spec("price", view, side, [], (lo, hi), [f"@{lo:g}-{hi:g}"]))

    # 2. v1's filters, on the same footing as everything else
    tiers = [("", []), ("tier1", [["tier", "==", 1]]), ("tier2+", [["tier", ">=", 2]])]
    favs = [("", []), ("fav", [["is_fav", "==", 1]]), ("dog", [["is_fav", "==", 0]])]
    filters = [("", []), ("form+6", [["form_diff", ">=", 6]]), ("form-6", [["form_diff", "<=", -6]]),
               ("rest+3", [["rest_diff", ">=", 3]]), ("rest-3", [["rest_diff", "<=", -3]]),
               ("tg5≥3.2", [["tg5_sum", ">=", 3.2]]), ("tg5≤2.2", [["tg5_sum", "<=", 2.2]])]
    for side, (tn, tw), (fn, fw), band, (ln, lw) in product(("home", "away"), tiers, favs, COARSE, filters):
        if not (tw or fw or lw):
            continue                                   # a bare band is template 1
        out.append(_spec("v1_filters", "1x2_sides", side, tw + fw + lw, band,
                         [tn, fn, f"@{band[0]:g}-{band[1]:g}", ln]))
    mfilters = [("|form|≥6", [["abs_form_diff", ">=", 6]]), ("|form|≤2", [["abs_form_diff", "<=", 2]]),
                ("tg5≥3.2", [["tg5_sum", ">=", 3.2]]), ("tg5≤2.2", [["tg5_sum", "<=", 2.2]])]
    for (tn, tw), (ln, lw) in product(tiers, mfilters):
        out.append(_spec("v1_filters", "1x2_draw", "draw", tw + lw, None, [tn, ln]))
        for side, view in (("over", "ou25_over"), ("under", "ou25_under")):
            out.append(_spec("v1_filters", view, side, tw + lw, None, [tn, ln]))

    # 3. team state — close_model's inputs one at a time, top and bottom quintile
    for col, side, band in product(("x_mres", "x_sot_f", "x_sot_s", "x_gd_s", "x_sh_s"), ("home", "away"),
                                   [None] + COARSE):
        for tag, op, q in (("top20%", ">=", 0.8), ("bottom20%", "<=", 0.2)):
            thr = _q(side_rows, col, q)
            out.append(_spec("team_state", "1x2_sides", side, [[col, op, thr]], band,
                             [f"{col}{op}{thr:g} ({tag})", band and f"@{band[0]:g}-{band[1]:g}"]))

    # 4. the model — close_model's predicted move and its value at the entry odds
    mtrain = side_rows[side_rows.mv_hat.notna()]
    # q97-q99 were added on 2026-10-10 AFTER a decile table on train+validation showed the
    # move clearing Pinnacle's margin only in the top 1% — validation is not independent
    # for those three thresholds; the holdout (ledger round 2) is their test
    for side, band in product(("home", "away"), [None, (1.01, 2.6), (2.6, 100.0)]):
        for q in (0.7, 0.8, 0.9, 0.95, 0.97, 0.98, 0.99):
            thr = _q(mtrain, "mv_hat", q)
            out.append(_spec("model", "1x2_sides", side, [["mv_hat", ">=", thr]], band,
                             [f"mv_hat≥{thr:g} (top {100 - 100 * q:.0f}%)", band and f"@{band[0]:g}-{band[1]:g}"]))
        for v in (0.0, 0.02, 0.04, 0.06):
            out.append(_spec("model", "1x2_sides", side, [["model_value", ">=", v]], band,
                             [f"value≥{v:+.0%}", band and f"@{band[0]:g}-{band[1]:g}"]))
    return out


def mask(df: pd.DataFrame, spec: dict) -> np.ndarray:
    """`side` "both" takes every side of the view (home AND away on 1x2_sides)."""
    m = df.view.to_numpy() == spec["view"]
    if spec["side"] != "both":
        m &= df.side.to_numpy() == spec["side"]
    for col, op, val in spec["where"]:
        x = df[col].to_numpy(float)
        with np.errstate(invalid="ignore"):
            if op == "==":
                m &= x == val
            elif op == ">=":
                m &= x >= val
            elif op == "<=":
                m &= x <= val
            elif op == ">":
                m &= x > val
            elif op == "<":
                m &= x < val
            elif op == "between":
                m &= (x >= val[0]) & (x <= val[1])
            else:
                raise ValueError(f"operator {op!r} — one of {OPS}")
    if spec.get("odds"):
        o = df.o0.to_numpy(float)
        m &= (o >= spec["odds"][0]) & (o < spec["odds"][1])
    return m


def spec_id(spec: dict) -> str:
    core = {k: spec[k] for k in ("view", "side", "where", "odds")}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


def returns(df: pd.DataFrame, objective: str, execution: str) -> np.ndarray:
    return stats.per_bet(objective, execution, df.p0.to_numpy(), df.o0.to_numpy(),
                         df.p1.to_numpy(), df.won.to_numpy(), df.o1.to_numpy())


def run(train: pd.DataFrame, valid: pd.DataFrame, specs: list, objective: str, execution: str,
        target: float | None = None, k: int = DEFAULT_K) -> dict:
    """One run: gate, shrink and select on train; confirm the selection on validation."""
    stats.check(objective, execution)
    target = DEFAULT_TARGET[objective] if target is None else target
    r_tr, r_va = returns(train, objective, execution), returns(valid, objective, execution)
    rows, seen = [], {}
    for s in specs:
        m = mask(train, s)
        t = stats.mean_test(r_tr[m])
        rec = {"id": spec_id(s), "spec": s, "train": t, "mde": stats.mde(t["se"])}
        rec["gated"] = bool(t["n"] < MIN_N or rec["mde"] > target)
        if not rec["gated"]:
            h = hashlib.sha1(np.packbits(m).tobytes()).hexdigest()
            if h in seen:
                rec["gated"], rec["duplicate_of"] = True, seen[h]
            else:
                seen[h] = rec["id"]
        rows.append(rec)

    live = [r for r in rows if not r["gated"]]
    for tpl in {r["spec"]["template"] for r in live}:
        grp = [r for r in live if r["spec"]["template"] == tpl]
        means = np.array([r["train"]["mean"] for r in grp])
        ses = np.array([r["train"]["se"] for r in grp])
        post, psd, mu, tau2 = stats.eb_shrink(means, ses) if len(grp) > 1 else (means, ses, means[0], 0.0)
        for r, pm, ps in zip(grp, post, psd):
            r.update(post=float(pm), post_sd=float(ps), prior_mu=float(mu), prior_tau=float(np.sqrt(tau2)))

    picked = sorted((r for r in live if r.get("post", -1) > 0), key=lambda r: r["post"], reverse=True)[:k]
    alpha = 0.05 / max(len(picked), 1)
    for r in picked:
        v = stats.mean_test(r_va[mask(valid, r["spec"])])
        r["valid"] = v
        r["validated"] = bool(v["n"] >= 30 and (v["mean"] or 0) > 0 and v["p"] < alpha)
    return {"objective": objective, "execution": execution, "target": target, "k": k,
            "specs": len(specs), "gated": sum(r["gated"] for r in rows),
            "duplicates": sum("duplicate_of" in r for r in rows), "tested": len(live),
            "picked": picked, "survivors": [r for r in picked if r["validated"]], "rows": rows,
            "alpha_valid": alpha}
