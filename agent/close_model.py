"""
close_model.py — where will a football 1X2 price CLOSE?   H-STATS-CLOSE (#47)

The question here is not "who wins". The price already knows far more about
that than any table of past results: on its own, this model loses to
Pinnacle's pre-close price by 13e-3 of log loss, out of sample. The question is
**where the price is going**. The pre-close price has not yet absorbed what a
team's recent shots and results say, and the close has:

  * Pinnacle, walk-forward 2015-2025, 97k matches: bets at the PRE-close where
    price + stats saw >= 2% value beat the close by +1.19% CI[+0.94,+1.44]
    (n=5,396; 10 of 11 seasons positive). On results, stats add nothing beyond
    the close, so the close is where that information ends up.
  * Polymarket 2024-26, trained on Pinnacle seasons < 2024 only: where the
    model put PM's T-24h mid >= 2pp too long, PM's own close came +1.10pp
    CI[+0.54,+1.65] toward it (n=113), and the mirror -1.09pp.

Inputs, all from PREVIOUS league matches only (state is read before the match
updates it, so a match never sees itself):

    gd_f / gd_s     goal difference, EWMA α 0.20 / 0.06
    sot_f / sot_s   shots-on-target difference, EWMA α 0.20 / 0.06
    sh_s            shots difference, EWMA α 0.06
    mres            points minus the points the match's CLOSING price expected, α 0.10
    rest            days since the team's previous league match, capped at 14

each as (this side − the other side), plus is_home and the logit of the side's
own de-vigged price. One logistic per side (home win, away win; the draw is not
modelled).

⚠️ The closing price behind `mres` is Pinnacle's until 2026-01-15, when
Football-Data stopped publishing it; after that it is Betfair Exchange's close,
else the market maximum. The fitted coefficients come from the Pinnacle years.
⚠️ Shots exist only for the 22 Football-Data leagues (STATS_LEAGUES). A team in
any other league reads 0 on every shots term, which the model would take as
"even" — so nothing outside those leagues is priced.

    python close_model.py --train          # fit on every Pinnacle season, write close_model.json
    python close_model.py --evaluate       # walk-forward: log loss, move R², CLV; + Polymarket
    python close_model.py --evaluate-tm    # research: + Transfermarkt congestion and XI value
    python close_model.py --teams          # rebuild the team-state cache the agent reads
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
load_dotenv(os.path.join(HERE, "../ingest/.env"))

import db_txn                                                      # noqa: E402

log = logging.getLogger("close_model")

DATABASE_URL = os.getenv("DATABASE_URL")
MODEL_PATH = os.path.join(HERE, "close_model.json")
STATES_PATH = os.path.join(HERE, "data", "close_model_teams.json")

STATS_LEAGUES = frozenset({
    "ENG-PR", "ENG-CH", "ENG-L1", "ENG-L2", "ENG-CON", "SCO-PR", "SCO-CH", "SCO-L1", "SCO-L2",
    "GER-BL1", "GER-BL2", "ITA-SA", "ITA-SB", "ESP-LL", "ESP-L2", "FRA-L1", "FRA-L2",
    "NED-ED", "BEL-JPL", "POR-PL", "TUR-SL", "GRE-SL",
})
BK_PIN_PRE, BK_PIN_CLOSE, BK_BF_CLOSE, BK_MAX_CLOSE = 4, 5, 10, 11
CLOSE_PRIORITY = (BK_PIN_CLOSE, BK_BF_CLOSE, BK_MAX_CLOSE)

ALPHA = {"gd_f": 0.20, "gd_s": 0.06, "sot_f": 0.20, "sot_s": 0.06, "sh_s": 0.06, "mres": 0.10}
TERMS = (*ALPHA, "rest")
FEATURES = [f"x_{k}" for k in TERMS] + ["is_home"]
MIN_GAMES = 8          # a team with fewer previous league matches is not priced
REST_CAP = 14
EVAL_FROM = 2015       # first walk-forward test season


# ── prices ──────────────────────────────────────────────────────────────────

def devig(odds: np.ndarray) -> np.ndarray:
    """Rows of three decimal odds → probabilities, power method (k solved per
    row so that Σ p^k = 1). Proportional de-vig overstates longshots, which is
    exactly the bias a model of moves would then 'discover'."""
    p = 1.0 / np.asarray(odds, dtype=float)
    lo, hi = np.full(len(p), 0.3), np.full(len(p), 3.0)
    for _ in range(60):
        k = (lo + hi) / 2
        s = (p ** k[:, None]).sum(1)
        lo, hi = np.where(s > 1, k, lo), np.where(s > 1, hi, k)
    q = p ** k[:, None]
    return q / q.sum(1, keepdims=True)


def logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


# ── history ─────────────────────────────────────────────────────────────────

def _conn():
    return db_txn.connect(DATABASE_URL)


def load_matches(conn) -> pd.DataFrame:
    """Every finished domestic league match, oldest first, with its stats, the
    Pinnacle pre-close (training only) and the best closing price we hold."""
    m = pd.read_sql("""
        SELECT m.id AS match_id, l.code AS league, EXTRACT(YEAR FROM s.start_date)::int AS season,
               m.kickoff_utc, m.home_team_id AS h, m.away_team_id AS a,
               m.home_score AS hs, m.away_score AS as_,
               st.home_shots_on_tgt AS hst, st.away_shots_on_tgt AS ast,
               st.home_shots AS hsh, st.away_shots AS ash
          FROM matches m
          JOIN seasons s ON s.id = m.season_id
          JOIN leagues l ON l.id = s.league_id
          LEFT JOIN match_stats st ON st.match_id = m.id
         WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
           AND m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
           AND NOT COALESCE(l.is_cup, false) AND NOT COALESCE(l.is_international, false)
           AND l.code <> 'USA-NBA'""", conn)
    o = pd.read_sql(f"""
        SELECT match_id, bookmaker_id AS bk, home_odds::float8 AS oh, draw_odds::float8 AS od,
               away_odds::float8 AS oa
          FROM match_odds
         WHERE bookmaker_id IN ({BK_PIN_PRE}, {', '.join(map(str, CLOSE_PRIORITY))})
           AND home_odds > 1 AND draw_odds > 1 AND away_odds > 1""", conn)
    o = o.drop_duplicates(["match_id", "bk"], keep="last")
    pre = o[o.bk == BK_PIN_PRE].set_index("match_id")[["oh", "od", "oa"]]
    cl = (o[o.bk != BK_PIN_PRE].assign(rank=lambda d: d.bk.map({b: i for i, b in enumerate(CLOSE_PRIORITY)}))
          .sort_values("rank").drop_duplicates("match_id").set_index("match_id"))
    m = m.join(pre.add_prefix("pre_"), on="match_id").join(cl[["oh", "od", "oa", "bk"]].add_prefix("cl_"), on="match_id")
    m["kickoff_utc"] = pd.to_datetime(m.kickoff_utc, utc=True)
    return m.sort_values(["kickoff_utc", "match_id"]).reset_index(drop=True)


def _new_state() -> dict:
    return {"n": 0, "last": None, **{k: 0.0 for k in ALPHA}}


def walk(m: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Pre-match team state for every match, then the state after the last one.

    The state is read BEFORE the match updates it, so no match sees itself."""
    close = np.full((len(m), 3), np.nan)
    has = m[["cl_oh", "cl_od", "cl_oa"]].notna().all(1).values
    if has.any():
        close[has] = devig(m.loc[has, ["cl_oh", "cl_od", "cl_oa"]].values)
    st: dict = defaultdict(_new_state)
    cols = {f"{s}_{k}": np.zeros(len(m)) for s in ("h", "a") for k in (*ALPHA, "n", "rest")}
    H, A, KO = m.h.values, m.a.values, m.kickoff_utc.values
    HS, AS = m.hs.values, m.as_.values
    HST, AST, HSH, ASH = (m[c].values.astype(float) for c in ("hst", "ast", "hsh", "ash"))
    for i in range(len(m)):
        th, ta = st[H[i]], st[A[i]]
        for side, me in (("h", th), ("a", ta)):
            for k in ALPHA:
                cols[f"{side}_{k}"][i] = me[k]
            cols[f"{side}_n"][i] = me["n"]
            cols[f"{side}_rest"][i] = (min((KO[i] - me["last"]) / np.timedelta64(1, "D"), REST_CAP)
                                       if me["last"] is not None else 7.0)
        gd = HS[i] - AS[i]
        for me, sgn, col in ((th, 1, 0), (ta, -1, 2)):
            upd = {"gd_f": sgn * gd, "gd_s": sgn * gd}
            if not math.isnan(HST[i]) and not math.isnan(AST[i]):
                upd["sot_f"] = upd["sot_s"] = sgn * (HST[i] - AST[i])
            if not math.isnan(HSH[i]) and not math.isnan(ASH[i]):
                upd["sh_s"] = sgn * (HSH[i] - ASH[i])
            if has[i]:
                pts = 3 if sgn * gd > 0 else (1 if gd == 0 else 0)
                upd["mres"] = pts - (3 * close[i, col] + close[i, 1])
            for k, v in upd.items():
                me[k] = (1 - ALPHA[k]) * me[k] + ALPHA[k] * v
            me["n"] += 1
            me["last"] = KO[i]
    return pd.concat([m, pd.DataFrame(cols)], axis=1), st


