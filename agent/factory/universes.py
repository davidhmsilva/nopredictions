"""
universes.py — the tapes a strategy runs on.

A universe is one family of bets for which we know, at the moment a bot would
fire: the Polymarket price it would pay, the state it could see, and later what
the bet paid.

  * Four come from our own in-play recordings, so the price is the REAL CLOB
    ask at that minute, and the live bots read the very same tables the daemons
    keep writing — backtest and paper trading cannot drift apart.
  * Three are pre-match history, where the price is a PROXY: the de-vigged sharp
    close (or open) plus Polymarket's measured half-spread, then the taker fee
    (PM's pre-match mid sits on the de-vigged Pinnacle line —
    finding_pm_mid_is_pinnacle). A proxy universe asks the hardest question
    there is — does the rule beat the sharp price after PM's costs — so a pass
    there is worth more, not less.

Every loader returns the same core columns:
    fixture, ts, minute, ask, bid, depth, token_id, condition_id, won, label,
    source_row_id, market, side
plus the universe's own features. `won` is 1 / 0 / 0.5 (a 50-50 resolution) and
NaN while unsettled. No feature may be something that was not known at `ts`.
"""
from __future__ import annotations

import os
import pickle
import time
import warnings
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
GAMES_CSV = os.path.join(HERE, "../../ingest/.cache/nfl/games.csv")

SOCCER_HALF_SPREAD = 0.006    # PM ask vs de-vigged Pinnacle, before fee: +0.58pp (n=272)
NFL_HALF_SPREAD = 0.005       # NFL main books are one tick wide
PROXY_DEPTH = 1e6

warnings.filterwarnings("ignore", message=".*pandas only supports SQLAlchemy.*")


@dataclass
class Universe:
    name: str
    description: str
    price_source: str                 # 'pm_clob' | 'pinnacle_proxy' | 'book_proxy'
    source_table: Optional[str]
    live: bool                        # can the runner paper-trade it off the live tape?
    features: tuple
    market_sides: dict = field(default_factory=dict)   # market -> allowed sides
    exits: tuple = ("hold",)
    split: str = "q70"                # 'q70' = 70th percentile of fixture dates, or an ISO date
    min_n_train: int = 40
    min_n_test: int = 15
    load: Optional[Callable] = None

    @property
    def markets(self) -> tuple:
        return tuple(self.market_sides)


# ── helpers ──────────────────────────────────────────────────────────────────

