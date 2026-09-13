"""
registry.py — where strategies live between the backtest and the money.

    candidate  passed out-of-sample, but its universe has no live tape yet
    paper      passed out-of-sample and is paper-trading on the live tape
    promoted   paper-traded to a forward result whose CI clears zero — the
               shortlist for real money, which is a person's decision, never ours
    retired    the forward record says no

Only what passed is stored; the full grid goes to a CSV report.
"""
from __future__ import annotations

import json
import math
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "../../ingest/.env"))
sys.path.insert(0, os.path.join(HERE, ".."))

import db_txn                                   # noqa: E402

from .engine import bh_qvalues                 # noqa: E402
from .spec import spec_hash                     # noqa: E402
from .universes import UNIVERSES                # noqa: E402

MAX_PAPER = 200            # live strategies at once; the rest wait as candidates
EXPLORE_PER_UNIVERSE = 10
FWD_FDR_Q = 0.10
PROMOTE_MIN_N = 50
RETIRE_MIN_N = 30
RETIRE_FLAT_N = 150


def connect():
    return db_txn.connect(os.getenv("DATABASE_URL"))


def _clean(x):
    """JSON-safe: NaN/inf become null."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def _score(r: dict) -> float:
    t = r["test"]
    return (t.get("yield") or 0) * math.sqrt(t.get("n") or 0)


def upsert_results(conn, results: list) -> dict:
    """Store every passing spec. A strategy that already exists keeps its status —
    a new backtest never resurrects a retired one or demotes a promoted one."""
    passing = sorted((r for r in results if r.get("pass")), key=_score, reverse=True)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM factory_strategies WHERE status = 'paper'")
        live_now = cur.fetchone()[0]
    new = upd = 0
    for r in passing:
        s = r["spec"]
        U = UNIVERSES[s["universe"]]
        bt = _clean({k: r[k] for k in ("all", "train", "test", "cal", "q", "strong",
                                       "cash_out_fallbacks", "cutoff")})
        want = "paper" if (U.live and live_now < MAX_PAPER) else "candidate"
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO factory_strategies (spec_hash, universe, name, template, spec, status,
                                                status_reason, bt, bt_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, now())
                ON CONFLICT (spec_hash) DO UPDATE
                   SET bt = EXCLUDED.bt, bt_at = now(), name = EXCLUDED.name
                RETURNING (xmax = 0)""",
                (spec_hash(s), s["universe"], s["name"], s.get("template"), json.dumps(s), want,
                 "passed out-of-sample" + ("" if want == "paper" else
                                           " — no live tape for this universe" if not U.live
                                           else " — paper slots full"),
                 json.dumps(bt)))
            inserted = cur.fetchone()[0]
        if inserted:
            new += 1
            live_now += want == "paper"
        else:
            upd += 1
    return {"passing": len(passing), "new": new, "updated": upd}


def upsert_explore(conn, results: list, per_universe: int = EXPLORE_PER_UNIVERSE) -> dict:
    """The explore tier: per live universe, the best specs that were positive on
    train, on test AND on the calibration arm without passing the FDR — ranked by
    the all-period z, one per trigger (price-gate and exit variants of the same
    `where` count once). The forward record decides what they are."""
    added = 0
    for uname, U in UNIVERSES.items():
        if not U.live:
            continue
        pool = [r for r in results if r["spec"]["universe"] == uname and r.get("tested")
                and not r.get("pass") and (r["train"].get("yield") or 0) > 0
                and (r["test"].get("yield") or -1) > 0 and r["test"].get("n", 0) >= U.min_n_test
                and (r["cal"].get("pp") or -1) > 0 and r["all"].get("se")]
        pool.sort(key=lambda r: r["all"]["yield"] / r["all"]["se"], reverse=True)
        seen, picked = set(), []
        for r in pool:
            key = json.dumps(r["spec"]["where"], sort_keys=True)
            if key not in seen:
                seen.add(key)
                picked.append(r)
            if len(picked) >= per_universe:
                break
        for r in picked:
            s = r["spec"]
            bt = _clean({k: r.get(k) for k in ("all", "train", "test", "cal", "q", "strong",
                                               "cash_out_fallbacks", "cutoff")})
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO factory_strategies (spec_hash, universe, name, template, spec, status,
                                                    status_reason, bt, bt_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, 'explore', %s, %s::jsonb, now())
                    ON CONFLICT (spec_hash) DO UPDATE SET bt = EXCLUDED.bt, bt_at = now()
                    RETURNING (xmax = 0)""",
                    (spec_hash(s), uname, s["name"], s.get("template"), json.dumps(s),
                     f"explore: +train +test +cal, FDR q={r.get('q', 1):.2f} — the forward record decides",
                     json.dumps(bt)))
                added += cur.fetchone()[0]
    return {"explore_added": added}


def active(conn) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, universe, name, spec, status_changed_at FROM factory_strategies
                        WHERE status IN ('explore', 'paper', 'promoted')""")
        return [dict(r) for r in cur.fetchall()]


def entered(conn, sids: list, fixtures: list) -> set:
    if not sids or not fixtures:
        return set()
    with conn.cursor() as cur:
        cur.execute("""SELECT strategy_id, fixture FROM factory_trades
                        WHERE strategy_id = ANY(%s) AND fixture = ANY(%s)""", (sids, fixtures))
        return {(a, b) for a, b in cur.fetchall()}


