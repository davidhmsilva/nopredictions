#!/usr/bin/env python3
"""
Live pressure agent — watches football fixtures in play, measures how hard a
team is pressing, and paper-trades PM over lines when pressure says a goal is
coming.

Two jobs, deliberately not one:

  1. RECORD. Every poll of every live fixture writes a row to
     pressure_observations, whether or not it trades. That series is the only
     way the pressure model can ever be fitted — `goal_events` has goals at
     minute resolution but no in-game stats at all, so there is nothing in our
     history to backtest a pressure signal against. It has to be recorded
     forward.

  2. TRADE (paper). When the pressure-adjusted fair value clears the ask by
     MIN_EDGE_PP after the taker fee, open a 1u paper position on
     Over(current total + 0.5) and let it settle on the real result.

What the pressure multiplier is worth is UNKNOWN. The weights in
live_tracker._danger_index were written by hand and never estimated, and
PRESSURE_GAIN below is a guess. That is why every row stores the fair value
both with and without the pressure term: the base arm is the control, the
pressure arm is the treatment, and the difference between them on settled rows
is the actual result. At PRESSURE_GAIN = 0 the agent degenerates to the base
table strategy, which is the honest null.

The clock problem that ruined late_goal_observations v1 does not arise here:
the minute and the score come from api-football's live feed, not from PM's
listed start time. The Gamma ladder is read only as a cross-check and its
disagreement is recorded, never silently trusted.

Usage:
    python pressure_agent.py --once            # one cycle
    python pressure_agent.py --once --dry-run  # no DB writes, no trades
    python pressure_agent.py                   # forever, 60s cycles
    python pressure_agent.py --settle          # backfill outcomes
    python pressure_agent.py --report          # what has been collected
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

import late_goals_table as lgt                                      # noqa: E402
from edge_engine import taker_fee_pp                                # noqa: E402
from late_goals_observer import (                                   # noqa: E402
    _fetch_book,
    _fetch_events,
    _norm,
    infer_goals,
    ladder_consistent,
    ladder_of,
    pm_over25,
)
from live_tracker import LiveMatchTracker, PressureSignals          # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("pressure")

DATABASE_URL = os.getenv("DATABASE_URL")
STRATEGY_NAME = "Live Pressure Overs"
OBS_VERSION = 1

CYCLE_S = 60
REFRESH_MARKETS_S = 300         # PM universe re-pull

# ── the pressure term ────────────────────────────────────────────────────────
# pressure_index is the mean of the two danger indices — a goal can come from
# either end, and the over line does not care which. It is turned into a
# multiplier on the remaining GOAL RATE rather than on the probability, because
# probabilities do not scale: doubling a 0.9 probability is meaningless, whereas
# doubling the rate behind it is exactly what "twice as much pressure" means.
#
#   lambda = -ln(1 - fair_base)      remaining rate implied by the base table
#   fair   = 1 - exp(-lambda * k)    same rate, scaled by pressure
#
# PRESSURE_NEUTRAL is the danger level at which k = 1, i.e. the level the base
# table already assumes. It is a GUESS — the average danger index across live
# football is not something we have measured. Both it and PRESSURE_GAIN are
# meant to be refit from pressure_observations once the sample exists; until
# then they are held small enough that the pressure term cannot dominate the
# empirical base rate.
PRESSURE_NEUTRAL = 35.0
PRESSURE_GAIN = 0.50
K_MIN, K_MAX = 0.70, 1.60

# ── entry gates ──────────────────────────────────────────────────────────────
MIN_EDGE_PP = 2.0               # after the taker fee
MIN_DEPTH_USD = 50.0
MIN_MINUTE = 20                 # below this the stat window is not informative
MAX_MINUTE = 88                 # past this there is no time for a goal to arrive
MIN_PRESSURE = 45.0             # do not trade "a goal might happen eventually"
MAX_ASK = 0.85                  # PM asks above 0.85 resolve far below their price
STAKE_UNITS = 1.0
SETTLE_HORIZON_MIN = 10         # the "goal is coming" horizon


def _conn():
    return psycopg2.connect(DATABASE_URL)


# ── pricing ──────────────────────────────────────────────────────────────────

def pressure_factor(pressure_index: float) -> float:
    """Multiplier on the remaining goal rate. 1.0 = the base table, unmodified."""
    k = 1.0 + PRESSURE_GAIN * (pressure_index - PRESSURE_NEUTRAL) / PRESSURE_NEUTRAL
    return max(K_MIN, min(K_MAX, k))


def apply_pressure(fair_base: float, k: float) -> float:
    """Rescale a P(>=1 more goal) by a rate multiplier, staying inside (0, 1).

    The clamp is not cosmetic: a large enough rate underflows exp() to zero and
    returns exactly 1.0, and a fair value of 1.0 flows downstream into 1/p for
    the displayed odds and into an edge that can never be beaten. k is bounded
    well below that today, but this is the pricing primitive and it should not
    depend on its caller's clamp being correct.
    """
    if not (0.0 < fair_base < 1.0):
        return fair_base
    lam = -math.log(1.0 - fair_base)
    return min(1.0 - 1e-9, max(1e-9, 1.0 - math.exp(-lam * k)))


def pressure_index_of(sig: PressureSignals) -> float:
    """One scalar for 'how likely does this look right now', from both ends."""
    return (sig.home_danger_index + sig.away_danger_index) / 2.0


# ── PM <-> api-football matching ─────────────────────────────────────────────

def match_pm_fixture(sig: PressureSignals, pm_fixtures: list[dict]) -> dict | None:
    """Find the PM fixture for an api-football one. Loose, because the two feeds
    disagree on club naming constantly ("Man City" / "Manchester City" / "Man.
    City"). Both team names must hit, so a loose match is still a safe one."""
    want_h, want_a = _norm(sig.home), _norm(sig.away)
    words = lambda s: [w for w in s.split() if len(w) > 3]           # noqa: E731

    for fx in pm_fixtures:
        title = _norm(fx["title"])
        if not words(want_h) or not words(want_a):
            continue
        if any(w in title for w in words(want_h)) and any(w in title for w in words(want_a)):
            return fx
    return None


# ── one cycle ────────────────────────────────────────────────────────────────

def observe(tracker: LiveMatchTracker, table: dict, pm_fixtures: list[dict],
            pre_cache: dict[str, float]) -> list[dict]:
    signals = tracker.poll()
    if not signals:
        return []

    rows: list[dict] = []
    for sig in signals.values():
        fx = match_pm_fixture(sig, pm_fixtures)
        row = _base_row(sig, tracker.window_minutes)
        goals = sig.home_goals + sig.away_goals

        # No stats coverage means no pressure measurement, and a row claiming
        # pressure 5/100 for a match nobody measured is worse than no row: it
        # would enter the regression as evidence.
        if not sig.has_stats:
            row["pressure_index"] = None
            row["home_danger"] = row["away_danger"] = None
            row["skip_reason"] = "no api-football stats coverage"
            rows.append(row)
            continue

        if fx is None:
            row["skip_reason"] = "no PM fixture"
            rows.append(row)
            continue

        cells = ladder_of(fx)
        ladder = {ln: c["over_price"] for ln, c in cells.items()}
        row["event_title"] = fx["title"]

        # Pre-match total, the strongest single predictor of a late goal (14pp
        # across buckets, vs 4.5pp for league identity). Cached per fixture — by
        # the time a match is live its own pre-kickoff price is long gone.
        pre = pre_cache.get(fx["title"])
        if pre is None:
            pre = pm_over25(cells)
            if pre is not None:
                pre_cache[fx["title"]] = pre
        row["pre_over25"] = pre

        # api-football owns the score. The ladder is read only to notice when
        # the two disagree — on the late-goals v1 series the Gamma ladder was
        # wrong on 29% of polls, so it is evidence, not an authority.
        if ladder and ladder_consistent(ladder):
            lower, upper, certain = infer_goals(ladder)
            if certain:
                row["ladder_goals"] = lower
                row["score_agrees"] = (lower == goals)

        if not (MIN_MINUTE <= sig.minute <= MAX_MINUTE):
            row["skip_reason"] = f"minute {sig.minute} outside {MIN_MINUTE}-{MAX_MINUTE}"
            rows.append(row)
            continue

        target = cells.get(goals + 0.5)
        if not target or not target.get("token_id"):
            row["skip_reason"] = f"no PM line at over {goals + 0.5}"
            rows.append(row)
            continue
        row["target_line"] = target["line"]
        row["condition_id"] = target.get("condition_id")
        row["token_id"] = target.get("token_id")

        # The CLOB, not Gamma. A Gamma mid says nothing about what is executable,
        # and booking paper fills at unexecutable prices is exactly what turned
        # the convergence trader's +141% into a real +3.4% (db/028).
        book = _fetch_book(target)
        if not book:
            row["skip_reason"] = "no book"
            rows.append(row)
            continue
        row.update(best_bid=book["best_bid"], best_ask=book["best_ask"],
                   bid_depth_usd=book["bid_depth_usd"],
                   ask_depth_usd=book["ask_depth_usd"])

        fair_base, fair_n = lgt.lookup(table, sig.minute, goals, pre, needed=1)
        if fair_base is None:
            row["skip_reason"] = f"state off the fair-value grid ({sig.minute}', {goals} goals)"
            rows.append(row)
            continue

        k = pressure_factor(row["pressure_index"])
        fair_pressure = apply_pressure(fair_base, k)
        fee = taker_fee_pp(book["best_ask"])

        row.update(
            fair_base=fair_base, fair_pressure=fair_pressure, fair_n=fair_n,
            pressure_factor=k, fee_pp=fee,
            edge_base_pp=100.0 * (fair_base - book["best_ask"]) - fee,
            edge_pressure_pp=100.0 * (fair_pressure - book["best_ask"]) - fee,
        )

        row["would_enter"] = bool(
            row["edge_pressure_pp"] >= MIN_EDGE_PP
            and row["pressure_index"] >= MIN_PRESSURE
            # A real window delta, not the scaled fallback live_tracker uses
            # before it has a baseline — that fallback is the match average
            # wearing a window's clothes, and it would read steady play as a
            # surge every time.
            and row["has_window"]
            # PM asks above 0.85 resolve at 0.66 (n=382). Nothing up there is
            # priced to be bought.
            and book["best_ask"] <= MAX_ASK
            and (book["ask_depth_usd"] or 0) >= MIN_DEPTH_USD
            # A score we cannot pin makes the fair value meaningless: it is a
            # lookup keyed on the score.
            and row["score_agrees"] is not False
        )
        if not row["would_enter"] and not row["skip_reason"]:
            row["skip_reason"] = _why_not(row, book, sig)
        rows.append(row)

    return rows


def _why_not(row: dict, book: dict, sig: PressureSignals) -> str:
    if row["edge_pressure_pp"] < MIN_EDGE_PP:
        return f"edge {row['edge_pressure_pp']:+.1f}pp < {MIN_EDGE_PP}"
    if row["pressure_index"] < MIN_PRESSURE:
        return f"pressure {row['pressure_index']:.0f} < {MIN_PRESSURE}"
    if not row["has_window"]:
        return "no window baseline yet"
    if book["best_ask"] > MAX_ASK:
        return f"ask {book['best_ask']:.2f} > {MAX_ASK}"
    if (book["ask_depth_usd"] or 0) < MIN_DEPTH_USD:
        return f"depth ${book['ask_depth_usd']:.0f} < ${MIN_DEPTH_USD:.0f}"
    if row["score_agrees"] is False:
        return f"score disagreement: api={row['goals_total']} ladder={row['ladder_goals']}"
    return ""


def _base_row(sig: PressureSignals, window_min: int) -> dict:
    return {
        "obs_version": OBS_VERSION,
        "fixture_id": sig.fixture_id,
        "home": sig.home, "away": sig.away, "league": None,
        "event_title": None, "condition_id": None, "token_id": None,
        "minute": sig.minute,
        "home_goals": sig.home_goals, "away_goals": sig.away_goals,
        "goals_total": sig.home_goals + sig.away_goals,
        "ladder_goals": None, "score_agrees": None,
        "home_xg": sig.home_xg_total, "away_xg": sig.away_xg_total,
        "home_shots_on": sig.home_shots_on_total, "away_shots_on": sig.away_shots_on_total,
        "home_shots_total": sig.home_shots_total, "away_shots_total": sig.away_shots_total,
        "home_shots_inside": sig.home_shots_inside_total,
        "away_shots_inside": sig.away_shots_inside_total,
        "home_corners": sig.home_corners_total, "away_corners": sig.away_corners_total,
        "home_possession": sig.home_possession, "away_possession": sig.away_possession,
        "home_reds": sig.home_reds, "away_reds": sig.away_reds,
        "window_min": window_min,
        "home_xg_window": sig.home_xg_window, "away_xg_window": sig.away_xg_window,
        "home_shots_on_window": sig.home_shots_on_window,
        "away_shots_on_window": sig.away_shots_on_window,
        "home_shots_inside_window": sig.home_shots_inside_window,
        "away_shots_inside_window": sig.away_shots_inside_window,
        "home_corners_window": sig.home_corners_window,
        "away_corners_window": sig.away_corners_window,
        "has_window": sig.has_window,
        "home_danger": sig.home_danger_index, "away_danger": sig.away_danger_index,
        "pressure_index": pressure_index_of(sig),
        "pressure_factor": None,
        "pre_over25": None, "target_line": None,
        "best_bid": None, "best_ask": None,
        "bid_depth_usd": None, "ask_depth_usd": None,
        "fair_base": None, "fair_pressure": None, "fair_n": None, "fee_pp": None,
        "edge_base_pp": None, "edge_pressure_pp": None,
        "would_enter": False, "entered": False,
        "paper_trade_id": None, "skip_reason": None,
    }


# ── writing ──────────────────────────────────────────────────────────────────

_COLS = [
    "obs_version", "fixture_id", "league", "home", "away", "event_title",
    "condition_id", "token_id", "minute", "home_goals", "away_goals",
    "goals_total", "ladder_goals", "score_agrees", "home_xg", "away_xg",
    "home_shots_on", "away_shots_on", "home_shots_total", "away_shots_total",
    "home_shots_inside", "away_shots_inside", "home_corners", "away_corners",
    "home_possession", "away_possession", "home_reds", "away_reds",
    "window_min", "home_xg_window", "away_xg_window", "home_shots_on_window",
    "away_shots_on_window", "home_shots_inside_window", "away_shots_inside_window",
    "home_corners_window", "away_corners_window", "has_window",
    "home_danger", "away_danger", "pressure_index", "pressure_factor",
    "pre_over25", "target_line", "best_bid", "best_ask", "bid_depth_usd",
    "ask_depth_usd", "fair_base", "fair_pressure", "fair_n", "fee_pp",
    "edge_base_pp", "edge_pressure_pp", "would_enter", "entered",
    "paper_trade_id", "skip_reason",
]


def _write(conn, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO pressure_observations ({','.join(_COLS)}) VALUES %s",
            [[r.get(c) for c in _COLS] for r in rows],
        )
    conn.commit()


def _strategy_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
        got = cur.fetchone()
    if not got:
        raise SystemExit(f"strategy '{STRATEGY_NAME}' missing — apply db/031")
    return got[0]


def open_trades(conn, strategy_id: int, rows: list[dict]) -> int:
    """Open one 1u paper position per qualifying row.

    The unique index on (fixture_id, target_line) WHERE entered is what stops
    the agent re-buying the same line every cycle while a team camps in the box.
    """
    opened = 0
    for r in rows:
        if not r["would_enter"]:
            continue
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pressure_observations "
                "WHERE fixture_id = %s AND target_line = %s AND entered LIMIT 1",
                (r["fixture_id"], r["target_line"]),
            )
            if cur.fetchone():
                r["would_enter"], r["skip_reason"] = False, "already entered this line"
                continue

            reasoning = (
                f"{r['home']} {r['home_goals']}-{r['away_goals']} {r['away']} "
                f"{r['minute']}' — Over {r['target_line']} at "
                f"{r['best_ask']:.3f} ({1 / r['best_ask']:.2f}). "
                f"Pressure {r['pressure_index']:.0f}/100 "
                f"(danger H={r['home_danger']:.0f} A={r['away_danger']:.0f}, "
                f"window xG H={r['home_xg_window']:.2f} A={r['away_xg_window']:.2f}). "
                f"Base fair {r['fair_base']:.3f} ({1 / r['fair_base']:.2f}, n={r['fair_n']}) "
                f"-> pressure fair {r['fair_pressure']:.3f} ({1 / r['fair_pressure']:.2f}) "
                f"at k={r['pressure_factor']:.2f}. "
                f"Edge {r['edge_pressure_pp']:+.1f}pp after {r['fee_pp']:.2f}pp fee "
                f"(base arm would be {r['edge_base_pp']:+.1f}pp). "
                f"PAPER — pressure multiplier is unfitted."
            )
            cur.execute(
                """INSERT INTO paper_trades
                     (strategy_id, outcome, entry_price, entry_odds, stake_units,
                      model_probability, expected_edge, reasoning, confidence,
                      pm_token_id, pm_live)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)
                   RETURNING id""",
                (strategy_id,
                 f"Over {r['target_line']} — {r['event_title'] or r['home'] + ' vs ' + r['away']}",
                 r["best_ask"], 1.0 / r["best_ask"], STAKE_UNITS,
                 r["fair_pressure"], r["edge_pressure_pp"] / 100.0,
                 reasoning, "paper", r["token_id"]),
            )
            r["paper_trade_id"] = cur.fetchone()[0]
            r["entered"] = True
            opened += 1
        conn.commit()
    return opened