def side_rows(f: pd.DataFrame, price: str = "pre") -> pd.DataFrame:
    """One row per side (home win, away win) with features as (side − other)."""
    ok = f[[f"{price}_oh", f"{price}_od", f"{price}_oa"]].notna().all(1)
    f = f[ok & (f.h_n >= MIN_GAMES) & (f.a_n >= MIN_GAMES) & f.league.isin(STATS_LEAGUES)].copy()
    P = devig(f[[f"{price}_oh", f"{price}_od", f"{price}_oa"]].values)
    C = np.full_like(P, np.nan)
    hc = f[["cl_oh", "cl_od", "cl_oa"]].notna().all(1).values
    C[hc] = devig(f.loc[hc, ["cl_oh", "cl_od", "cl_oa"]].values)
    out = []
    for side, opp, j, won in (("h", "a", 0, f.hs > f.as_), ("a", "h", 2, f.as_ > f.hs)):
        d = pd.DataFrame({"match_id": f.match_id.values, "season": f.season.values,
                          "league": f.league.values, "is_home": int(side == "h"),
                          "p0": P[:, j], "p1": C[:, j],
                          "o0": f[f"{price}_o{side}"].values, "o1": f[f"cl_o{side}"].values,
                          "cl_bk": f.cl_bk.values, "y": won.astype(int).values})
        for k in TERMS:
            d[f"x_{k}"] = f[f"{side}_{k}"].values - f[f"{opp}_{k}"].values
        out.append(d)
    d = pd.concat(out, ignore_index=True)
    d["l0"], d["l1"] = logit(d.p0), logit(d.p1)
    d["move"] = d.l1 - d.l0
    return d


