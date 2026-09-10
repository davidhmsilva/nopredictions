"""xg_proxy_fit.py — refit the box-score xG estimate used by live_tracker.

api-football stopped sending xG on 2026-09-02 (every league on the same day,
while shots, corners and possession kept arriving). xG is 40% of the danger
index, so `live_tracker.estimate_xg` rebuilds it from the shot counts the feed
still publishes. This script is where its coefficients come from:

  - TARGET  api-football's own live xG, on the fixtures where it still sent it
            (same feed, same stat definitions as the counts it is fitted on)
  - FIT     non-negative least squares through the origin, stacked home/away
  - SCORE   out-of-sample by fixture (5 folds), cumulative and 15-minute window
  - CHECK   the danger index recomputed with real / estimated / renormalised xG
            on the same rows, and the gates (45, 19) each one would pass
  - CROSS   32k historical team-matches (Understat/FBref xG vs Football-Data
            shots) on the ESPN-shaped terms, as an outside check on the shape

    cd agent && source ../ingest/.venv/bin/activate
    python xg_proxy_fit.py

Prints the numbers; writes nothing. Copy the coefficients into live_tracker's
_XGE_* constants by hand — the weights under test are never changed silently.
"""
import os
import sys

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from scipy.optimize import nnls

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_tracker import _W_XG, danger_index  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

LIVE_SQL = """
with fx as (select distinct fixture_id from pressure_observations
             where coalesce(home_xg,0)+coalesce(away_xg,0) > 0)
select distinct on (p.fixture_id, p.minute)
       p.fixture_id, p.minute, p.home_xg, p.away_xg,
       p.home_shots_on, p.away_shots_on, p.home_shots_total, p.away_shots_total,
       p.home_shots_inside, p.away_shots_inside, p.home_corners, p.away_corners,
       p.home_possession, p.away_possession
  from pressure_observations p join fx using (fixture_id)
 where p.home_shots_total is not null and p.home_xg is not null
 order by p.fixture_id, p.minute, p.observed_at desc
"""

HIST_SQL = """
select home_xg, away_xg, home_shots, away_shots, home_shots_on_tgt, away_shots_on_tgt,
       home_corners, away_corners
  from match_stats
 where home_xg is not null and home_shots is not null
   and home_shots_on_tgt is not null and home_corners is not null
"""

VARIANTS = {
    "full": ["on", "ins", "out", "cor"],      # api-football: shots-in-box available
    "espn": ["on", "off", "cor"],             # ESPN: no shots-in-box
}


def _stack(df: pd.DataFrame) -> pd.DataFrame:
    sides = []
    for s in ("home", "away"):
        sides.append(pd.DataFrame({
            "fixture_id": df.fixture_id, "minute": df.minute, "side": s,
            "xg": df[f"{s}_xg"], "on": df[f"{s}_shots_on"], "tot": df[f"{s}_shots_total"],
            "ins": df[f"{s}_shots_inside"], "cor": df[f"{s}_corners"],
            "poss": df[f"{s}_possession"],
        }))
    out = pd.concat(sides, ignore_index=True)
    out[["xg", "on", "tot", "ins", "cor", "poss"]] = out[
        ["xg", "on", "tot", "ins", "cor", "poss"]].astype(float)
    out = out[(out.tot >= out.on) & (out.tot >= out.ins)].copy()
    out["off"] = out.tot - out.on
    out["out"] = out.tot - out.ins
    return out


def _r2(y, p) -> float:
    return float(1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum())


def fit_live(L: pd.DataFrame) -> dict:
    fixtures = L.fixture_id.unique()
    np.random.default_rng(7).shuffle(fixtures)
    folds = np.array_split(fixtures, 5)
    coefs = {}
    for name, cols in VARIANTS.items():
        pred = pd.Series(np.nan, index=L.index)
        for f in folds:
            te = L.fixture_id.isin(f)
            c, _ = nnls(L.loc[~te, cols].values, L.loc[~te, "xg"].values)
            pred[te] = L.loc[te, cols].values @ c
        coefs[name], _ = nnls(L[cols].values, L.xg.values)
        L[f"pred_{name}"] = pred
        late = L.minute >= 80
        print(f"\n{name}: coef {dict(zip(cols, np.round(coefs[name], 4)))}")
        print(f"  OOS cumulative R2 {_r2(L.xg, pred):.3f}  MAE {(L.xg - pred).abs().mean():.3f}"
              f"  | 80'+ R2 {_r2(L.xg[late], pred[late]):.3f}")
    return coefs


