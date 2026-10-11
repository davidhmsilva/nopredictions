"""
pm_prematch_flow.py — who moves Polymarket's 1X2 in the days before kick-off,
and do they know where it will close?   H-PM-FLOW (research, data only)

The operator's question (2026-10-10): not "does Polymarket lag Pinnacle", but
"in the 24-48h before a match, does Polymarket MOVE, WHO moves it, is it
someone with their own line, and how would we get to the fair line?"

    --fetch     every TAKER trade in [KO−96h, KO] on the three 1X2 markets of
                each fixture: Stage J's venue_market_history, linked to our
                matches, ≥ $30k of 1X2 volume, kick-off from 2025-09-01. One
                pickle per market in factory/cache/pm_flow/, so it resumes.
    --report    the price path by window, who trades each window, and whose
                trades the close agrees with — wallets chosen on the FIRST half
                of the period and judged on the SECOND, because a wallet that
                looks informed on the data it was picked from is the same
                winner's curse factory v2 exists to avoid.

Data API facts this relies on (probed 2026-10-10):
  * /trades?market=<conditionId> returns TAKER fills, newest first;
    `start`/`end` (unix s) bound it, which is how it is paged — `offset` stops
    at 10,000, and a big match has more in-play trades than that.
  * a fill on the No token (outcomeIndex 1) is the mirror of one on Yes:
    price_yes = 1 − price and the direction flips.
"""
from __future__ import annotations

import argparse
import logging
import os
import pickle
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from factory.v2 import tape as v2tape                              # noqa: E402

log = logging.getLogger("pm_flow")
DATA_API = "https://data-api.polymarket.com"
CACHE = os.path.join(HERE, "factory", "cache", "pm_flow")
WINDOW_S = 96 * 3600
PAGE = 1000
RATE = 6.0                 # requests per second, all threads together
MIN_FIXTURE_USD = 30_000
FROM = "2025-09-01"


# ── sample ──────────────────────────────────────────────────────────────────

def sample(conn) -> pd.DataFrame:
    """The three 1X2 markets of every fixture worth studying."""
    return pd.read_sql_query(f"""
        WITH f AS (
            SELECT event_id FROM venue_market_history
             WHERE venue = 'polymarket' AND family = 'moneyline' AND sport = 'soccer'
               AND match_id IS NOT NULL AND kickoff_utc >= '{FROM}' AND kickoff_utc < now() - interval '1 day'
               AND competition NOT LIKE '%%fifwc%%'
             GROUP BY event_id
            HAVING count(*) = 3 AND count(DISTINCT subject_side) = 3 AND sum(volume_usd) >= {MIN_FIXTURE_USD})
        SELECT v.market_id AS cid, v.event_id, v.competition, v.fixture, v.subject_side, v.match_id,
               v.match_swapped, v.kickoff_utc, v.volume_usd::float8 AS volume_usd,
               v.mid_24h::float8 AS mid_24h, v.mid_close::float8 AS mid_close, v.payout0::float8 AS payout0
          FROM venue_market_history v JOIN f USING (event_id)
         WHERE v.venue = 'polymarket' AND v.family = 'moneyline'""", conn)


# ── fetch ───────────────────────────────────────────────────────────────────

class Pacer:
    def __init__(self, rate: float):
        self.gap, self.next, self.lock = 1.0 / rate, time.monotonic(), threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


def _get(s: requests.Session, pacer: Pacer, params: dict, tries: int = 6):
    for k in range(tries):
        pacer.wait()
        try:
            r = s.get(f"{DATA_API}/trades", params=params, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** k)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** k)
            continue
        raise RuntimeError(f"/trades {r.status_code}: {r.text[:160]}")
    raise RuntimeError("/trades: retries exhausted")


KEEP = ("timestamp", "side", "outcomeIndex", "price", "size", "proxyWallet", "transactionHash")


def fetch_market(s: requests.Session, pacer: Pacer, cid: str, ko: int) -> pd.DataFrame:
    """Every taker fill in [ko − WINDOW_S, ko], paged backwards by time."""
    start, end, rows = ko - WINDOW_S, ko, []
    while True:
        batch = _get(s, pacer, {"market": cid, "start": start, "end": end, "limit": PAGE}) or []
        rows += [{k: r.get(k) for k in KEEP} for r in batch]
        if len(batch) < PAGE:
            break
        oldest = min(int(r["timestamp"]) for r in batch)
        end = oldest if oldest < end else end - 1          # never stall on one busy second
        if end <= start:
            break
    d = pd.DataFrame(rows, columns=KEEP)
    return d.drop_duplicates().reset_index(drop=True)