def _flt(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype(float)


def _won_bool(s: pd.Series) -> pd.Series:
    return s.map({True: 1.0, False: 0.0}).astype(float)


def _filter(mode: str, live_minutes: int, fixture_ids, settled_sql: str) -> tuple[str, list]:
    if mode == "backtest":
        return f" AND {settled_sql}", []
    if mode == "live":
        return " AND observed_at > now() - (%s * interval '1 minute')", [int(live_minutes)]
    if mode == "settle":
        return f" AND fixture_id = ANY(%s) AND {settled_sql}", [list(fixture_ids or [])]
    raise ValueError(mode)


def _finish(df: pd.DataFrame, dedupe: Optional[list] = None) -> pd.DataFrame:
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for c in ("ask", "bid", "depth", "minute"):
        if c in df:
            df[c] = _flt(df[c])
    df = df.sort_values(["fixture", "ts"], kind="mergesort")
    if dedupe:
        df = df.drop_duplicates(dedupe, keep="first")
    return df.reset_index(drop=True)


_STATS_SQL = """
    COALESCE(home_xg,0)+COALESCE(away_xg,0) AS xg_total,
    COALESCE(home_shots_on,0)+COALESCE(away_shots_on,0) AS shots_on_total,
    COALESCE(home_shots_total,0)+COALESCE(away_shots_total,0) AS shots_total,
    COALESCE(home_corners,0)+COALESCE(away_corners,0) AS corners_total,
    COALESCE(home_reds,0)+COALESCE(away_reds,0) AS reds_total,
    home_possession"""


# ── in-play: real Polymarket prices ──────────────────────────────────────────

def load_next_goal(conn, mode="backtest", live_minutes=4, fixture_ids=None) -> pd.DataFrame:
    """The 'one more goal' over line (goals + 0.5, full match) at every minute."""
    extra, params = _filter(mode, live_minutes, fixture_ids,
                            "settled_at IS NOT NULL AND goal_before_ft IS NOT NULL")
    df = pd.read_sql_query(f"""
        SELECT id AS source_row_id, fixture_id, observed_at AS ts, minute, home_goals, away_goals,
               goals_total, league, event_title, pre_over25, pressure_index,
               GREATEST(home_danger, away_danger) AS max_danger, {_STATS_SQL},
               CASE WHEN has_window THEN COALESCE(home_xg_window,0)+COALESCE(away_xg_window,0) END AS xg_window,
               CASE WHEN has_window THEN COALESCE(home_shots_on_window,0)+COALESCE(away_shots_on_window,0) END AS shots_on_window,
               CASE WHEN has_window THEN COALESCE(home_corners_window,0)+COALESCE(away_corners_window,0) END AS corners_window,
               fair_base, token_id, condition_id, best_bid AS bid, best_ask AS ask,
               ask_depth_usd AS depth, obs_version, goal_before_ft AS won_raw
          FROM pressure_observations
         WHERE best_ask IS NOT NULL AND best_bid IS NOT NULL {extra}""", conn, params=params)
    df["fixture"] = "af:" + df["fixture_id"].astype(str)
    df["goal_diff"] = _flt(df["home_goals"]) - _flt(df["away_goals"])
    df["abs_diff"] = df["goal_diff"].abs()
    df["fair_gap"] = _flt(df["fair_base"]) - _flt(df["ask"])
    df["won"] = _won_bool(df["won_raw"])
    df["label"] = ("Over " + (_flt(df["goals_total"]) + 0.5).map("{:g}".format) + " (next goal) — "
                   + df["event_title"].fillna("?"))
    return _finish(df, dedupe=["fixture", "minute"])


def load_ht_over05(conn, mode="backtest", live_minutes=4, fixture_ids=None) -> pd.DataFrame:
    """1st Half O/U 0.5 — over, while the match is still 0-0."""
    extra, params = _filter(mode, live_minutes, fixture_ids, "goal_before_ht IS NOT NULL")
    df = pd.read_sql_query(f"""
        SELECT id AS source_row_id, fixture_id, observed_at AS ts, minute, league, event_title,
               pre_over25, pressure_now, opening_pressure, pressure_index, {_STATS_SQL},
               fair_base, token_id, condition_id, best_bid AS bid, best_ask AS ask,
               ask_depth_usd AS depth, obs_version, goal_before_ht AS won_raw
          FROM ht_pressure_observations
         WHERE best_ask IS NOT NULL AND best_bid IS NOT NULL
           AND COALESCE(home_goals,0) = 0 AND COALESCE(away_goals,0) = 0 {extra}""",
        conn, params=params)
    df["fixture"] = "af:" + df["fixture_id"].astype(str)
    df["fair_gap"] = _flt(df["fair_base"]) - _flt(df["ask"])
    df["won"] = _won_bool(df["won_raw"])
    df["label"] = "1H Over 0.5 at 0-0 — " + df["event_title"].fillna("?")
    return _finish(df, dedupe=["fixture", "minute"])


def load_fav_ht(conn, mode="backtest", live_minutes=4, fixture_ids=None) -> pd.DataFrame:
    """'<Pre-match favourite> leading at half time?' — YES."""
    extra, params = _filter(mode, live_minutes, fixture_ids, "fav_led_at_ht IS NOT NULL")
    df = pd.read_sql_query(f"""
        SELECT id AS source_row_id, fixture_id, observed_at AS ts, minute, league, event_title,
               home_goals, away_goals, goals_total, fav_side, fav_team, fav_prob,
               fav_pressure_now, dog_pressure_now, dominance_now, opening_dominance, pre_over25,
               {_STATS_SQL}, fair_base, token_id, condition_id, best_bid AS bid, best_ask AS ask,
               ask_depth_usd AS depth, obs_version, fav_led_at_ht AS won_raw
          FROM fav_ht_observations
         WHERE best_ask IS NOT NULL AND best_bid IS NOT NULL AND fav_side IS NOT NULL {extra}""",
        conn, params=params)
    df["fixture"] = "af:" + df["fixture_id"].astype(str)
    home = df["fav_side"] == "home"
    hg, ag = _flt(df["home_goals"]), _flt(df["away_goals"])
    df["fav_lead"] = np.where(home, hg - ag, ag - hg)
    poss = _flt(df["home_possession"])
    df["fav_possession"] = np.where(home, poss, 100.0 - poss)
    df["fair_gap"] = _flt(df["fair_base"]) - _flt(df["ask"])
    df["won"] = _won_bool(df["won_raw"])
    df["label"] = df["fav_team"].fillna("favourite") + " leading at HT — " + df["event_title"].fillna("?")
    return _finish(df, dedupe=["fixture", "minute"])


def load_settled(conn, mode="backtest", live_minutes=4, fixture_ids=None) -> pd.DataFrame:
    """Markets the score has already decided, still quoted below 1."""
    extra, params = _filter(mode, live_minutes, fixture_ids, "pm_winner IS NOT NULL")
    df = pd.read_sql_query(f"""
        SELECT id AS source_row_id, fixture_id, observed_at AS ts, minute, league,
               pm_title AS event_title, question, phase, rule, mins_since_first_seen,
               token_id, condition_id, best_bid AS bid, best_ask AS ask, ask_depth_usd AS depth,
               obs_version,
               CASE WHEN pm_winner IS NULL THEN NULL
                    WHEN pm_winner = winning_outcome THEN true ELSE false END AS won_raw
          FROM settled_market_observations
         WHERE best_ask IS NOT NULL {extra}""", conn, params=params)
    # one market per key: a fixture carries dozens of decided markets
    df["fixture"] = "af:" + df["fixture_id"].astype(str) + ":" + df["condition_id"].astype(str)
    df["won"] = _won_bool(df["won_raw"])
    df["label"] = df["question"].fillna("?") + " — " + df["event_title"].fillna("?")
    return _finish(df, dedupe=["fixture", "minute"])


# ── pre-match: sharp-price proxies ───────────────────────────────────────────

def _devig(*odds: pd.Series) -> list[pd.Series]:
    inv = [1.0 / _flt(o) for o in odds]
    s = sum(inv)
    return [x / s for x in inv]


def _soccer_prematch(conn, entry: str) -> pd.DataFrame:
    b = pd.read_sql_query("""
        SELECT match_id, league_code, country, tier, season_start, kickoff_utc, total_goals, result,
               ph_close, pd_close, pa_close, over25_close, under25_close,
               ph_open, pd_open, pa_open, over25_open, under25_open,
               home_rest_days, away_rest_days, home_form_pts5, away_form_pts5,
               home_avg_tg5, away_avg_tg5
          FROM bt_features""", conn)
    ph, pdr, pa = _devig(b[f"ph_{entry}"], b[f"pd_{entry}"], b[f"pa_{entry}"])
    po, pu = _devig(b[f"over25_{entry}"], b[f"under25_{entry}"])
    oh, od, oa = _devig(b["ph_open"], b["pd_open"], b["pa_open"])
    oo, ou = _devig(b["over25_open"], b["under25_open"])
    form = _flt(b["home_form_pts5"]) - _flt(b["away_form_pts5"])
    rest = _flt(b["home_rest_days"]) - _flt(b["away_rest_days"])
    tg = _flt(b["home_avg_tg5"]) + _flt(b["away_avg_tg5"])
    tgoals = _flt(b["total_goals"])
    res = b["result"].astype(str).str.strip()
    ko = pd.to_datetime(b["kickoff_utc"], utc=True)
    parts = []
    for market, side, prob, opn, won, fdiff, rdiff, fav in (
            ("1x2", "home", ph, oh, (res == "H"), form, rest, ph >= pa),
            ("1x2", "draw", pdr, od, (res == "D"), form, rest, None),
            ("1x2", "away", pa, oa, (res == "A"), -form, -rest, pa > ph),
            ("ou25", "over", po, oo, (tgoals >= 3), form, rest, None),
            ("ou25", "under", pu, ou, (tgoals <= 2), form, rest, None)):
        d = pd.DataFrame({
            "source_row_id": b["match_id"], "ts": ko, "league_code": b["league_code"],
            "country": b["country"], "tier": _flt(b["tier"]), "season_start": _flt(b["season_start"]),
            "month": ko.dt.month.astype(float), "market": market, "side": side, "prob": prob,
            "form_diff": fdiff, "rest_diff": rdiff, "tg5_sum": tg,
            "is_fav": (fav.astype(float) if fav is not None else np.nan),
            "won": won.astype(float).where(tgoals.notna() if market == "ou25" else res.isin(["H", "D", "A"])),
        })
        if entry == "close":
            d["move"] = prob - opn          # known by the close; never offered to the open universe
        d["fixture"] = "m:" + b["match_id"].astype(str) + ":" + market
        d["label"] = f"{market} {side}"
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    half = SOCCER_HALF_SPREAD
    df["ask"] = df["prob"] + half
    df["bid"] = df["prob"] - half
    df["depth"] = PROXY_DEPTH
    df["minute"] = np.nan
    df["token_id"] = None
    df["condition_id"] = None
    return _finish(df)


def load_soccer_prematch_close(conn, mode="backtest", **_) -> pd.DataFrame:
    return _soccer_prematch(conn, "close") if mode == "backtest" else pd.DataFrame()


def load_soccer_prematch_open(conn, mode="backtest", **_) -> pd.DataFrame:
    return _soccer_prematch(conn, "open") if mode == "backtest" else pd.DataFrame()


def _am2dec(s: pd.Series, default: Optional[float]) -> np.ndarray:
    x = pd.to_numeric(s, errors="coerce")
    if default is not None:
        x = x.fillna(default)
    x = x.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(x > 0, 1 + x / 100.0, 1 + 100.0 / np.abs(x))


def load_nfl_prematch(conn=None, mode="backtest", **_) -> pd.DataFrame:
    if mode != "backtest" or not os.path.exists(GAMES_CSV):
        return pd.DataFrame()
    g = pd.read_csv(GAMES_CSV, low_memory=False)
    g = g[(g["season"] >= 2012) & g["result"].notna() & g["spread_line"].notna()
          & g["total_line"].notna()].reset_index(drop=True)
    r, sl, tl, tot = (g["result"].astype(float), g["spread_line"].astype(float),
                      g["total_line"].astype(float), g["total"].astype(float))
    ph_ml, pa_ml = _devig(pd.Series(_am2dec(g["home_moneyline"], None)),
                          pd.Series(_am2dec(g["away_moneyline"], None)))
    ph_sp, pa_sp = _devig(pd.Series(_am2dec(g["home_spread_odds"], -110.0)),
                          pd.Series(_am2dec(g["away_spread_odds"], -110.0)))
    po, pu = _devig(pd.Series(_am2dec(g["over_odds"], -110.0)),
                    pd.Series(_am2dec(g["under_odds"], -110.0)))
    rest = g["home_rest"].astype(float) - g["away_rest"].astype(float)
    prime = (g["weekday"].isin(["Thursday", "Monday"]) | (g["gametime"].fillna("") >= "20:00")).astype(float)
    common = {"ts": pd.to_datetime(g["gameday"], utc=True), "season": g["season"].astype(float),
              "week": g["week"].astype(float), "playoff": (g["game_type"] != "REG").astype(float),
              "total_line": tl, "div_game": g["div_game"].astype(float), "roof": g["roof"],
              "wind": pd.to_numeric(g["wind"], errors="coerce"),
              "temp": pd.to_numeric(g["temp"], errors="coerce"), "primetime": prime}

    def cover(x):                      # 1 / 0, a push is void (NaN)
        return pd.Series(np.where(x > 0, 1.0, np.where(x < 0, 0.0, np.nan)))

    parts = []
    for market, side, prob, won, is_home, margin, rdiff in (
            ("ml", "home", ph_ml, pd.Series(np.where(r > 0, 1.0, np.where(r < 0, 0.0, 0.5))), 1.0, sl, rest),
            ("ml", "away", pa_ml, pd.Series(np.where(r < 0, 1.0, np.where(r > 0, 0.0, 0.5))), 0.0, -sl, -rest),
            ("spread", "home", ph_sp, cover(r - sl), 1.0, sl, rest),
            ("spread", "away", pa_sp, cover(sl - r), 0.0, -sl, -rest),
            ("total", "over", po, cover(tot - tl), np.nan, pd.Series(np.nan, index=g.index), rest),
            ("total", "under", pu, cover(tl - tot), np.nan, pd.Series(np.nan, index=g.index), rest)):
        d = pd.DataFrame({**common, "market": market, "side": side, "prob": prob.values,
                          "won": won.values, "is_home": is_home, "exp_margin": margin.values,
                          "is_fav": (margin > 0).astype(float).where(margin.notna()).values,
                          "rest_diff": rdiff.values, "source_row_id": g.index.values})
        d["fixture"] = "nfl:" + g["game_id"] + ":" + market
        d["label"] = market + " " + side + " — " + g["away_team"] + " @ " + g["home_team"]
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    df["ask"] = df["prob"] + NFL_HALF_SPREAD
    df["bid"] = df["prob"] - NFL_HALF_SPREAD
    df["depth"] = PROXY_DEPTH
    df["minute"] = np.nan
    df["token_id"] = None
    df["condition_id"] = None
    return _finish(df)


# ── the registry ─────────────────────────────────────────────────────────────

_PRICE = ("odds", "spread", "depth")
_STATS = ("xg_total", "shots_on_total", "shots_total", "corners_total", "reds_total")

UNIVERSES: dict[str, Universe] = {u.name: u for u in [
    Universe(
        "soccer_inplay_next_goal",
        "Buy the full-match over one goal above the current score, at any minute.",
        "pm_clob", "pressure_observations", True,
        ("minute", "goals_total", "home_goals", "away_goals", "goal_diff", "abs_diff", "pre_over25",
         "pressure_index", "max_danger") + _STATS + ("home_possession", "xg_window", "shots_on_window",
         "corners_window", "fair_base", "fair_gap", "league", "obs_version") + _PRICE,
        exits=("hold", "cash_out"), load=load_next_goal),
    Universe(
        "soccer_inplay_ht_over05",
        "Buy 1st Half O/U 0.5 (over) while the match is 0-0.",
        "pm_clob", "ht_pressure_observations", True,
        ("minute", "pre_over25", "pressure_now", "opening_pressure", "pressure_index") + _STATS
        + ("home_possession", "fair_base", "fair_gap", "league", "obs_version") + _PRICE,
        exits=("hold", "cash_out"), load=load_ht_over05),
    Universe(
        "soccer_inplay_fav_ht",
        "Buy '<pre-match favourite> leading at half time?'.",
        "pm_clob", "fav_ht_observations", True,
        ("minute", "home_goals", "away_goals", "goals_total", "fav_side", "fav_prob", "fav_lead",
         "fav_pressure_now", "dog_pressure_now", "dominance_now", "opening_dominance", "pre_over25")
        + _STATS + ("fav_possession", "fair_base", "fair_gap", "league", "obs_version") + _PRICE,
        exits=("hold", "cash_out"), load=load_fav_ht),
    Universe(
        "soccer_settled",
        "Buy a market the score has already decided, while it is still quoted below 1.",
        "pm_clob", "settled_market_observations", True,
        ("minute", "phase", "rule", "mins_since_first_seen", "league", "obs_version") + _PRICE,
        min_n_train=20, min_n_test=8, load=load_settled),
    Universe(
        "soccer_prematch_close",
        "Pre-match 1X2 / O/U 2.5 at the close, 22 leagues 2012-2026. Price = de-vigged Pinnacle close + PM half-spread.",
        "pinnacle_proxy", None, False,
        ("league_code", "country", "tier", "season_start", "month", "prob", "is_fav", "form_diff",
         "rest_diff", "tg5_sum", "move", "odds"),
        market_sides={"1x2": ("home", "draw", "away"), "ou25": ("over", "under")},
        split="2022-07-01", min_n_train=200, min_n_test=80, load=load_soccer_prematch_close),
    Universe(
        "soccer_prematch_open",
        "Pre-match 1X2 / O/U 2.5 at the OPEN. Nothing from the close is visible.",
        "pinnacle_proxy", None, False,
        ("league_code", "country", "tier", "season_start", "month", "prob", "is_fav", "form_diff",
         "rest_diff", "tg5_sum", "odds"),
        market_sides={"1x2": ("home", "draw", "away"), "ou25": ("over", "under")},
        split="2022-07-01", min_n_train=200, min_n_test=80, load=load_soccer_prematch_open),
    Universe(
        "nfl_prematch",
        "NFL moneyline / closing spread / closing total, 2012-2025. Price = de-vigged consensus close + half-spread.",
        "book_proxy", None, False,
        ("season", "week", "playoff", "is_home", "is_fav", "exp_margin", "total_line", "prob",
         "div_game", "roof", "wind", "temp", "rest_diff", "primetime", "odds"),
        market_sides={"ml": ("home", "away"), "spread": ("home", "away"), "total": ("over", "under")},
        split="2021-06-01", min_n_train=150, min_n_test=60, load=load_nfl_prematch),
]}


def load_cached(conn, U: Universe, refresh: bool = False, max_age_h: float = 6.0) -> pd.DataFrame:
    """A universe's backtest tape, pickled; the in-play ones grow every day, so
    they are reloaded once the cache is older than max_age_h."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{U.name}.pkl")
    if not refresh and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        with open(path, "rb") as fh:
            return pickle.load(fh)
    df = U.load(conn, mode="backtest")
    with open(path, "wb") as fh:
        pickle.dump(df, fh)
    return df