# ── the model ───────────────────────────────────────────────────────────────

def fit(rows: pd.DataFrame, features: list[str] = FEATURES):
    from sklearn.linear_model import LogisticRegression
    cols = ["l0"] + features
    return LogisticRegression(C=1.0, max_iter=1000).fit(rows[cols].values, rows.y.values), cols


def train(conn) -> dict:
    f, _ = walk(load_matches(conn))
    rows = side_rows(f)
    rows = rows[rows.cl_bk == BK_PIN_CLOSE]          # the Pinnacle years only
    m, cols = fit(rows)
    spec = {
        "version": 1, "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hypothesis": "H-STATS-CLOSE", "features": cols,
        "coef": [round(float(c), 6) for c in m.coef_[0]], "intercept": round(float(m.intercept_[0]), 6),
        "alpha": ALPHA, "min_games": MIN_GAMES, "rest_cap": REST_CAP,
        "seasons": [int(rows.season.min()), int(rows.season.max())], "n_rows": int(len(rows)),
        "leagues": sorted(STATS_LEAGUES),
    }
    with open(MODEL_PATH, "w") as fh:
        json.dump(spec, fh, indent=1)
    log.info(f"trained on {len(rows):,} side-rows, seasons {spec['seasons']} → {MODEL_PATH}")
    return spec