def windows(L: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (fid, side), g in L.sort_values("minute").groupby(["fixture_id", "side"]):
        g = g.set_index("minute")
        for m in g.index:
            base = g.loc[g.index <= m - 15]
            if m < 20 or base.empty or m - base.index[-1] > 18:
                continue
            b, cur = base.iloc[-1], g.loc[m]
            rows.append(dict(
                fixture_id=fid, side=side, minute=m, poss=cur.poss,
                **{k: cur[k] - b[k] for k in ("xg", "on", "ins", "cor", "pred_full", "pred_espn")}))
    W = pd.DataFrame(rows)
    return W[(W[["xg", "on", "ins", "cor"]] >= -1e-9).all(axis=1)]


def index_check(W: pd.DataFrame) -> None:
    def di(r, xg, **kw):
        return danger_index(r.on, r.ins, xg, r.cor, r.poss, **kw)

    W = W.copy()
    W["real"] = [di(r, r.xg) for r in W.itertuples()]
    W["estimated"] = [di(r, max(r.pred_full, 0)) for r in W.itertuples()]
    # The pre-v5 treatment, spelled out: danger_index itself now ESTIMATES a
    # missing xG, so asking it for has_xg=False no longer gives renormalising.
    W["renormalised"] = [di(r, 0.0) / (1.0 - _W_XG) for r in W.itertuples()]
    W["espn_estimated"] = [di(r, max(r.pred_espn, 0), has_inside=False) for r in W.itertuples()]
    W["espn_renorm"] = [di(r, 0.0, has_xg=False, has_inside=False) for r in W.itertuples()]
    keys = ["real", "estimated", "renormalised", "espn_estimated", "espn_renorm"]
    P = W.pivot_table(index=["fixture_id", "minute"], columns="side", values=keys).dropna()
    pi = pd.DataFrame({k: (P[(k, "home")] + P[(k, "away")]) / 2 for k in keys})
    late = pi.index.get_level_values("minute") >= 75
    print(f"\nindex (mean of sides), {len(pi)} fixture-minutes, {late.sum()} at 75'+")
    print(f"{'':15s} {'MAE':>5s} {'p90 75+':>8s} {'>=45 75+':>9s} {'agree45':>8s} {'>=19':>6s}")
    for k in keys:
        x, y = pi[k], pi.real
        print(f"{k:15s} {(x - y).abs().mean():5.2f} {x[late].quantile(.9):8.1f} "
              f"{(x[late] >= 45).mean():9.3f} {((x >= 45) == (y >= 45)).mean():8.3f} "
              f"{(x >= 19).mean():6.3f}")


def historical(conn) -> None:
    h = pd.read_sql(HIST_SQL, conn)
    H = pd.concat([pd.DataFrame({
        "xg": h[f"{s}_xg"], "on": h[f"{s}_shots_on_tgt"],
        "off": h[f"{s}_shots"] - h[f"{s}_shots_on_tgt"], "cor": h[f"{s}_corners"]})
        for s in ("home", "away")]).astype(float).dropna()
    c, _ = nnls(H[["on", "off", "cor"]].values, H.xg.values)
    p = H[["on", "off", "cor"]].values @ c
    print(f"\nhistorical full-match, {len(H)} team-matches: on/off/cor {np.round(c, 4)}  R2 {_r2(H.xg, p):.3f}")


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    L = _stack(pd.read_sql(LIVE_SQL, conn))
    print(f"live rows {len(L)} (side-minutes), fixtures {L.fixture_id.nunique()}")
    fit_live(L)
    index_check(windows(L))
    historical(conn)


if __name__ == "__main__":
    main()