def fetch(conn, workers: int = 4, limit: int | None = None) -> None:
    os.makedirs(CACHE, exist_ok=True)
    smp = sample(conn)
    with open(os.path.join(CACHE, "_sample.pkl"), "wb") as fh:
        pickle.dump(smp, fh)
    todo = [r for r in smp.itertuples() if not os.path.exists(os.path.join(CACHE, f"{r.cid}.pkl"))]
    if limit:
        todo = todo[:limit]
    log.info(f"sample: {smp.event_id.nunique():,} fixtures · {len(smp):,} markets · {len(todo):,} to fetch")
    pacer, s, done, n_trades = Pacer(RATE), requests.Session(), 0, 0
    t0 = time.time()

    def one(r):
        d = fetch_market(s, pacer, r.cid, int(pd.Timestamp(r.kickoff_utc).timestamp()))
        with open(os.path.join(CACHE, f"{r.cid}.pkl.tmp"), "wb") as fh:
            pickle.dump(d, fh)
        os.replace(os.path.join(CACHE, f"{r.cid}.pkl.tmp"), os.path.join(CACHE, f"{r.cid}.pkl"))
        return len(d)

    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(one, r) for r in todo]
        for f in as_completed(futs):
            try:
                n_trades += f.result()
            except Exception as exc:                        # one market must not stop the sweep
                log.warning(f"market failed: {exc}")
            done += 1
            if done % 250 == 0:
                log.info(f"{done:,}/{len(todo):,} markets · {n_trades:,} trades · {time.time() - t0:.0f}s")
    log.info(f"done: {done:,} markets · {n_trades:,} trades · {time.time() - t0:.0f}s")


# ── load ────────────────────────────────────────────────────────────────────

def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(markets, trades). Trades are in YES terms: p_yes, and dir = +1 when the
    taker's fill raises their Yes exposure (buy Yes / sell No)."""
    with open(os.path.join(CACHE, "_sample.pkl"), "rb") as fh:
        smp = pickle.load(fh)
    parts = []
    for cid in smp.cid:
        path = os.path.join(CACHE, f"{cid}.pkl")
        if os.path.exists(path):
            with open(path, "rb") as fh:
                d = pickle.load(fh)
            if len(d):
                parts.append(d.assign(cid=cid))
    t = pd.concat(parts, ignore_index=True)
    t["price"], t["size"] = t.price.astype(float), t["size"].astype(float)
    yes = t.outcomeIndex.astype(int) == 0
    buy = t.side == "BUY"
    t["p_yes"] = np.where(yes, t.price, 1.0 - t.price)
    t["dir"] = np.where(yes == buy, 1.0, -1.0)
    t["usd"] = t["size"] * t.price
    t = t.merge(smp[["cid", "event_id", "kickoff_utc", "subject_side", "match_id", "match_swapped",
                     "mid_close", "payout0", "competition"]], on="cid")
    t["ko"] = pd.to_datetime(t.kickoff_utc, utc=True).astype("int64") // 10**9
    t["h_before"] = (t.ko - t.timestamp.astype(int)) / 3600.0
    return smp[smp.cid.isin(t.cid.unique())].copy(), t


# ── analysis ────────────────────────────────────────────────────────────────

HORIZONS = (72, 48, 24, 6, 1)              # hours before kick-off; the last point is the close
                                           # (72h reads fills from 72-78h; the fetch window is 96h)
STALE_H = 6.0                              # a price older than this at a horizon is no price
MIN_FIX = 15                               # fixtures a wallet needs before it is judged


def coherent_close(m: pd.DataFrame) -> pd.Series:
    """Fixtures whose three closing mids sum to a real book (fake mids from
    one-sided books — finding_pm_inplay_calibrated — break the sum)."""
    s = m.groupby("event_id").mid_close.agg(["sum", "count"])
    return s[(s["count"] == 3) & s["sum"].between(0.97, 1.05)].index


def price_at(t: pd.DataFrame, h: float) -> pd.Series:
    """Per market: the median of the last five fills at or before KO − h,
    if the newest of them is no older than STALE_H."""
    w = t[(t.h_before >= h) & (t.h_before <= h + STALE_H)].sort_values("h_before")
    return w.groupby("cid").head(5).groupby("cid").p_yes.median()