def load_model() -> dict:
    with open(MODEL_PATH) as fh:
        return json.load(fh)


def predict(spec: dict, l0: float, x: dict[str, float]) -> float:
    """The model's probability for one side: its own price logit `l0`, and the
    (side − other) differences in `x`, keyed like FEATURES."""
    z = spec["intercept"]
    for name, c in zip(spec["features"], spec["coef"]):
        z += c * (l0 if name == "l0" else x[name])
    return float(sigmoid(z))


# ── live team state ─────────────────────────────────────────────────────────

def build_states(conn) -> dict:
    m = load_matches(conn)
    _, st = walk(m)
    seen = pd.concat([m[["h", "kickoff_utc", "league"]].rename(columns={"h": "t"}),
                      m[["a", "kickoff_utc", "league"]].rename(columns={"a": "t"})])
    league_of = seen.sort_values("kickoff_utc").groupby("t").league.last().to_dict()
    out = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "last_match": m.kickoff_utc.max().isoformat(), "teams": {}}
    for tid, s in st.items():
        out["teams"][str(int(tid))] = {**{k: round(float(s[k]), 5) for k in ALPHA}, "n": int(s["n"]),
                                       "last": (pd.Timestamp(s["last"]).tz_localize("UTC").isoformat()
                                                if s["last"] is not None else None),
                                       "league": league_of.get(tid)}
    os.makedirs(os.path.dirname(STATES_PATH), exist_ok=True)
    with open(STATES_PATH, "w") as fh:
        json.dump(out, fh)
    log.info(f"team states: {len(out['teams'])} teams, last match {out['last_match']}")
    return out


def load_states(conn=None, max_age_h: float = 6.0) -> dict:
    """The cached team states, rebuilt when older than `max_age_h`."""
    try:
        with open(STATES_PATH) as fh:
            s = json.load(fh)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(s["built_at"])).total_seconds() / 3600
        if age <= max_age_h:
            return s
    except (OSError, ValueError, KeyError):
        pass
    return build_states(conn or _conn())


def features_for(states: dict, home_id: int, away_id: int, kickoff: datetime) -> dict | None:
    """(home − away) differences for a fixture not yet played, or None when
    either side has too little history or plays outside STATS_LEAGUES."""
    t = states["teams"]
    h, a = t.get(str(home_id)), t.get(str(away_id))
    if not h or not a or h["n"] < MIN_GAMES or a["n"] < MIN_GAMES:
        return None
    if h.get("league") not in STATS_LEAGUES or a.get("league") not in STATS_LEAGUES:
        return None

    def rest(s):
        if not s.get("last"):
            return 7.0
        return min((kickoff - datetime.fromisoformat(s["last"])).total_seconds() / 86400, REST_CAP)
    x = {f"x_{k}": h[k] - a[k] for k in ALPHA}
    x["x_rest"] = rest(h) - rest(a)
    return x


def fair_pair(spec: dict, x_home: dict, p_home: float, p_away: float) -> tuple[float, float]:
    """Model probabilities (home win, away win) from the venue's own de-vigged
    prices and the home-perspective differences."""
    x_away = {k: -v for k, v in x_home.items()}
    q_h = predict(spec, float(logit(p_home)), {**x_home, "is_home": 1})
    q_a = predict(spec, float(logit(p_away)), {**x_away, "is_home": 0})
    return q_h, q_a


