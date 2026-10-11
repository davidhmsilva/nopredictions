"""
tape.py — the pre-match tape factory v2 runs on.

One row per match × market × side, 22+ leagues, 2012-07 → 2026-01-14 (the
last Pinnacle close Football-Data published):

    p0 / o0   Pinnacle PRE-close (Football-Data's PS columns, collected 1-3
              days before kick-off): the price a bet is placed at
    p1 / o1   Pinnacle close (PSC): the benchmark a CLV is measured against
    won       the result
    move      logit(p1) − logit(p0): where the price went after the entry

Probabilities are de-vigged with the POWER method (close_model.devig). The
proportional split v1 used overstates longshots, which manufactures exactly
the favourite–longshot bias a family test would then "discover".

Features, every one known before the entry:
    v1's       form_diff, rest_diff, tg5_sum, tier, month, is_fav
    team state close_model's EWMAs from previous league matches (gd, shots on
               target, shots, points vs the price, rest), as side − opponent
    model      close_model's walk-forward predicted move (mv_hat) and its
               value at the entry odds (model_value), from seasons < this one.
               Only 2015+ and only home/away: the draw is not modelled.

Entry time. Football-Data collects its pre-close odds "Friday afternoons for
weekend games, and on Tuesday afternoons for midweek games" (notes.txt), and
kickoff_utc carries the London clock (finding_fd_kickoff_london_time). So
`hours_before` = kick-off − 15:00 London on that Friday (Fri-Mon games) or
Tuesday (Tue-Thu): Saturday 15:00 ≈ 24h, Sunday ≈ 48h, Monday ≈ 72h. "Afternoon"
is not a time, so it is an estimate, and it was checked rather than assumed:
the pre-close → close move grows with it (|move| 1.9pp <12h, 2.3pp ~24h,
2.5pp ~48h, 2.6pp ~72h, train+validation). Games before 15:00 on collection
day get NaN.

Splits are by season and fixed in code, so no search can move them:
    1x2    train 2012-18 · validation 2019-21 · holdout 2022+
    ou25   train 2019-20 · validation 2021    · holdout 2022+   (Pinnacle O/U
           prices start in 2019 in our data)
The holdout is the same calendar for every market, and only protocol.confirm
is meant to read it.
"""
from __future__ import annotations

import logging
import os
import pickle
import sys
import time
import warnings

import numpy as np
import pandas as pd
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, AGENT)
load_dotenv(os.path.join(AGENT, "../ingest/.env"))

import close_model as cm                                          # noqa: E402
import db_txn                                                     # noqa: E402

log = logging.getLogger("factory.v2")
warnings.filterwarnings("ignore", message=".*pandas only supports SQLAlchemy.*")

CACHE = os.path.join(AGENT, "factory", "cache", "v2_prematch.pkl")
HOLDOUT_FROM = 2022
SPLITS = {"1x2": {"train": (2012, 2018), "validation": (2019, 2021)},
          "ou25": {"train": (2019, 2020), "validation": (2021, 2021)}}
VIEWS = {"1x2_sides": ("1x2", ("home", "away")), "1x2_draw": ("1x2", ("draw",)),
         "ou25_over": ("ou25", ("over",)), "ou25_under": ("ou25", ("under",))}
TEAM = [f"x_{k}" for k in cm.TERMS]          # x_gd_f … x_rest, side − opponent


def connect():
    """A short job: Supavisor's transaction port, not the session port the
    daemons hold (see venue_open_recorder)."""
    return db_txn.connect(os.environ["DATABASE_URL"].replace(":5432/", ":6543/"))


def split_of(market: str, season: np.ndarray) -> np.ndarray:
    s = np.asarray(season, float)
    out = np.full(s.shape, None, dtype=object)
    for name, (a, b) in SPLITS[market].items():
        out[(s >= a) & (s <= b)] = name
    out[s >= HOLDOUT_FROM] = "holdout"
    return out


COLLECT_HOUR = 15          # Football-Data's "afternoon", London clock