def path(m: pd.DataFrame, t: pd.DataFrame) -> pd.DataFrame:
    p = pd.DataFrame(index=m.cid.values)
    for h in HORIZONS:
        p[f"p{h}"] = price_at(t, h)
    p["close"] = m.set_index("cid").mid_close
    return p


def _ratio_se(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    """Σa/Σb and its delta-method standard error across clusters."""
    A, B, n = a.sum(), b.sum(), len(a)
    r = A / B
    if n < 2:
        return r, float("nan")
    return r, float(np.sqrt(((a - r * b) ** 2).sum() * n / (n - 1)) / B)


def wallet_table(t: pd.DataFrame) -> pd.DataFrame:
    """Per taker wallet: fixtures, USD, and USD-weighted CLV in pp (the close's
    verdict on the direction of each fill), clustered by fixture."""
    t = t.assign(cu=t.clv_pp * t.usd)
    f = t.groupby(["proxyWallet", "event_id"]).agg(a=("cu", "sum"), b=("usd", "sum")).reset_index()
    rows = []
    for w, g in f.groupby("proxyWallet"):
        r, se = _ratio_se(g.a, g.b)
        rows.append((w, len(g), g.b.sum(), r, se))
    out = pd.DataFrame(rows, columns=["wallet", "fixtures", "usd", "clv_pp", "se"]).set_index("wallet")
    out["z"] = out.clv_pp / out.se
    return out


def report(args) -> None:
    m, t = load()
    ok = coherent_close(m)
    m, t = m[m.event_id.isin(ok)], t[t.event_id.isin(ok)].copy()
    t = t[(t.h_before > 0) & (t.h_before <= 96)]
    t["clv_pp"] = 100 * t.dir * (t.mid_close - t.p_yes)
    print(f"\n{m.event_id.nunique():,} fixtures · {len(m):,} markets with a coherent close · {len(t):,} taker fills "
          f"· ${t.usd.sum() / 1e6:,.1f}M · {t.proxyWallet.nunique():,} wallets")

    # 1. does it move, and when
    p = path(m, t)
    pts = [f"p{h}" for h in HORIZONS] + ["close"]
    print("\n1. THE PATH — |move| per window in pp (markets with a price at both ends)")
    print(f"   {'window':<12} {'markets':>8} {'|move|':>8} {'per hour':>9} {'continues?':>11}  {'USD share':>9} {'wallets':>8}")
    for a, b in zip(pts[:-1], pts[1:]):
        d = p[list(dict.fromkeys([a, b, "close"]))].dropna()
        mv = 100 * (d[b] - d[a])
        rest = 100 * (d["close"] - d[b])
        ha = int(a[1:]); hb = 0 if b == "close" else int(b[1:])
        cont = np.corrcoef(mv, rest)[0, 1] if b != "close" and len(d) > 30 else float("nan")
        win = t[(t.h_before <= ha) & (t.h_before > hb)]
        print(f"   {ha:>3}h→{('KO' if b == 'close' else f'{hb}h'):<6} {len(d):>8,} {mv.abs().mean():>8.2f} "
              f"{mv.abs().mean() / (ha - hb):>9.3f} {cont:>+11.3f}  {win.usd.sum() / t.usd.sum():>9.1%} "
              f"{win.proxyWallet.nunique():>8,}")
    d = p[["p48", "p24", "close"]].dropna()
    print(f"   T-48 → close: |move| {100 * (d.close - d.p48).abs().mean():.2f}pp, of which T-48 → T-24 "
          f"{100 * (d.p24 - d.p48).abs().mean():.2f}pp (n={len(d):,})")

    # 2. who — chosen on the first half, judged on the second
    cut = pd.to_datetime(m.kickoff_utc, utc=True).median()
    first = t[pd.to_datetime(t.kickoff_utc, utc=True) < cut]
    second = t[pd.to_datetime(t.kickoff_utc, utc=True) >= cut]
    W1, W2 = wallet_table(first), wallet_table(second)
    judged = W1[W1.fixtures >= MIN_FIX]
    good, bad = judged[judged.z >= 3].index, judged[judged.z <= -3].index
    print(f"\n2. WHO — taker wallets; CLV = the close's verdict on each fill's direction, USD-weighted, pp")
    print(f"   split at {cut:%Y-%m-%d}. First half: {len(W1):,} wallets, {len(judged):,} with ≥{MIN_FIX} fixtures; "
          f"{len(good)} look informed (z ≥ 3), {len(bad)} look anti-informed (z ≤ −3)")
    for name, ws in (("'informed' (first half)", good), ("'anti-informed' (first half)", bad)):
        s = second[second.proxyWallet.isin(ws)]
        if len(s) == 0:
            print(f"   {name}: no second-half trades")
            continue
        f = s.assign(cu=s.clv_pp * s.usd).groupby("event_id").agg(a=("cu", "sum"), b=("usd", "sum"))
        r, se = _ratio_se(f.a, f.b)
        print(f"   {name}: SECOND half {len(f):,} fixtures, ${s.usd.sum() / 1e6:.2f}M, "
              f"CLV {r:+.2f}pp [{r - 1.96 * se:+.2f}, {r + 1.96 * se:+.2f}]")
    both = W1[W1.fixtures >= MIN_FIX].join(W2[W2.fixtures >= MIN_FIX], lsuffix="_1", rsuffix="_2", how="inner")
    if len(both) > 10:
        print(f"   persistence: {len(both)} wallets with ≥{MIN_FIX} fixtures in both halves, "
              f"rank corr of CLV {both.clv_pp_1.rank().corr(both.clv_pp_2.rank()):+.3f}")
    allw = wallet_table(t)
    print(f"   everyone: CLV {(_ratio_se(*[t.assign(cu=t.clv_pp * t.usd).groupby('event_id')[c].sum() for c in ('cu', 'usd')])[0]):+.2f}pp "
          f"(takers as a whole pay the spread to the close)")
    top = allw[allw.fixtures >= 30].sort_values("usd", ascending=False).head(12)
    print("   biggest takers (≥30 fixtures), whole period:")
    for w, r in top.iterrows():
        sh = t[t.proxyWallet == w]
        early = sh[sh.h_before > 24].usd.sum() / sh.usd.sum()
        print(f"     {w[:10]}…  fixtures {int(r.fixtures):>5,}  ${r.usd / 1e6:>6.2f}M  CLV {r.clv_pp:+.2f}pp ±{1.96 * r.se:.2f}"
              f"  ·  {early:.0%} of USD before T-24")
    pinnacle_section(m, t, good)
    fair_line_section(m, t, good, cut)
    if args.save:
        allw.to_csv(os.path.join(HERE, "..", "reports", f"pm_flow_wallets_{pd.Timestamp.utcnow():%Y-%m-%d}.csv"))


def _collection_utc(ko_utc: pd.Series) -> pd.Series:
    """Football-Data's collection moment for each kick-off: 15:00 London on the
    Friday (Fri-Mon games) or Tuesday (Tue-Thu) — the same rule as v2's tape,
    computed here from Polymarket's TRUE UTC kick-off."""
    loc = pd.to_datetime(ko_utc, utc=True).dt.tz_convert("Europe/London")
    wd = loc.dt.dayofweek
    back = np.select([wd == 4, wd == 5, wd == 6, wd == 0, wd == 1, wd == 2, wd == 3], [0, 1, 2, 3, 0, 1, 2])
    day = loc.dt.normalize() - pd.to_timedelta(back, unit="D")
    coll = day + pd.Timedelta(hours=15)
    return coll.dt.tz_convert("UTC")


def pinnacle_section(m: pd.DataFrame, t: pd.DataFrame, informed) -> None:
    """At the moment Football-Data read Pinnacle's price (its pre-close), where
    was Polymarket — and afterwards, who moved toward whom?"""
    from close_model import devig
    from factory.v2 import stats as v2s
    conn = v2tape.connect()
    bt = pd.read_sql_query("""SELECT match_id, ph_open, pd_open, pa_open, ph_close, pd_close, pa_close
                                FROM bt_features WHERE match_id = ANY(%s)""",
                           conn, params=([int(x) for x in m.match_id.unique()],))
    if bt.empty:
        print("\n3. PINNACLE — no overlap with Pinnacle prices")
        return
    o0 = bt[["ph_open", "pd_open", "pa_open"]].astype(float).to_numpy()
    o1 = bt[["ph_close", "pd_close", "pa_close"]].astype(float).to_numpy()
    ok = np.isfinite(o0).all(1) & np.isfinite(o1).all(1)
    bt, P0, P1 = bt[ok], devig(o0[ok]), devig(o1[ok])
    long = []
    for j, side in enumerate(("home", "draw", "away")):
        long.append(pd.DataFrame({"match_id": bt.match_id.values, "true_side": side,
                                  "pin_ps": P0[:, j], "pin_close": P1[:, j]}))
    pin = pd.concat(long)
    mm = m.copy()
    mm["true_side"] = np.where(mm.match_swapped & (mm.subject_side != "draw"),
                               np.where(mm.subject_side == "home", "away", "home"), mm.subject_side)
    mm = mm.merge(pin, on=["match_id", "true_side"])
    mm["coll"] = _collection_utc(mm.kickoff_utc)
    mm["h_ps"] = (pd.to_datetime(mm.kickoff_utc, utc=True) - mm.coll).dt.total_seconds() / 3600
    mm = mm[mm.h_ps > 1]
    tt = t.merge(mm[["cid", "h_ps"]], on="cid")
    w = tt[(tt.h_before >= tt.h_ps) & (tt.h_before <= tt.h_ps + STALE_H)].sort_values("h_before")
    mm["pm_ps"] = mm.cid.map(w.groupby("cid").head(5).groupby("cid").p_yes.median())
    after = tt[tt.h_before < tt.h_ps]
    flow = after.assign(sg=after.dir * after.usd).groupby("cid").sg.sum()
    iflow = after[after.proxyWallet.isin(informed)].assign(sg=lambda d: d.dir * d.usd).groupby("cid").sg.sum()
    mm["flow"], mm["iflow"] = mm.cid.map(flow).fillna(0.0), mm.cid.map(iflow).fillna(0.0)
    d = mm.dropna(subset=["pm_ps"]).copy()
    d["gap"] = 100 * (d.pin_ps - d.pm_ps)
    d["pm_move"] = 100 * (d.mid_close - d.pm_ps)
    d["pin_move"] = 100 * (d.pin_close - d.pin_ps)
    d["final_gap"] = 100 * (d.pin_close - d.mid_close)
    g = d.event_id.to_numpy()
    print(f"\n3. PINNACLE AT THE SAME MOMENT — {d.event_id.nunique():,} fixtures, {len(d):,} prices "
          f"(Football-Data's pre-close, median {d.h_ps.median():.0f}h before KO)")
    print(f"   |PM − Pinnacle| at that moment: mean {d.gap.abs().mean():.2f}pp, median {d.gap.abs().median():.2f}pp · "
          f"at the close {d.final_gap.abs().mean():.2f}pp")

    def slope(y, x):
        X = np.column_stack([np.ones(len(x)), x])
        b, V = v2s.ols_cluster(y, X, g)
        return b[1], np.sqrt(V[1, 1])
    b1, s1 = slope(d.pm_move.to_numpy(), d.gap.to_numpy())
    b2, s2 = slope(d.pin_move.to_numpy(), -d.gap.to_numpy())
    print(f"   PM's move to its close   ~ (Pinnacle − PM): slope {b1:+.2f} ±{1.96 * s1:.2f}  "
          f"(1 = Polymarket closes the whole gap toward Pinnacle)")
    print(f"   Pinnacle's move to close ~ (PM − Pinnacle): slope {b2:+.2f} ±{1.96 * s2:.2f}  "
          f"(> 0 = Pinnacle moves toward Polymarket: PM knew something first)")
    for lo, hi in ((0, 1), (1, 2), (2, 4), (4, 100)):
        x = d[(d.gap.abs() >= lo) & (d.gap.abs() < hi)]
        if len(x) < 30:
            continue
        sg = np.sign(x.gap)
        print(f"     gap {lo}-{hi}pp: n={len(x):>5,}  PM moved toward Pinnacle {np.mean(sg * x.pm_move):+.2f}pp, "
              f"Pinnacle toward PM {np.mean(-sg * x.pin_move):+.2f}pp")
    # whose flow lines up with what
    x = d[d.flow != 0]
    print(f"   taker flow after that moment, sign agreement — with the gap (copying Pinnacle): "
          f"{np.mean(np.sign(x.flow) == np.sign(x.gap)):.1%}; with Pinnacle's own later move: "
          f"{np.mean(np.sign(x.flow) == np.sign(x.pin_move)):.1%}  (n={len(x):,})")
    xi = d[d.iflow != 0]
    if len(xi) >= 30:
        print(f"   'informed' wallets' flow — with the gap: {np.mean(np.sign(xi.iflow) == np.sign(xi.gap)):.1%}; "
              f"with Pinnacle's later move: {np.mean(np.sign(xi.iflow) == np.sign(xi.pin_move)):.1%}  (n={len(xi):,})")


def fair_line_section(m: pd.DataFrame, t: pd.DataFrame, informed, cut) -> None:
    """At T-48 and T-24: which information predicts where Polymarket closes?
    close_model's gap (its probability from team state and PM's own price,
    minus PM's price), the taker flow of the previous 24h, and the flow of the
    wallets that looked informed in the first half. Fitted on the first half
    of the period, scored on the second."""
    import close_model as cm
    from factory.v2 import stats as v2s
    conn = v2tape.connect()
    f, _ = cm.walk(cm.load_matches(conn))
    spec = cm.load_model()
    st = f.set_index("match_id")
    mm = m[m.subject_side != "draw"].copy()
    mm["true_side"] = np.where(mm.match_swapped, np.where(mm.subject_side == "home", "away", "home"), mm.subject_side)
    mm = mm[mm.match_id.isin(st.index)]
    s = st.loc[mm.match_id.astype(int).to_numpy()]
    ok = ((s.h_n.to_numpy() >= cm.MIN_GAMES) & (s.a_n.to_numpy() >= cm.MIN_GAMES)
          & s.league.isin(cm.STATS_LEAGUES).to_numpy())
    me = np.where(mm.true_side == "home", "h", "a")
    for k in cm.TERMS:
        hk, ak = s[f"h_{k}"].to_numpy(float), s[f"a_{k}"].to_numpy(float)
        mm[f"x_{k}"] = np.where(ok, np.where(me == "h", hk - ak, ak - hk), np.nan)
    mm["is_home"] = (mm.true_side == "home").astype(float)
    coef = dict(zip(spec["features"], spec["coef"]))
    second = pd.to_datetime(mm.kickoff_utc, utc=True) >= cut
    print(f"\n4. HOW TO GET TO THE FAIR LINE — what predicts Polymarket's move to its close "
          f"(home/away, fitted before {cut:%Y-%m-%d}, scored after)")
    for h in (48, 24):
        p = price_at(t, h)
        d = mm.assign(p=mm.cid.map(p)).dropna(subset=["p", "mid_close"])
        z = spec["intercept"] + coef["l0"] * cm.logit(d.p.to_numpy())
        for k in cm.TERMS:
            z = z + coef[f"x_{k}"] * d[f"x_{k}"].to_numpy(float)
        z = z + coef["is_home"] * d.is_home.to_numpy()
        d["model_gap"] = 100 * (cm.sigmoid(z) - d.p)
        w = t[(t.h_before > h) & (t.h_before <= h + 24)]
        tot = w.groupby("cid").usd.sum()
        d["flow"] = d.cid.map(w.assign(sg=w.dir * w.usd).groupby("cid").sg.sum() / tot).fillna(0.0)
        wi = w[w.proxyWallet.isin(informed)]
        d["iflow"] = d.cid.map(wi.assign(sg=wi.dir * wi.usd).groupby("cid").sg.sum() / tot).fillna(0.0)
        d["move"] = 100 * (d.mid_close - d.p)
        d = d.dropna(subset=["model_gap"])
        sec = second.reindex(d.index).to_numpy()
        tr, te = d[~sec], d[sec]
        print(f"   from T-{h}h: {len(d):,} prices · mean |move| to close {d.move.abs().mean():.2f}pp")
        for cols in (["model_gap"], ["flow"], ["iflow"], ["model_gap", "flow", "iflow"]):
            if len(tr) < 100 or len(te) < 100:
                break
            X = np.column_stack([np.ones(len(tr))] + [tr[c] for c in cols])
            b, _ = v2s.ols_cluster(tr.move.to_numpy(), X, tr.event_id.to_numpy())
            Xt = np.column_stack([np.ones(len(te))] + [te[c] for c in cols])
            pred = Xt @ b
            r2 = 1 - ((te.move - pred) ** 2).sum() / ((te.move - te.move.mean()) ** 2).sum()
            print(f"     {' + '.join(cols):<28} out-of-sample R² {100 * r2:+.2f}%  coef {np.round(b[1:], 3)}")
        if len(te) >= 100:
            q = te.model_gap.quantile([0.8, 0.9])
            for name, thr in (("top 20%", q.iloc[0]), ("top 10%", q.iloc[1])):
                x = te[te.model_gap >= thr]
                r = v2s.mean_test(x.move.to_numpy() / 100)
                print(f"     model gap {name} (≥{thr:.1f}pp), after the cut: n={r['n']:,}  PM moved "
                      f"{100 * r['mean']:+.2f}pp ±{196 * r['se']:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--save", action="store_true", help="--report also writes the wallet table to reports/")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [pm_flow] %(message)s", datefmt="%H:%M:%S")
    if args.fetch:
        fetch(v2tape.connect(), args.workers, args.limit)
    if args.report:
        report(args)


if __name__ == "__main__":
    main()