# ── settlement ───────────────────────────────────────────────────────────────

def settle(conn) -> int:
    """Fill in both horizons for rows whose fixture has moved on.

    goal_next_10 is read off our own later observations of the same fixture —
    the score at minute+10 is a row we recorded. goal_before_ft needs the final
    score, which only arrives once the fixture is over.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, fixture_id, minute, goals_total, paper_trade_id
                 FROM pressure_observations
                WHERE settled_at IS NULL
                  AND observed_at < now() - interval '15 minutes'
                ORDER BY id"""
        )
        pending = cur.fetchall()

    if not pending:
        return 0

    # Later observations of the same fixture, and the last one we ever saw.
    with conn.cursor() as cur:
        cur.execute(
            """SELECT fixture_id, minute, goals_total
                 FROM pressure_observations
                WHERE fixture_id = ANY(%s)
                ORDER BY fixture_id, minute""",
            ([r["fixture_id"] for r in pending],),
        )
        series: dict[int, list[tuple[int, int]]] = {}
        for fid, minute, goals in cur.fetchall():
            series.setdefault(fid, []).append((minute, goals))

    settled = 0
    with conn.cursor() as cur:
        for r in pending:
            obs = series.get(r["fixture_id"], [])
            later = [(m, g) for m, g in obs if m >= r["minute"] + SETTLE_HORIZON_MIN]
            last_minute = max((m for m, _ in obs), default=r["minute"])

            at_plus_10 = goal_next_10 = None
            if later:
                at_plus_10 = min(later, key=lambda x: x[0])[1]
                goal_next_10 = at_plus_10 > r["goals_total"]

            # The tape has to have run past 88' before "no goal before full
            # time" means anything. A fixture we stopped watching at 70'
            # because the Mac went to sleep is not a settled no-goal — calling
            # it one would bias every result toward the null.
            final_goals = goal_before_ft = None
            if last_minute >= MAX_MINUTE:
                final_goals = max(g for _, g in obs)
                goal_before_ft = final_goals > r["goals_total"]

            if goal_next_10 is None and goal_before_ft is None:
                continue

            cur.execute(
                """UPDATE pressure_observations
                      SET goals_at_plus_10 = %s, goal_next_10 = %s,
                          final_goals = %s, goal_before_ft = %s,
                          settled_at = now()
                    WHERE id = %s""",
                (at_plus_10, goal_next_10, final_goals, goal_before_ft, r["id"]),
            )
            settled += 1

            # payout_units is GROSS by project convention — lost = 0,
            # won = stake * entry_odds. Booking it net is the bug that had to be
            # repaired across 38 rows on 2026-05-27 and recurred once since, so
            # the odds are read back from the trade rather than recomputed here.
            if goal_before_ft is not None and r["paper_trade_id"]:
                cur.execute(
                    """UPDATE paper_trades
                          SET result = %s,
                              payout_units = CASE WHEN %s
                                                  THEN stake_units * entry_odds
                                                  ELSE 0 END,
                              resolved_at = now()
                        WHERE id = %s AND result IS NULL""",
                    ("won" if goal_before_ft else "lost", goal_before_ft,
                     r["paper_trade_id"]),
                )
    conn.commit()
    return settled