def insert_trades(conn, rows: list) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO factory_trades (strategy_id, fixture, universe, source_table, source_row_id,
                token_id, condition_id, entry_minute, entry_ask, entry_odds, stake, fee_units, label,
                observed_at)
            VALUES %s ON CONFLICT (strategy_id, fixture) DO NOTHING""", rows)
        return cur.rowcount


def pending(conn) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT t.id, t.strategy_id, t.universe, t.fixture, t.source_row_id, t.stake,
                              s.spec
                         FROM factory_trades t JOIN factory_strategies s ON s.id = t.strategy_id
                        WHERE t.settled_at IS NULL AND t.entry_at < now() - interval '15 minutes'""")
        return [dict(r) for r in cur.fetchall()]


def settle_trade(conn, tid: int, result: str, ret: float, stake: float) -> None:
    with conn.cursor() as cur:
        cur.execute("""UPDATE factory_trades SET result = %s, ret = %s, pnl = %s, settled_at = now()
                        WHERE id = %s AND settled_at IS NULL""",
                    (result, round(ret, 6), round(ret * stake, 6), tid))


def refresh_forward(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE factory_strategies s
               SET fwd_n = x.n, fwd_won = x.w, fwd_stake = x.st, fwd_pnl = x.pnl, fwd_yield = x.y,
                   fwd_ci_lo = x.y - 1.96 * x.se, fwd_ci_hi = x.y + 1.96 * x.se, fwd_updated_at = now()
              FROM (SELECT strategy_id, count(*) n, sum((result = 'won')::int) w, sum(stake) st,
                           sum(pnl) pnl, avg(ret) y,
                           COALESCE(stddev_samp(ret) / sqrt(count(*)), 1) se
                      FROM factory_trades WHERE settled_at IS NOT NULL GROUP BY 1) x
             WHERE s.id = x.strategy_id""")


def apply_promotion(conn) -> list:
    """explore/paper → promoted when the forward yield survives Benjamini-Hochberg
    across EVERY active strategy (200 bots produce forward champions by chance
    alone); → retired when the forward record clearly says no. Returns the
    changes, for the log."""
    changes = []
    with conn.cursor() as cur:
        cur.execute("""SELECT id, fwd_yield, fwd_ci_lo, fwd_ci_hi FROM factory_strategies
                        WHERE status IN ('explore', 'paper', 'promoted') AND fwd_n >= %s
                          AND fwd_ci_hi > fwd_ci_lo""", (RETIRE_MIN_N,))
        rows = cur.fetchall()
    if rows:
        ps = []
        for _, y, lo, hi in rows:
            se = (float(hi) - float(lo)) / (2 * 1.96)
            ps.append(0.5 * math.erfc((float(y) / se) / math.sqrt(2)))
        qs = bh_qvalues(ps)
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                UPDATE factory_strategies s SET fwd_q = v.q FROM (VALUES %s) AS v(id, q)
                 WHERE s.id = v.id""", [(r[0], q) for r, q in zip(rows, qs)])
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE factory_strategies SET status = 'promoted', status_changed_at = now(),
                   status_reason = format('forward n=%%s yield %%s%%%% FDR q %%s', fwd_n,
                                          round(100 * fwd_yield, 1), round(fwd_q, 3))
             WHERE status IN ('explore', 'paper') AND fwd_n >= %s AND fwd_yield > 0 AND fwd_q <= %s
            RETURNING id, name, 'promoted'""", (PROMOTE_MIN_N, FWD_FDR_Q))
        changes += cur.fetchall()
        cur.execute("""
            UPDATE factory_strategies SET status = 'retired', status_changed_at = now(),
                   status_reason = format('forward n=%%s yield %%s%%%% CI hi %%s%%%%', fwd_n,
                                          round(100 * fwd_yield, 1), round(100 * fwd_ci_hi, 1))
             WHERE status IN ('explore', 'paper', 'promoted')
               AND ((fwd_n >= %s AND fwd_ci_hi < 0) OR (fwd_n >= %s AND fwd_yield <= 0))
            RETURNING id, name, 'retired'""", (RETIRE_MIN_N, RETIRE_FLAT_N))
        changes += cur.fetchall()
        # a retired paper slot is a free slot: the best waiting candidate takes it
        cur.execute("""
            WITH free AS (SELECT GREATEST(%s - count(*), 0) k FROM factory_strategies WHERE status = 'paper')
            UPDATE factory_strategies SET status = 'paper', status_changed_at = now(),
                   status_reason = 'paper slot freed'
             WHERE id IN (SELECT s.id FROM factory_strategies s
                           WHERE s.status = 'candidate' AND s.universe = ANY(%s)
                           ORDER BY (s.bt->'test'->>'yield')::float * sqrt((s.bt->'test'->>'n')::float) DESC
                           LIMIT (SELECT k FROM free))
            RETURNING id, name, 'paper'""",
            (MAX_PAPER, [u for u, U in UNIVERSES.items() if U.live]))
        changes += cur.fetchall()
    return changes


def leaderboard(conn, limit: int = 30) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, status, universe, name, fwd_n, fwd_yield, fwd_ci_lo, fwd_ci_hi,
                   (bt->'train'->>'n')::int AS bt_train_n, (bt->'train'->>'yield')::float AS bt_train_y,
                   (bt->'test'->>'n')::int AS bt_test_n, (bt->'test'->>'yield')::float AS bt_test_y,
                   (bt->>'q')::float AS q, (bt->>'strong')::bool AS strong, status_reason
              FROM factory_strategies
             ORDER BY CASE status WHEN 'promoted' THEN 0 WHEN 'paper' THEN 1 WHEN 'explore' THEN 2
                                  WHEN 'candidate' THEN 3 ELSE 4 END,
                      COALESCE(fwd_ci_lo, -9) DESC,
                      (bt->'test'->>'yield')::float * sqrt((bt->'test'->>'n')::float) DESC NULLS LAST
             LIMIT %s""", (limit,))
        return [dict(r) for r in cur.fetchall()]