# ── evaluation ──────────────────────────────────────────────────────────────

def _ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _boot(v: np.ndarray, groups: np.ndarray, n: int = 1000, seed: int = 7) -> tuple[float, float]:
    t = pd.DataFrame({"v": v, "g": groups}).groupby("g").v.agg(["sum", "count"])
    s, k = t["sum"].values, t["count"].values
    idx = np.random.default_rng(seed).integers(0, len(t), (n, len(t)))
    b = s[idx].sum(1) / k[idx].sum(1)
    return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def walk_forward(rows: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    from sklearn.linear_model import LinearRegression, LogisticRegression
    out = []
    for s in sorted(rows.season.unique()):
        if s < EVAL_FROM:
            continue
        tr, te = rows[rows.season < s], rows[rows.season == s].copy()
        if tr.empty or te.empty:
            continue
        lr = lambda cols: LogisticRegression(C=1.0, max_iter=1000).fit(tr[cols], tr.y)  # noqa: E731
        te["q_price"] = lr(["l0"]).predict_proba(te[["l0"]])[:, 1]
        te["q_both"] = lr(["l0"] + features).predict_proba(te[["l0"] + features])[:, 1]
        mv = te.move.notna()
        reg = LinearRegression().fit(tr.loc[tr.move.notna(), ["l0"] + features], tr.loc[tr.move.notna(), "move"])
        te.loc[mv, "mv_hat"] = reg.predict(te.loc[mv, ["l0"] + features])
        out.append(te)
    return pd.concat(out)


def report_walk(T: pd.DataFrame, label: str) -> None:
    d = _ll(T.y, T.q_both) - _ll(T.y, T.q_price)
    lo, hi = _boot(d.values, T.match_id.values)
    m = T.move.notna()
    r2 = 1 - ((T.move[m] - T.mv_hat[m]) ** 2).mean() / ((T.move[m] - T.move[m].mean()) ** 2).mean()
    print(f"\n[{label}] {T.match_id.nunique():,} matches OOS {T.season.min()}-{T.season.max()}")
    print(f"  results: price+stats vs price, log loss x1000 {d.mean()*1000:+.3f} [{lo*1000:+.3f},{hi*1000:+.3f}]")
    print(f"  move pre->close: OOS R2 {r2:.4f}, corr {np.corrcoef(T.mv_hat[m], T.move[m])[0,1]:.3f}")
    for thr in (0.02, 0.04, 0.06):
        b = T[(T.q_both * T.o0 - 1 >= thr) & T.o1.notna()]
        if len(b) < 30:
            continue
        clv = (b.o0 / b.o1 - 1) * 100
        lo, hi = _boot(clv.values, b.match_id.values)
        pos = (b.assign(c=clv).groupby("season").c.mean() > 0).mean()
        print(f"  value >= {thr:.0%} at the pre-close: n={len(b):6,}  CLV {clv.mean():+.2f}% [{lo:+.2f},{hi:+.2f}]"
              f"  seasons positive {pos:.0%}")


def evaluate(conn) -> None:
    f, _ = walk(load_matches(conn))
    rows = side_rows(f)
    rows = rows[rows.cl_bk == BK_PIN_CLOSE]
    report_walk(walk_forward(rows, FEATURES), "Pinnacle pre-close -> close")
    evaluate_pm(conn, rows, f)


def evaluate_pm(conn, rows: pd.DataFrame, f: pd.DataFrame, features: list[str] = FEATURES,
                extra: pd.DataFrame | None = None) -> None:
    """The model trained on Pinnacle seasons < 2024, applied to Polymarket's own
    T-24h mid (Stage J, coherent three-way books only) for 2024-25 and later."""
    m, cols = fit(rows[rows.season < 2024], features)
    v = pd.read_sql("""
        SELECT event_id, match_id, match_swapped AS sw, subject_side AS side,
               mid_24h::float8 AS m24, mid_close::float8 AS mc, payout0::float8 AS pay
          FROM venue_market_history
         WHERE venue = 'polymarket' AND family = 'moneyline' AND subject_side IN ('home','away','draw')
           AND payout0 IN (0, 1) AND mid_24h IS NOT NULL AND mid_close IS NOT NULL
           AND match_id IS NOT NULL""", conn)
    g = v.groupby("event_id")
    ok = g.side.transform("size").eq(3) & g.side.transform("nunique").eq(3)
    for c in ("m24", "mc"):
        ok &= g[c].transform("sum").between(0.98, 1.04)
    v = v[ok].copy()
    for c in ("m24", "mc"):
        v[c] = v[c] / v.groupby("event_id")[c].transform("sum")
    v = v[v.side != "draw"].copy()
    v["is_home"] = np.where(v.sw, (v.side == "away"), (v.side == "home")).astype(int)
    feat = f[f.league.isin(STATS_LEAGUES) & (f.h_n >= MIN_GAMES) & (f.a_n >= MIN_GAMES)][
        ["match_id", "season"] + [f"{s}_{k}" for s in ("h", "a") for k in TERMS]]
    j = v.merge(feat, on="match_id")
    j = j[j.season >= 2024].copy()
    for k in TERMS:
        j[f"x_{k}"] = np.where(j.is_home == 1, j[f"h_{k}"] - j[f"a_{k}"], j[f"a_{k}"] - j[f"h_{k}"])
    if extra is not None:
        j = j.merge(extra, on=["match_id", "is_home"], how="inner")
    j["l0"] = logit(j.m24)
    j["q"] = m.predict_proba(j[cols].values)[:, 1]
    j["gap"], j["mv"] = j.q - j.m24, j.mc - j.m24
    print(f"\n[Polymarket T-24h -> close] {j.event_id.nunique()} fixtures, {len(j)} prices; "
          f"corr(model gap, PM move) {np.corrcoef(j.gap, j.mv)[0,1]:.3f}, slope {np.polyfit(j.gap, j.mv, 1)[0]:.2f}")
    for thr in (0.01, 0.02, 0.03):
        b = j[j.gap >= thr]
        if len(b) < 15:
            continue
        lo, hi = _boot(b.mv.values * 100, b.event_id.values)
        print(f"  model says PM >= {thr*100:.0f}pp too long: n={len(b):4d}  PM moved {b.mv.mean()*100:+.2f}pp [{lo:+.2f},{hi:+.2f}]")


# ── research: Transfermarkt congestion and XI value ──────────────────────────

def tm_features(conn, f: pd.DataFrame) -> pd.DataFrame:
    """Per (match, side): calendar and squad terms from Transfermarkt, every
    competition included. RESEARCH ONLY — tm_games stops in 2026-05, so none of
    this can be computed for a fixture today.
      tm_rest     days since the last game in ANY competition (cap 14)
      tm_euro_prev  a European club game in the 4 days before
      tm_euro_next  a European club game in the 4 days after (rotation risk;
                    a live version needs the schedule, which we do not hold)
      tm_n14      games in the previous 14 days
      tm_xi_dep   log(last XI value / mean of the 10 XIs before it): who is missing
      tm_xi_str   log mean XI value of the last 10 games: squad strength"""
    cmap = pd.read_sql("SELECT tm_club_id, team_id FROM tm_club_map WHERE team_id IS NOT NULL", conn)
    g = pd.read_sql("SELECT game_id, game_date, competition_type, home_club_id, away_club_id FROM tm_games", conn)
    lf = pd.read_sql("SELECT game_id, club_id, xi_value_eur::float8 AS xi FROM tm_lineup_features", conn)
    long = pd.concat([g.rename(columns={"home_club_id": "club"})[["game_id", "game_date", "competition_type", "club"]],
                      g.rename(columns={"away_club_id": "club"})[["game_id", "game_date", "competition_type", "club"]]])
    long = long.merge(lf.rename(columns={"club_id": "club"}), on=["game_id", "club"], how="left")
    long = long.merge(cmap.rename(columns={"tm_club_id": "club"}), on="club")
    long["d"] = pd.to_datetime(long.game_date).values.astype("datetime64[D]")
    by_team = {t: (x.d.values, x.competition_type.fillna("").values, x.xi.values.astype(float))
               for t, x in long.sort_values("d").groupby("team_id")}
    day = np.timedelta64(1, "D")
    rows = []
    kd = f.kickoff_utc.dt.tz_convert(None).values.astype("datetime64[D]")
    for i, (mid, h, a) in enumerate(zip(f.match_id.values, f.h.values, f.a.values)):
        for side, tid in ((1, h), (0, a)):
            if tid not in by_team:
                continue
            d, comp, xi_all = by_team[tid]
            j = np.searchsorted(d, kd[i], side="left")      # games before the match day
            k = np.searchsorted(d, kd[i], side="right")     # games after it
            if j < 11:
                continue
            gap_prev = (kd[i] - d[:j]) / day
            gap_next = (d[k:k + 3] - kd[i]) / day
            rec = {"match_id": mid, "is_home": side,
                   "tm_rest": float(min(gap_prev[-1], REST_CAP)),
                   "tm_euro_prev": int(((gap_prev <= 4) & (comp[:j] == "international_cup")).any()),
                   "tm_euro_next": int(((gap_next <= 4) & (comp[k:k + 3] == "international_cup")).any()),
                   "tm_n14": int((gap_prev <= 14).sum())}
            xi = xi_all[j - 11:j]
            if np.isfinite(xi).all() and (xi > 0).all():
                rec["tm_xi_dep"] = float(np.log(xi[-1] / xi[:-1].mean()))
                rec["tm_xi_str"] = float(np.log(xi[-10:].mean()))
            rows.append(rec)
    return pd.DataFrame(rows)


TM_TERMS = ("tm_rest", "tm_euro_prev", "tm_euro_next", "tm_n14", "tm_xi_dep", "tm_xi_str")


def evaluate_tm(conn) -> None:
    f, _ = walk(load_matches(conn))
    rows = side_rows(f)
    rows = rows[rows.cl_bk == BK_PIN_CLOSE]
    t = tm_features(conn, f[f.match_id.isin(rows.match_id)])
    me = t.rename(columns={k: f"{k}_me" for k in TM_TERMS})
    op = t.assign(is_home=1 - t.is_home).rename(columns={k: f"{k}_op" for k in TM_TERMS})
    both = me.merge(op, on=["match_id", "is_home"]).dropna()
    for k in TM_TERMS:
        both[f"x_{k}"] = both[f"{k}_me"] - both[f"{k}_op"]
    tm_cols = [f"x_{k}" for k in TM_TERMS]
    r = rows.merge(both[["match_id", "is_home"] + tm_cols], on=["match_id", "is_home"])
    print(f"Transfermarkt terms available on {r.match_id.nunique():,} of {rows.match_id.nunique():,} matches")
    report_walk(walk_forward(r, FEATURES), "same matches, base terms")
    report_walk(walk_forward(r, FEATURES + tm_cols), "same matches, base + Transfermarkt")
    from sklearn.linear_model import LinearRegression
    z = r.dropna(subset=["move"])
    cols = ["l0"] + FEATURES + tm_cols
    reg = LinearRegression().fit((z[cols] - z[cols].mean()) / z[cols].std(), z.move)
    print("  move, standardised coefficients:", {c: round(float(v), 4) for c, v in zip(cols, reg.coef_)})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--evaluate-tm", action="store_true")
    ap.add_argument("--teams", action="store_true")
    a = ap.parse_args()
    conn = _conn()
    if a.train:
        train(conn)
    if a.evaluate:
        evaluate(conn)
    if a.evaluate_tm:
        evaluate_tm(conn)
    if a.teams:
        build_states(conn)
    if not (a.train or a.evaluate or a.evaluate_tm or a.teams):
        ap.print_help()


if __name__ == "__main__":
    main()
