"""
engine.py — run a spec over a tape: mask, first entry per fixture, return net of
the taker fee, split train/test by date, and control the false discovery rate
across a batch.

What a result means:
  * `train` / `test` — one bet per fixture, the first qualifying moment; returns
    per unit staked AFTER the Polymarket taker fee (0.05·p·(1−p) per share, on
    the buy and on any cash-out sale).
  * `cal` — every qualifying row, not only the first, averaged per fixture then
    across fixtures (won − cost). Far more power than the entries alone; this
    is the arm the 100-game review had to fall back on.
  * `q` — Benjamini-Hochberg over every spec in the batch that reached the
    train minimum. Testing thousands of rules makes dozens look good by chance;
    q is what says how many of the survivors are expected to be luck.
  * `pass` — q ≤ FDR_Q on train AND positive on the held-out test period.
    `strong` — the test period alone is also significant.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .universes import Universe

FEE_RATE = 0.05
Z = 1.96
FDR_Q = 0.10


@dataclass
class Frame:
    U: Universe
    df: pd.DataFrame
    cols: dict
    fix: np.ndarray                 # integer fixture codes, non-decreasing with row order
    train: np.ndarray               # row belongs to a train-period fixture
    cutoff: Optional[pd.Timestamp]
    _tok: Optional[dict] = None

    def token_index(self) -> dict:
        """token_id -> (minutes, bids), sorted by minute — the tape a cash-out reads."""
        if self._tok is None:
            d = self.df[self.df["token_id"].notna()][["token_id", "minute", "bid"]]
            d = d.sort_values(["token_id", "minute"], kind="mergesort")
            self._tok = {t: (g["minute"].to_numpy(float), g["bid"].to_numpy(float))
                         for t, g in d.groupby("token_id", sort=False)}
        return self._tok


def make_frame(U: Universe, df: pd.DataFrame, split: bool = True) -> Frame:
    df = df.copy()
    df["odds"] = 1.0 / df["ask"]
    df["spread"] = df["ask"] - df["bid"]
    cols = {c: df[c].to_numpy() for c in df.columns}
    fix = pd.factorize(df["fixture"], sort=False)[0]
    cutoff, train = None, np.ones(len(df), bool)
    if split and len(df):
        first = df.groupby("fixture", sort=False)["ts"].transform("min")
        if U.split == "q70":
            cutoff = df.groupby("fixture")["ts"].min().quantile(0.7)
        else:
            cutoff = pd.Timestamp(U.split, tz="UTC")
        train = (first < cutoff).to_numpy()
    return Frame(U, df, cols, fix, train, cutoff)


def _num(a: np.ndarray) -> np.ndarray:
    if a.dtype == object or a.dtype == bool:
        return pd.to_numeric(pd.Series(a), errors="coerce").to_numpy(float)
    return a.astype(float, copy=False)


def _cond(a: np.ndarray, op: str, v) -> np.ndarray:
    if op == "notnull":
        return ~pd.isna(a)
    if op == "isnull":
        return pd.isna(a)
    if op == "in":
        return pd.Series(a).isin(list(v)).to_numpy()
    if isinstance(v, str):
        s = pd.Series(a)
        if op == "==":
            return s.eq(v).to_numpy()
        if op == "!=":
            return (s.ne(v) & s.notna()).to_numpy()
        raise ValueError(f"{op} is not defined on text")
    x = _num(a)
    with np.errstate(invalid="ignore"):
        if op == "between":
            return (x >= v[0]) & (x <= v[1])
        if op == "==":
            return x == v
        if op == "!=":
            return (x != v) & ~np.isnan(x)
        if op == ">=":
            return x >= v
        if op == "<=":
            return x <= v
        if op == ">":
            return x > v
        if op == "<":
            return x < v
    raise ValueError(op)


def mask(spec: dict, fr: Frame, require_won: bool = True) -> np.ndarray:
    c = fr.cols
    m = np.ones(len(fr.df), bool)
    if spec.get("market"):
        m &= pd.Series(c["market"]).eq(spec["market"]).to_numpy()
    if spec.get("side"):
        m &= pd.Series(c["side"]).eq(spec["side"]).to_numpy()
    for col, op, val in spec["where"]:
        m &= _cond(c[col], op, val)
    p = spec.get("price") or {}
    odds, ask = _num(c["odds"]), _num(c["ask"])
    with np.errstate(invalid="ignore"):
        if "odds_min" in p:
            m &= odds >= p["odds_min"]
        if "odds_max" in p:
            m &= odds <= p["odds_max"]
        if "max_spread" in p:
            m &= _num(c["spread"]) <= p["max_spread"] + 1e-9
        if "min_depth_usd" in p:
            m &= _num(c["depth"]) >= p["min_depth_usd"]
        m &= np.isfinite(ask) & (ask > 0) & (ask < 1)
    if require_won:
        m &= ~np.isnan(_num(c["won"]))
    return m


def entries(fr: Frame, m: np.ndarray) -> np.ndarray:
    """The first qualifying row of each fixture."""
    idx = np.flatnonzero(m)
    if idx.size == 0:
        return idx
    _, first = np.unique(fr.fix[idx], return_index=True)
    return idx[first]


def cost_per_share(ask: np.ndarray) -> np.ndarray:
    return ask * (1.0 + FEE_RATE * (1.0 - ask))


def returns(fr: Frame, eidx: np.ndarray, exit: dict) -> tuple[np.ndarray, int]:
    """Return per unit staked for each entry, and how many cash-outs fell back to
    holding because the tape had no quote at the exit minute."""
    c = fr.cols
    ask = _num(c["ask"])[eidx]
    won = _num(c["won"])[eidx]
    cost = cost_per_share(ask)
    ret = won / cost - 1.0
    fallbacks = 0
    if exit.get("type") == "cash_out" and eidx.size:
        M = float(exit["minute"])
        tok, mins, toks = fr.token_index(), _num(c["minute"]), c["token_id"]
        for k, i in enumerate(eidx):
            if not mins[i] < M:
                continue
            arr = tok.get(toks[i])
            if arr is None:
                fallbacks += 1
                continue
            tm, tb = arr
            j = int(np.searchsorted(tm, M))
            if j < len(tm) and tm[j] <= M + 3 and np.isfinite(tb[j]) and tb[j] > 0:
                b = tb[j]
                ret[k] = b * (1.0 - FEE_RATE * (1.0 - b)) / cost[k] - 1.0
            else:
                # the token left the tape: in 'next goal' that is the goal that
                # settled it; otherwise a recording gap — hold is the only honest read
                fallbacks += 1
    return ret, fallbacks


def summarize(ret: np.ndarray, won: np.ndarray, ask: np.ndarray) -> dict:
    n = int(ret.size)
    if n == 0:
        return {"n": 0}
    y = float(ret.mean())
    se = float(ret.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    z = y / se if se and se > 0 and not math.isnan(se) else 0.0
    p_one = 0.5 * math.erfc(z / math.sqrt(2))            # H1: yield > 0
    return {"n": n, "hit": float(np.mean(won)), "implied": float(np.mean(ask)),
            "avg_odds": float(np.mean(1.0 / ask)), "yield": y, "se": se,
            "ci_lo": y - Z * se if n > 1 else None, "ci_hi": y + Z * se if n > 1 else None,
            "p": p_one, "pnl": float(ret.sum())}


def calibration(fr: Frame, m: np.ndarray) -> dict:
    """won − cost over every qualifying row, clustered by fixture."""
    idx = np.flatnonzero(m)
    if idx.size == 0:
        return {"fixtures": 0}
    ask = _num(fr.cols["ask"])[idx]
    diff = _num(fr.cols["won"])[idx] - cost_per_share(ask)
    per = pd.Series(diff).groupby(fr.fix[idx]).mean().to_numpy()
    k = per.size
    mu = float(per.mean())
    se = float(per.std(ddof=1) / math.sqrt(k)) if k > 1 else float("nan")
    return {"fixtures": int(k), "rows": int(idx.size), "pp": 100 * mu,
            "ci_lo_pp": 100 * (mu - Z * se) if k > 1 else None,
            "ci_hi_pp": 100 * (mu + Z * se) if k > 1 else None}


def backtest_one(spec: dict, fr: Frame) -> dict:
    m = mask(spec, fr)
    e = entries(fr, m)
    ret, fb = returns(fr, e, spec.get("exit") or {"type": "hold"})
    won, ask = _num(fr.cols["won"])[e], _num(fr.cols["ask"])[e]
    tr = fr.train[e]
    return {"all": summarize(ret, won, ask), "train": summarize(ret[tr], won[tr], ask[tr]),
            "test": summarize(ret[~tr], won[~tr], ask[~tr]), "cal": calibration(fr, m),
            "cash_out_fallbacks": fb,
            "cutoff": fr.cutoff.isoformat() if fr.cutoff is not None else None}


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


def run_batch(specs: list, frames: dict, fdr_q: float = FDR_Q) -> list:
    out = []
    for s in specs:
        fr = frames.get(s["universe"])
        if fr is None:
            continue
        r = backtest_one(s, fr)
        r["spec"] = s
        out.append(r)
    # FDR per universe: a pre-match rule and an in-play rule are different families
    for uname in {r["spec"]["universe"] for r in out}:
        U = frames[uname].U
        tested = [r for r in out if r["spec"]["universe"] == uname
                  and r["train"].get("n", 0) >= U.min_n_train]
        qs = bh_qvalues([r["train"]["p"] for r in tested])
        for r, q in zip(tested, qs):
            t = r["test"]
            r["tested"] = True
            r["q"] = q
            r["pass"] = bool(q <= fdr_q and t.get("n", 0) >= U.min_n_test and t.get("yield", -1) > 0)
            r["strong"] = bool(r["pass"] and t.get("p", 1) < 0.05)
    for r in out:
        r.setdefault("tested", False)
        r.setdefault("pass", False)
        r.setdefault("strong", False)
    return out
