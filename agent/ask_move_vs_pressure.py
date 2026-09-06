#!/usr/bin/env python3
"""Does PM's ask MOVEMENT predict the next goal better than our pressure index?

The question came out of Liverpool 1-2 Nottingham Forest (2026-08-29): the ask on
the one-more-goal line fell 0.60 -> 0.51 between 75' and 81' while our index read
16/100, and the goal came at ~82'. That is one fixture, i.e. nothing. This
measures it on the whole recorded book.

Design, and why each piece is there:

  * FREE STATE is minute + goals_total + pre_over25. Anything a signal "predicts"
    that these already carry is not information, and reading a raw correlation
    here is how [[finding-fav-behind-not-noise]] went wrong.
  * The ask LEVEL is already known to beat our index ([[finding-live-reading-
    ceiling]]: ~16x the information). The new question is whether the MOVEMENT
    adds anything ON TOP OF THE LEVEL. If PM's book is a martingale at this
    horizon -- which [[finding-microstructure-maker-edge]] found on 298k ticks --
    the delta should add ~nothing, and that is a real answer, not a null result.
  * Deltas are computed within a constant (fixture, target_line, goals_total)
    segment. Across a goal the line re-bases and the "movement" would be an
    artifact of the line changing, not of the market moving.
  * Rows are 60s polls with overlapping 10-minute outcome windows, so n=17,933 is
    NOT 17,933 independent observations. Every CI here is a BOOTSTRAP OVER
    FIXTURES (473 of them), which is the unit that is actually independent.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from tools.db import _conn

LAGS = (3, 5, 10)
SEED = 20260829


def load() -> pd.DataFrame:
    q = """
      SELECT fixture_id, minute, goals_total, target_line, pre_over25,
             best_ask, best_bid, pressure_index, goal_next_10, observed_at
        FROM pressure_observations
       WHERE goal_next_10 IS NOT NULL
         AND best_ask IS NOT NULL
         AND pressure_index IS NOT NULL
         AND COALESCE(stats_frozen, false) = false
         AND minute BETWEEN 20 AND 88
         AND pre_over25 IS NOT NULL
    """
    with _conn() as c:
        df = pd.read_sql(q, c)
    # One row per fixture-minute: polls repeat within a minute and would otherwise
    # weight a slow minute more than a fast one.
    df = (df.sort_values("observed_at")
            .drop_duplicates(subset=["fixture_id", "minute"], keep="last")
            .reset_index(drop=True))
    for col in ("best_ask", "best_bid", "pressure_index", "pre_over25",
                "target_line", "goals_total"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["y"] = df["goal_next_10"].astype(int)
    return df


def add_deltas(df: pd.DataFrame) -> pd.DataFrame:
    """Ask movement over the previous k minutes, within one line segment."""
    df = df.sort_values(["fixture_id", "minute"]).copy()
    # A segment is a stretch with the same line AND the same score. Either changing
    # re-bases what the ask means.
    seg = ["fixture_id", "target_line", "goals_total"]
    for k in LAGS:
        prev = (df.set_index(seg + ["minute"])
                  .index.to_frame(index=False)
                  .assign(minute=lambda d: d["minute"] + k))
        lookup = df.set_index(seg + ["minute"])["best_ask"]
        idx = pd.MultiIndex.from_frame(
            df[seg].assign(minute=df["minute"] - k))
        df[f"ask_lag{k}"] = lookup.reindex(idx).to_numpy()
        df[f"dask{k}"] = df["best_ask"] - df[f"ask_lag{k}"]
    df["spread"] = df["best_ask"] - df["best_bid"]
    return df


def mcfadden(X: np.ndarray, y: np.ndarray) -> float:
    """McFadden pseudo-R^2 of a logit fit, vs the intercept-only model."""
    if X.shape[1] == 0:
        p = np.clip(y.mean(), 1e-9, 1 - 1e-9)
        ll = (y * np.log(p) + (1 - y) * np.log(1 - p)).sum()
        return 0.0, ll
    m = LogisticRegression(max_iter=5000, C=1e6, solver="lbfgs")
    m.fit(X, y)
    p = np.clip(m.predict_proba(X)[:, 1], 1e-12, 1 - 1e-12)
    ll = (y * np.log(p) + (1 - y) * np.log(1 - p)).sum()
    p0 = np.clip(y.mean(), 1e-9, 1 - 1e-9)
    ll0 = (y * np.log(p0) + (1 - y) * np.log(1 - p0)).sum()
    return 1 - ll / ll0, ll


def fit_set(d: pd.DataFrame, cols: list[str]) -> float:
    X = d[cols].to_numpy(dtype=float) if cols else np.empty((len(d), 0))
    if cols:
        X = (X - X.mean(0)) / np.where(X.std(0) == 0, 1, X.std(0))
    r2, _ = mcfadden(X, d["y"].to_numpy())
    return r2


def main() -> None:
    df = add_deltas(load())
    print(f"rows={len(df):,}  fixtures={df.fixture_id.nunique()}  "
          f"base rate P(goal in next 10) = {df.y.mean():.3f}")

    k = 5
    d = df.dropna(subset=[f"dask{k}", "best_ask", "pressure_index",
                          "pre_over25", "minute", "goals_total"]).copy()
    print(f"\nwith a {k}-minute ask delta inside one line segment: "
          f"rows={len(d):,}  fixtures={d.fixture_id.nunique()}")

    BASE = ["minute", "goals_total", "pre_over25"]
    models = {
        "M0  free state (minute, score, pre-match total)": BASE,
        "M1  + our pressure index":                        BASE + ["pressure_index"],
        "M2  + PM ask LEVEL":                              BASE + ["best_ask"],
        f"M3  + ask level + ask MOVE ({k}min)":            BASE + ["best_ask", f"dask{k}"],
        f"M4  + level + move + pressure":                   BASE + ["best_ask", f"dask{k}", "pressure_index"],
    }
    r2 = {name: fit_set(d, cols) for name, cols in models.items()}
    print("\n--- McFadden pseudo-R^2 (in-sample fit) ---")
    prev = None
    for name, v in r2.items():
        inc = "" if prev is None else f"   Δ vs M0 = {v - r2[list(models)[0]]:+.5f}"
        print(f"  {name:52s} {v:.5f}{inc}")
        prev = v
    print(f"\n  the question: ask MOVE on top of ask LEVEL   "
          f"Δ = {r2[f'M3  + ask level + ask MOVE ({k}min)'] - r2['M2  + PM ask LEVEL']:+.5f}")
    print(f"  our pressure on top of level+move            "
          f"Δ = {r2[f'M4  + level + move + pressure'] - r2[f'M3  + ask level + ask MOVE ({k}min)']:+.5f}")

    # ---- OUT-OF-SAMPLE, grouped by fixture ----
    # The in-sample increments above are >= 0 BY CONSTRUCTION: a nested logit can
    # never fit worse with one more column, so a bootstrap of them has a lower
    # bound pinned at zero and "clear of zero" would mean nothing. The only test
    # that can come back negative -- and therefore the only one worth reading --
    # is held-out prediction, with whole fixtures held out so a poll never
    # predicts its own neighbours.
    from sklearn.model_selection import GroupKFold

    def oos_losses(cols: list[str]) -> np.ndarray:
        """Per-row held-out log loss."""
        y = d["y"].to_numpy()
        g = d["fixture_id"].to_numpy()
        out = np.full(len(d), np.nan)
        for tr, te in GroupKFold(n_splits=5).split(d, y, groups=g):
            if not cols:
                p = np.full(len(te), np.clip(y[tr].mean(), 1e-9, 1 - 1e-9))
            else:
                X = d[cols].to_numpy(dtype=float)
                mu, sd = X[tr].mean(0), X[tr].std(0)
                sd = np.where(sd == 0, 1, sd)
                Xs = (X - mu) / sd
                m = LogisticRegression(max_iter=5000, C=1.0, solver="lbfgs")
                m.fit(Xs[tr], y[tr])
                p = np.clip(m.predict_proba(Xs[te])[:, 1], 1e-12, 1 - 1e-12)
            out[te] = -(y[te] * np.log(p) + (1 - y[te]) * np.log(1 - p))
        return out

    L = {name: oos_losses(cols) for name, cols in models.items()}
    L["M_null intercept only"] = oos_losses([])
    print("\n--- OUT-OF-SAMPLE mean log loss (5-fold, whole fixtures held out) ---")
    print(f"  {'M_null intercept only':52s} {L['M_null intercept only'].mean():.5f}")
    for name in models:
        print(f"  {name:52s} {L[name].mean():.5f}")

    m0 = "M0  free state (minute, score, pre-match total)"
    m1 = "M1  + our pressure index"
    m2 = "M2  + PM ask LEVEL"
    m3 = f"M3  + ask level + ask MOVE ({k}min)"
    m4 = "M4  + level + move + pressure"

    rng = np.random.default_rng(SEED)
    fx = d.fixture_id.unique()
    idx_by_fx = {f: np.where(d.fixture_id.to_numpy() == f)[0] for f in fx}

    def boot(a_name, b_name, label):
        """Cluster bootstrap of the mean log-loss IMPROVEMENT of b over a.
        Positive = b predicts better."""
        diff = L[a_name] - L[b_name]
        reps = []
        for _ in range(2000):
            pick = rng.choice(fx, size=len(fx), replace=True)
            reps.append(np.concatenate([diff[idx_by_fx[f]] for f in pick]).mean())
        reps = np.array(reps)
        lo, hi = np.percentile(reps, [2.5, 97.5])
        verdict = "REAL" if lo > 0 else ("WORSE" if hi < 0 else "INCLUDES ZERO")
        print(f"  {label:46s} {diff.mean():+.5f}  CI95 [{lo:+.5f}, {hi:+.5f}]  {verdict}")

    print(f"\n--- improvement in held-out log loss, cluster-bootstrapped over "
          f"{len(fx)} fixtures (2000 resamples) ---")
    print("    positive = predicts the next 10 minutes better\n")
    boot(m0, m1, "our pressure index, over free state")
    boot(m0, m2, "PM ask LEVEL, over free state")
    boot(m2, m3, f"ask MOVE ({k}min), over the LEVEL   <- the question")
    boot(m3, m4, "our pressure, over level + move")

    # ---- robustness: is the answer specific to the 5-minute lag? ----
    print("\n--- same test at every lag (a signal should not live at one only) ---")
    from sklearn.model_selection import GroupKFold as _GKF
    for kk in LAGS:
        dk = df.dropna(subset=[f"dask{kk}", "best_ask", "pressure_index",
                               "pre_over25", "minute", "goals_total"]).copy()
        if len(dk) < 500:
            print(f"  lag {kk:>2}min: only {len(dk)} rows — skipped")
            continue
        yk = dk["y"].to_numpy(); gk = dk["fixture_id"].to_numpy()
        def oos(cols):
            out = np.full(len(dk), np.nan)
            for tr, te in _GKF(n_splits=5).split(dk, yk, groups=gk):
                X = dk[cols].to_numpy(dtype=float)
                mu, sd = X[tr].mean(0), X[tr].std(0); sd = np.where(sd == 0, 1, sd)
                Xs = (X - mu) / sd
                m = LogisticRegression(max_iter=5000, C=1.0, solver="lbfgs")
                m.fit(Xs[tr], yk[tr])
                pp = np.clip(m.predict_proba(Xs[te])[:, 1], 1e-12, 1 - 1e-12)
                out[te] = -(yk[te] * np.log(pp) + (1 - yk[te]) * np.log(1 - pp))
            return out
        base = ["minute", "goals_total", "pre_over25", "best_ask"]
        diff = oos(base) - oos(base + [f"dask{kk}"])
        fxk = dk.fixture_id.unique()
        ix = {f: np.where(gk == f)[0] for f in fxk}
        rg = np.random.default_rng(SEED)
        reps = np.array([np.concatenate([diff[ix[f]] for f in
                          rg.choice(fxk, size=len(fxk), replace=True)]).mean()
                         for _ in range(800)])
        lo, hi = np.percentile(reps, [2.5, 97.5])
        v = "REAL" if lo > 0 else ("WORSE" if hi < 0 else "zero")
        print(f"  lag {kk:>2}min  n={len(dk):>6} fx={len(fxk):>4}  "
              f"move over level = {diff.mean():+.5f}  CI95 [{lo:+.5f}, {hi:+.5f}]  {v}")

    # ---- does the move at least separate the outcome on its own? ----
    print(f"\n--- raw separation: mean {k}-min ask move, by what happened next ---")
    g = d.groupby("y")[f"dask{k}"].agg(["count", "mean", "std"])
    for yv, row in g.iterrows():
        lbl = "goal in next 10" if yv == 1 else "no goal"
        print(f"  {lbl:18s} n={int(row['count']):>6}  mean move={row['mean']:+.4f}")
    print(f"\n--- and our index, same split ---")
    g2 = d.groupby("y")["pressure_index"].agg(["count", "mean"])
    for yv, row in g2.iterrows():
        lbl = "goal in next 10" if yv == 1 else "no goal"
        print(f"  {lbl:18s} n={int(row['count']):>6}  mean pressure={row['mean']:.2f}")


if __name__ == "__main__":
    main()