def report(conn) -> None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT count(*) AS rows,
                      count(DISTINCT fixture_id) AS fixtures,
                      count(*) FILTER (WHERE has_window) AS with_window,
                      count(*) FILTER (WHERE fair_base IS NOT NULL) AS priced,
                      count(*) FILTER (WHERE entered) AS entered,
                      count(*) FILTER (WHERE goal_next_10 IS NOT NULL) AS settled_10,
                      avg(pressure_index) AS mean_pressure
                 FROM pressure_observations"""
        )
        s = cur.fetchone()
        print(f"\nrows={s['rows']}  fixtures={s['fixtures']}  with_window={s['with_window']}  "
              f"priced={s['priced']}  entries={s['entered']}  settled@10={s['settled_10']}")
        if s["mean_pressure"] is not None:
            print(f"mean pressure index = {s['mean_pressure']:.1f} "
                  f"(PRESSURE_NEUTRAL is set to {PRESSURE_NEUTRAL} — refit it from this)")

        # The primary test, as pre-registered: does pressure separate at all?
        cur.execute(
            """SELECT width_bucket(pressure_index, 0, 100, 5) AS b,
                      count(*) AS n,
                      avg(goal_next_10::int)::float8 AS p_goal,
                      min(pressure_index) AS lo, max(pressure_index) AS hi
                 FROM pressure_observations
                WHERE goal_next_10 IS NOT NULL
                GROUP BY 1 ORDER BY 1"""
        )
        rows = cur.fetchall()
        if rows:
            print(f"\nP(goal within {SETTLE_HORIZON_MIN}min) by pressure bucket:")
            for r in rows:
                print(f"  {r['lo']:5.0f}-{r['hi']:5.0f}  n={r['n']:5d}  "
                      f"{r['p_goal']:.3f}" + (f"  ({1 / r['p_goal']:.2f})" if r["p_goal"] else ""))
            print("  (n >= 200 per bucket before reading anything into this)")

        cur.execute(
            """SELECT count(*) AS n,
                      count(*) FILTER (WHERE pt.result = 'won') AS won,
                      sum(pt.payout_units - pt.stake_units)::float8 AS pnl,
                      sum(pt.stake_units)::float8 AS staked
                 FROM pressure_observations po
                 JOIN paper_trades pt ON pt.id = po.paper_trade_id
                WHERE pt.result IS NOT NULL"""
        )
        t = cur.fetchone()
        if t and t["n"]:
            yld = 100.0 * t["pnl"] / t["staked"] if t["staked"] else 0.0
            print(f"\npaper: n={t['n']} won={t['won']} P&L={t['pnl']:+.2f}u yield={yld:+.1f}%")
            print("  (verdict gate: n >= 200 AND yield CI clear of zero AND the "
                  "pressure arm beating the base arm)")


# ── loop ─────────────────────────────────────────────────────────────────────

def run(once: bool, dry_run: bool, interval: int) -> None:
    table = lgt.load(lgt.WIDE_TABLE_PATH)
    log.info(f"baseline {table['built_at']} — {len(table['cells'])} cells, "
             f"{table['minutes'][0]}'-{table['minutes'][-1]}'")

    conn = None if dry_run else _conn()
    strategy_id = None if dry_run else _strategy_id(conn)
    tracker = LiveMatchTracker()
    pre_cache: dict[str, float] = {}
    pm_fixtures: list[dict] = []
    last_markets = 0.0

    while True:
        t0 = time.time()
        if t0 - last_markets > REFRESH_MARKETS_S or not pm_fixtures:
            pm_fixtures = _fetch_events()
            last_markets = t0
            log.info(f"PM universe -> {len(pm_fixtures)} football fixtures")

        rows = observe(tracker, table, pm_fixtures, pre_cache)

        opened = 0
        if rows and conn is not None:
            opened = open_trades(conn, strategy_id, rows)
            _write(conn, rows)

        priced = [r for r in rows if r["fair_base"] is not None]
        for r in rows:
            if r["entered"]:
                log.info(
                    f"  ENTER  {r['home'][:18]:18} {r['home_goals']}-{r['away_goals']} "
                    f"{r['away'][:18]:18} {r['minute']}'  O{r['target_line']} "
                    f"ask={r['best_ask']:.3f} ({1 / r['best_ask']:.2f})  "
                    f"press={r['pressure_index']:.0f}  "
                    f"edge={r['edge_pressure_pp']:+.1f}pp "
                    f"(base {r['edge_base_pp']:+.1f}pp)  #{r['paper_trade_id']}"
                )
        log.info(f"live={len(rows):3d} priced={len(priced):3d} "
                 f"entered={opened:2d} {time.time() - t0:.1f}s")

        if once:
            break
        time.sleep(max(5, interval - (time.time() - t0)))

    if conn:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Live in-game pressure agent (paper only)")
    ap.add_argument("--once", action="store_true", help="one cycle then exit")
    ap.add_argument("--dry-run", action="store_true", help="no DB writes, no trades")
    ap.add_argument("--interval", type=int, default=CYCLE_S, help="seconds between cycles")
    ap.add_argument("--settle", action="store_true", help="backfill outcomes and exit")
    ap.add_argument("--report", action="store_true", help="what has been collected")
    args = ap.parse_args()

    if args.settle or args.report:
        conn = _conn()
        try:
            if args.settle:
                log.info(f"settled {settle(conn)} observations")
            if args.report:
                report(conn)
        finally:
            conn.close()
        return

    run(once=args.once, dry_run=args.dry_run, interval=args.interval)


if __name__ == "__main__":
    main()