def entry_hours(ts: pd.Series) -> np.ndarray:
    """Hours between Football-Data's collection and kick-off (see the module doc)."""
    ko = pd.to_datetime(ts, utc=True).dt.tz_convert("UTC").dt.tz_localize(None)   # the London clock
    wd = ko.dt.dayofweek
    back = np.select([wd == 4, wd == 5, wd == 6, wd == 0, wd == 1, wd == 2, wd == 3], [0, 1, 2, 3, 0, 1, 2])
    coll = ko.dt.normalize() - pd.to_timedelta(back, unit="D") + pd.Timedelta(hours=COLLECT_HOUR)
    h = ((ko - coll).dt.total_seconds() / 3600).to_numpy(float)
    return np.where(h > 0, h, np.nan)


def _flt(s) -> np.ndarray:
    return pd.to_numeric(pd.Series(s), errors="coerce").to_numpy(float)


def _team_state(conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per match: both teams' state before kick-off; per side: the walk-forward
    model's predicted move and probability."""
    t = time.time()
    f, _ = cm.walk(cm.load_matches(conn))
    log.info(f"team state walked over {len(f):,} matches in {time.time() - t:.0f}s")
    keep = ["match_id", "league", "h_n", "a_n"] + [f"{s}_{k}" for s in ("h", "a") for k in cm.TERMS]
    state = f[keep].copy()
    t = time.time()
    rows = cm.side_rows(f, "pre")
    rows = rows[rows.cl_bk == cm.BK_PIN_CLOSE]
    wf = cm.walk_forward(rows, cm.FEATURES)
    log.info(f"walk-forward model over {wf.season.nunique()} seasons in {time.time() - t:.0f}s")
    return state, wf[["match_id", "is_home", "mv_hat", "q_both"]]


def build(conn) -> pd.DataFrame:
    bt = pd.read_sql_query("""
        SELECT match_id, league_code, tier, season_start, kickoff_utc, total_goals, result,
               ph_open, pd_open, pa_open, ph_close, pd_close, pa_close,
               over25_open, under25_open, over25_close, under25_close,
               home_rest_days, away_rest_days, home_form_pts5, away_form_pts5,
               home_avg_tg5, away_avg_tg5
          FROM bt_features""", conn)
    state, wf = _team_state(conn)
    bt = bt.merge(state, on="match_id", how="left")

    o_open = bt[["ph_open", "pd_open", "pa_open"]].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    o_close = bt[["ph_close", "pd_close", "pa_close"]].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    u_open = bt[["over25_open", "under25_open"]].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    u_close = bt[["over25_close", "under25_close"]].apply(pd.to_numeric, errors="coerce").to_numpy(float)

    def devig(o):
        p = np.full(o.shape, np.nan)
        ok = np.isfinite(o).all(1) & (o > 1).all(1)
        if ok.any():
            p[ok] = cm.devig(o[ok])
        return p

    P0, P1, Q0, Q1 = devig(o_open), devig(o_close), devig(u_open), devig(u_close)
    res = bt.result.astype(str).str.strip().to_numpy()
    tg = _flt(bt.total_goals)
    form = _flt(bt.home_form_pts5) - _flt(bt.away_form_pts5)
    rest = _flt(bt.home_rest_days) - _flt(bt.away_rest_days)
    tg5 = _flt(bt.home_avg_tg5) + _flt(bt.away_avg_tg5)
    ko = pd.to_datetime(bt.kickoff_utc, utc=True)
    season = _flt(bt.season_start)
    has_team = ((_flt(bt.h_n) >= cm.MIN_GAMES) & (_flt(bt.a_n) >= cm.MIN_GAMES)
                & bt.league_code.isin(cm.STATS_LEAGUES).to_numpy())
    hx = {k: np.where(has_team, _flt(bt[f"h_{k}"]) - _flt(bt[f"a_{k}"]), np.nan) for k in cm.TERMS}

    parts = []
    for market, side, p0, p1, o0, o1, won, sign in (
            ("1x2", "home", P0[:, 0], P1[:, 0], o_open[:, 0], o_close[:, 0], res == "H", 1.0),
            ("1x2", "draw", P0[:, 1], P1[:, 1], o_open[:, 1], o_close[:, 1], res == "D", np.nan),
            ("1x2", "away", P0[:, 2], P1[:, 2], o_open[:, 2], o_close[:, 2], res == "A", -1.0),
            ("ou25", "over", Q0[:, 0], Q1[:, 0], u_open[:, 0], u_close[:, 0], tg >= 3, np.nan),
            ("ou25", "under", Q0[:, 1], Q1[:, 1], u_open[:, 1], u_close[:, 1], tg <= 2, np.nan)):
        oriented = np.isfinite(sign)
        o = sign if oriented else 1.0            # match-level rows read home − away
        d = pd.DataFrame({
            "match_id": bt.match_id.to_numpy(), "ts": ko, "league_code": bt.league_code.to_numpy(),
            "tier": _flt(bt.tier), "season": season, "month": ko.dt.month.to_numpy(float),
            "market": market, "side": side, "p0": p0, "p1": p1, "o0": o0, "o1": o1,
            "won": won.astype(float),
            "is_home": (1.0 if side == "home" else 0.0) if oriented else np.nan,
            "form_diff": o * form, "abs_form_diff": np.abs(form),
            "rest_diff": o * rest, "abs_rest_diff": np.abs(rest), "tg5_sum": tg5,
        })
        if market == "1x2" and oriented:
            other = P0[:, 2] if side == "home" else P0[:, 0]
            d["is_fav"] = (p0 > other).astype(float)
        else:
            d["is_fav"] = np.nan
        for k in cm.TERMS:
            d[f"x_{k}"] = o * hx[k]
        for k in ("gd_s", "sot_s", "mres"):
            d[f"ax_{k}"] = np.abs(hx[k])
        d["split"] = split_of(market, season)
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)       # bt_features has a result for every row

    wf =wf.assign(side=np.where(wf.is_home == 1, "home", "away")).drop(columns="is_home")
    df = df.merge(wf, on=["match_id", "side"], how="left")
    df["model_value"] = df.q_both * df.o0 - 1.0

    with np.errstate(invalid="ignore", divide="ignore"):
        df["l0"], df["l1"] = cm.logit(df.p0.to_numpy()), cm.logit(df.p1.to_numpy())
    df.loc[~np.isfinite(df.p0), "l0"] = np.nan
    df.loc[~np.isfinite(df.p1), "l1"] = np.nan
    df["move"] = df.l1 - df.l0
    df["hours_before"] = entry_hours(df.ts)
    df["hb_days"] = df.hours_before / 24.0 - 1.0          # centred on a Saturday's ~24h
    df["mv_x_hb"] = df.mv_hat * df.hb_days
    df["view"] = None
    for view, (market, sides) in VIEWS.items():
        df.loc[(df.market == market) & df.side.isin(sides), "view"] = view
    df = df[np.isfinite(df.p0) & np.isfinite(df.p1) & np.isfinite(df.o0) & df.split.notna()]
    return df.sort_values(["ts", "match_id", "market", "side"]).reset_index(drop=True)


def load(conn=None, refresh: bool = False) -> pd.DataFrame:
    if not refresh and os.path.exists(CACHE):
        with open(CACHE, "rb") as fh:
            return pickle.load(fh)
    df = build(conn or connect())
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as fh:
        pickle.dump(df, fh)
    return df


def summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["view", "split"])
    return pd.DataFrame({"rows": g.size(), "matches": g.match_id.nunique(),
                         "seasons": g.season.agg(lambda s: f"{int(s.min())}-{int(s.max())}"),
                         "team_state": g.x_gd_s.apply(lambda s: s.notna().mean()).round(2),
                         "hours_before": g.hours_before.median().round(0),
                         "model": g.mv_hat.apply(lambda s: s.notna().mean()).round(2)})
