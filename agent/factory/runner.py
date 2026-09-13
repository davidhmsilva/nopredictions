"""
runner.py — the paper bots.

Every minute: read the last few minutes of each live tape — the same tables the
in-play daemons are writing, through the same loaders the backtest used — and
for every paper/promoted strategy enter the first qualifying row of each fixture
it has not entered yet, at that row's CLOB ask. The bot a spec describes and the
bot that trades it are the same code path, so there is no train/serve skew to
argue about.

Settlement re-reads the settled rows of those fixtures and prices each trade with
the same `returns()` the backtest used (hold, or a cash-out off the tape).
"""
from __future__ import annotations

import logging

import numpy as np

from . import registry
from .engine import FEE_RATE, _num, entries, make_frame, mask, returns
from .universes import UNIVERSES

log = logging.getLogger("factory")

LIVE_MINUTES = 4          # how far back a live read looks; rows older than this are not tradeable


def _fid(fixture: str):
    try:
        return int(fixture.split(":")[1])
    except (IndexError, ValueError):
        return None


def run_live(conn, dry_run: bool = False) -> int:
    strategies = registry.active(conn)
    if not strategies:
        log.info("no paper or promoted strategies")
        return 0
    by_u: dict = {}
    for s in strategies:
        by_u.setdefault(s["universe"], []).append(s)
    placed = 0
    beat = []                 # one heartbeat line per run: silence must never mean "fine"
    for uname, lst in by_u.items():
        U = UNIVERSES[uname]
        if not U.live:
            continue
        df = U.load(conn, mode="live", live_minutes=LIVE_MINUTES)
        beat.append(f"{uname.replace('soccer_', '')} {len(lst)} bots/{0 if df is None else len(df)} rows")
        if df is None or df.empty:
            continue
        fr = make_frame(U, df, split=False)
        ts = fr.df["ts"]
        done = registry.entered(conn, [s["id"] for s in lst], sorted(set(fr.df["fixture"])))
        rows = []
        for s in lst:
            m = mask(s["spec"], fr, require_won=False)
            m &= (ts >= s["status_changed_at"]).to_numpy()      # never trade what it saw before going live
            for i in entries(fr, m):
                fx = fr.cols["fixture"][i]
                if (s["id"], fx) in done:
                    continue
                ask = float(_num(fr.cols["ask"])[i])
                stake = float(s["spec"]["stake"]["units"])
                minute = fr.cols["minute"][i]
                rows.append((s["id"], fx, uname, U.source_table, int(fr.cols["source_row_id"][i]),
                             fr.cols["token_id"][i], fr.cols["condition_id"][i],
                             None if np.isnan(minute) else int(minute), ask, round(1 / ask, 4), stake,
                             round(stake * FEE_RATE * (1 - ask), 6), str(fr.cols["label"][i])[:200],
                             fr.df["ts"].iloc[i].to_pydatetime()))
                done.add((s["id"], fx))
        if dry_run:
            for r in rows[:20]:
                log.info(f"DRY #{r[0]} {r[12]} @ {r[8]:.3f} ({r[9]:.2f}) min {r[7]}")
            placed += len(rows)
            continue
        n = registry.insert_trades(conn, rows)
        placed += n
        if n:
            log.info(f"{uname}: {n} paper entries")
    log.info(f"run: {' · '.join(beat) or 'no live universe'} → {placed} entries")
    return placed


def settle(conn) -> int:
    todo = registry.pending(conn)
    by_u: dict = {}
    for t in todo:
        by_u.setdefault(t["universe"], []).append(t)
    n = 0
    for uname, lst in by_u.items():
        U = UNIVERSES[uname]
        fids = sorted({f for f in (_fid(t["fixture"]) for t in lst) if f is not None})
        df = U.load(conn, mode="settle", fixture_ids=fids)
        if df is None or df.empty:
            continue
        fr = make_frame(U, df, split=False)
        at = {int(r): i for i, r in enumerate(fr.cols["source_row_id"])}
        won = _num(fr.cols["won"])
        for t in lst:
            i = at.get(int(t["source_row_id"]))
            if i is None or np.isnan(won[i]):
                continue          # the daemon has not settled that row yet
            ret, _ = returns(fr, np.array([i]), t["spec"].get("exit") or {"type": "hold"})
            w = won[i]
            result = "won" if w == 1 else "lost" if w == 0 else "void"
            registry.settle_trade(conn, t["id"], result, float(ret[0]), float(t["stake"]))
            n += 1
    registry.refresh_forward(conn)
    for sid, name, status in registry.apply_promotion(conn):
        log.info(f"#{sid} → {status}: {name}")
    return n
