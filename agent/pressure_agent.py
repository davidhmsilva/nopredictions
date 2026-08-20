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

This process also DRIVES the other two pressure arms, each a separate strategy
with its own table, gates and settlement:

  * ht_pressure_agent  — PM's 1st Half Over 0.5, on a goalless high-pressure
    opening;
  * fav_pressure_agent — PM's "<Favourite> leading at halftime?", when the
    pre-match favourite is visibly on top and it is still 0-0.

They ride in this loop rather than as their own daemons because all three read
the same api-football poll: three processes would triple the live calls and split
the stats budget three ways, and that budget is the constraint that once turned
36,917 rows into "no coverage" when the real answer was "we ran out of quota".

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
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

import late_goals_table as lgt                                      # noqa: E402
from edge_engine import taker_fee_pp                                # noqa: E402
from fixture_match import pair_score, split_title                   # noqa: E402
from late_goals_observer import (                                   # noqa: E402
    _fetch_book,
    _fetch_events,
    infer_goals,
    ladder_consistent,
    ladder_of,
    pm_over25,
)
from live_tracker import (                                          # noqa: E402
    PRESSURE_WINDOW_MIN,
    LiveMatchTracker,
    PressureSignals,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("pressure")

DATABASE_URL = os.getenv("DATABASE_URL")
STRATEGY_NAME = "Live Pressure Overs"
OBS_VERSION = 2

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
# table already assumes. It started as a guess of 35 and is now MEASURED: the
# mean pressure_index over 13,938 recorded rows is 22.0 (`--report` prints it).
# At 35 the average live match priced k = 0.81, so the pressure term was
# quietly shrinking every fair value below the empirical base rate it is
# supposed to modify — and those non-entry rows are the control the pressure
# arm gets judged against.
#
# This does NOT change which rows enter. Since obs_version 2 the entry rule is
# minute + pressure + executability; no edge derived from k gates anything. And
# fair_pressure stays recomputable for older rows, because fair_base and
# pressure_index are both stored raw — so the series does not fork here.
#
# PRESSURE_GAIN has to move with it. The slope of k in pressure is GAIN/NEUTRAL,
# so recentring from 35 to 22 on its own would have steepened the response 59%
# (0.0143 -> 0.0227 per point) — a change to how hard pressure bites, smuggled in
# under a fix to where the term is centred. 0.31 holds the originally chosen
# slope while the centre moves, so this commit changes one thing.
#
# k still saturates at K_MAX around pressure 64, which now covers most entries
# (mean entry pressure is 65). That is the honest consequence of the centre
# being where the data says: 65 really is 43 points above the average match.
# The gain itself is still a guess and still what the refit is for.
PRESSURE_NEUTRAL = 22.0
PRESSURE_GAIN = 0.31
K_MIN, K_MAX = 0.70, 1.60

# ── entry gates ──────────────────────────────────────────────────────────────
# obs_version 2 (2026-08-15): the entry rule is now a PREDICTION, not a price
# comparison. Heavy pressure late in a match is a forecast that a goal is coming,
# and we buy the over on that forecast alone. fair_base / fair_pressure /
# edge_*_pp are still computed and stored on every row — they are the null this
# is judged against — but they no longer gate anything. MIN_EDGE_PP is kept only
# so the recorded diagnostic keeps its meaning; nothing reads it as a gate.
#
# Do not mix obs_version 1 and 2 entries in one yield: v1 required edge >= 2pp
# AND pressure >= 45, so it selected a different population.
MIN_EDGE_PP = 2.0               # recorded only — NOT a gate since v2
MIN_DEPTH_USD = 50.0
MIN_MINUTE = 20                 # below this the stat window is not informative
MAX_MINUTE = 88                 # past this there is no time for a goal to arrive
ENTRY_MIN_MINUTE = 75           # v2: only predict a goal in the closing stretch
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
    """Find the PM fixture for an api-football one.

    Token matching with squad-marker and kick-off gates — see fixture_match. The
    earlier substring version paired a first team with its own reserve side and
    a club with an unrelated one, and priced each PM board against the wrong
    match's state.
    """
    best, best_score = None, 0.0
    for fx in pm_fixtures:
        split = split_title(fx["title"])
        if not split:
            continue
        score = pair_score(split[0], split[1], sig.home, sig.away)
        if score > best_score:
            best, best_score = fx, score
        elif score == best_score and score > 0:
            best = None                 # ambiguous: two fixtures fit equally
    return best


# ── one cycle ────────────────────────────────────────────────────────────────

_LISTED_CACHE: dict[int, bool] = {}


def _pm_listed(tracker: LiveMatchTracker, fid: int, pm_fixtures: list[dict]) -> bool:
    """Does PM have a board for this fixture at all?

    A fixture PM does not list can never be traded no matter how well we measure
    it, so it must never win a paid stats call. Cached per fixture: the answer
    cannot change while the match is running, and the token matcher is not free.
    """
    if fid not in _LISTED_CACHE:
        info = tracker.fixture_info.get(fid) or {}
        home, away = info.get("home", ""), info.get("away", "")
        hit = False
        for fx in pm_fixtures:
            split = split_title(fx["title"])
            if split and pair_score(split[0], split[1], home, away) > 0:
                hit = True
                break
        _LISTED_CACHE[fid] = hit
    return _LISTED_CACHE[fid]


def _enrich_priority(tracker: LiveMatchTracker, pm_fixtures: list[dict]):
    """Rank fixtures for a paid /fixtures/statistics call.

    The stats budget is the binding constraint (see live_tracker), so it goes
    where it can still change a decision. There are now TWO decision windows on
    one budget:

      * the first-half arm (ht_pressure_agent) measures at 15-18' and enters up
        to 25'. Its measurement cannot be back-filled — miss the window and that
        fixture is lost for the day — so it outranks everything else.
      * this arm predicts from ENTRY_MIN_MINUTE on, and needs a rolling-window
        baseline built shortly before that.

    A fixture PM does not list can never be traded, and used to be refused a call
    outright. It is now ranked LAST rather than skipped: 87% of the live fixtures
    we see are unlisted (715 of 822 over three days), the pressure model can only
    ever be fitted on data recorded forward, and an unlisted 0-0 teaches that fit
    exactly as much as a listed one. They only ever get calls the tradeable
    fixtures did not want — measured usage is under one stats call per cycle
    against a budget of 40 — so this cannot displace a decision.
    """
    import ht_pressure_agent as ht      # local: ht imports this module at its top

    def rank(fid: int, snap) -> float:
        minute = snap.minute
        listed = _pm_listed(tracker, fid, pm_fixtures)
        if not listed:
            # Below every tradeable rank, above nothing at all. Kept inside the
            # first half, where all three arms' questions live; a 70th minute we
            # can never act on is not worth a paid call.
            return 10 - minute / 100.0 if minute <= ht.OBSERVE_MAX_MINUTE else -1
        if ht.FIRST15_MIN - 3 <= minute <= ht.ENTRY_MAX_MINUTE:
            return 400 - minute          # the first-half window, unrepeatable
        if minute >= ENTRY_MIN_MINUTE - 5:
            return 200 + minute          # this arm's decision window
        if minute >= ENTRY_MIN_MINUTE - 20:
            return 100 + minute          # building the baseline the entry needs
        if minute < ht.FIRST15_MIN:
            return 90 - minute           # about to enter the first-half window
        return minute                    # observation value only

    return rank


def _no_stats_reason(status: str) -> str:
    """Say which of the two very different failures actually happened."""
    if status in ("empty", "league proven uncovered"):
        return "no api-football stats coverage"
    if status in ("quota", "api quota spent"):
        return "stats unavailable: api quota spent"
    if status == "over cycle budget":
        return "stats not fetched: over cycle budget"
    if status == "not worth a call this cycle":
        return "stats not fetched: deprioritised"
    if status.startswith(("http", "error")):
        return f"stats fetch failed: {status}"
    if status:
        return f"stats pending: {status}"
    return "stats not fetched"


def observe(signals: dict[int, PressureSignals], table: dict,
            pm_fixtures: list[dict], pre_cache: dict[str, float],
            window_min: int = PRESSURE_WINDOW_MIN,
            enrich_status: dict[int, str] | None = None) -> list[dict]:
    """One row per live fixture. The poll happens in run(), not here, because
    the first-half arm prices the same signals and a second poll would mean a
    second api-football live call and a second stats budget every cycle.

    `enrich_status` is the tracker's account of WHY a fixture has no stats. It is
    recorded verbatim so a row can never again claim "no coverage" when the real
    answer was "we did not ask".
    """
    if not signals:
        return []

    rows: list[dict] = []
    for sig in signals.values():
        fx = match_pm_fixture(sig, pm_fixtures)
        row = _base_row(sig, window_min)
        goals = sig.home_goals + sig.away_goals

        # No stats coverage means no pressure measurement, and a row claiming
        # pressure 5/100 for a match nobody measured is worse than no row: it
        # would enter the regression as evidence.
        if not sig.has_stats:
            row["pressure_index"] = None
            row["home_danger"] = row["away_danger"] = None
            row["skip_reason"] = _no_stats_reason(
                (enrich_status or {}).get(sig.fixture_id, ""))
            rows.append(row)
            continue

        # Everything PM-side, when there is a PM fixture at all. A row without
        # one still gets its state priced below — the fair value is a lookup on
        # (minute, score, pre-match total), and only the last of those comes
        # from PM.
        cells: dict = {}
        if fx is not None:
            cells = ladder_of(fx)
            ladder = {ln: c["over_price"] for ln, c in cells.items()}
            row["event_title"] = fx["title"]

            # Pre-match total, the strongest single predictor of a late goal
            # (14pp across buckets, vs 4.5pp for league identity). Cached per
            # fixture — by the time a match is live its own pre-kickoff price is
            # long gone.
            pre = pre_cache.get(fx["title"])
            if pre is None:
                pre = pm_over25(cells)
                if pre is not None:
                    pre_cache[fx["title"]] = pre
            row["pre_over25"] = pre

            # api-football owns the score. The ladder is read only to notice
            # when the two disagree — on the late-goals v1 series the Gamma
            # ladder was wrong on 29% of polls, so it is evidence, not an
            # authority.
            if ladder and ladder_consistent(ladder):
                lower, upper, certain = infer_goals(ladder)
                if certain:
                    row["ladder_goals"] = lower
                    row["score_agrees"] = (lower == goals)

        # Price the STATE before the book. fair_base is what the empirical table
        # already predicts from (minute, score, pre-match total) alone, and it is
        # the null the pressure term has to beat — so it belongs on every row we
        # can compute it for, not only on the ~6% that reached a PM book. Without
        # it the residual test ("does pressure add anything the table did not
        # already know") runs on the subset that happened to be listed and
        # quoted, which is not the subset the question is about.
        #
        # A missing pre-match total is not fatal: lookup falls back to the pooled
        # cell for that (minute, score), which is the honest answer when we never
        # saw a pre-kickoff price.
        if MIN_MINUTE <= sig.minute <= MAX_MINUTE:
            fair_base, fair_n = lgt.lookup(
                table, sig.minute, goals, row["pre_over25"], needed=1)
            if fair_base is not None:
                k = pressure_factor(row["pressure_index"])
                row.update(fair_base=fair_base, fair_n=fair_n, pressure_factor=k,
                           fair_pressure=apply_pressure(fair_base, k))

        if fx is None:
            row["skip_reason"] = "no PM fixture"
            rows.append(row)
            continue

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

        if row["fair_base"] is None:
            row["skip_reason"] = f"state off the fair-value grid ({sig.minute}', {goals} goals)"
            rows.append(row)
            continue

        # The edges need a price, so they are the only part that waits for the
        # book. fair_base / fair_pressure were set above.
        fee = taker_fee_pp(book["best_ask"])
        row.update(
            fee_pp=fee,
            edge_base_pp=100.0 * (row["fair_base"] - book["best_ask"]) - fee,
            edge_pressure_pp=100.0 * (row["fair_pressure"] - book["best_ask"]) - fee,
        )

        row["would_enter"] = bool(
            # v2: heavy pressure, late. That is the whole thesis — no price
            # comparison. The gates below are not edge tests; each one is either
            # "the number we are acting on is real" or "this fill is executable".
            sig.minute >= ENTRY_MIN_MINUTE
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
    if sig.minute < ENTRY_MIN_MINUTE:
        return f"minute {sig.minute} < {ENTRY_MIN_MINUTE}"
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
        "home": sig.home, "away": sig.away, "league": sig.league,
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
        # xG is 40% of the index and api-football serves it on about half the
        # fixtures it covers, so a match with no xG reads far quieter than it
        # played and effectively cannot clear MIN_PRESSURE. None, not False,
        # when there were no stats at all: "measured, no xG" and "never
        # measured" are different facts and only one of them is evidence.
        "has_xg": (bool(sig.home_xg_total or sig.away_xg_total)
                   if sig.has_stats else None),
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
    "home_corners_window", "away_corners_window", "has_window", "has_xg",
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
                f"v2 PREDICTION: heavy pressure at {r['minute']}' says a goal is "
                f"coming — bought on that call alone, not on a price comparison. "
                f"For the record, base fair {r['fair_base']:.3f} "
                f"({1 / r['fair_base']:.2f}, n={r['fair_n']}) -> pressure fair "
                f"{r['fair_pressure']:.3f} ({1 / r['fair_pressure']:.2f}) at "
                f"k={r['pressure_factor']:.2f}, i.e. {r['edge_pressure_pp']:+.1f}pp "
                f"after {r['fee_pp']:.2f}pp fee — recorded as the null, NOT a gate. "
                f"PAPER — threshold set on 25 fixtures, far below a result."
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

def _goal_minute_api(fixture_id: int, after_minute: int) -> int | None:
    """Exact minute of the first goal after `after_minute`, from api-football.

    Worth one call because it is the only source that carries stoppage time:
    our own polls cap at 90, so a 90+3 winner reads as "90" off the tape. Only
    ever called for an entry that already won, which is a couple of dozen calls
    a day — negligible against the budget the tracker now lives inside.
    """
    key = os.getenv("FOOTBALL_API_KEY", "")
    if not key:
        return None
    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures/events",
            params={"fixture": fixture_id, "type": "Goal"},
            headers={"x-apisports-key": key},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body.get("errors"):
            return None                      # quota/rate — fall back to the tape
        best = None
        for ev in body.get("response", []):
            detail = (ev.get("detail") or "").lower()
            if "own goal" in detail or "missed" in detail:
                # An own goal still counts for an over; a missed penalty does not.
                if "missed" in detail:
                    continue
            t = ev.get("time") or {}
            minute = (t.get("elapsed") or 0) + (t.get("extra") or 0)
            if minute > after_minute and (best is None or minute < best):
                best = minute
        return best
    except Exception:
        return None


def settle(conn) -> int:
    """Fill in both horizons for rows whose fixture has moved on.

    goal_next_10 is read off our own later observations of the same fixture —
    the score at minute+10 is a row we recorded. goal_before_ft needs the final
    score, which only arrives once the fixture is over.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, fixture_id, minute, goals_total, paper_trade_id, entered
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

            # WHEN the goal came, not just whether — but only for a bet that
            # actually won, so this stays a couple of dozen API calls a day.
            goal_minute = goal_src = None
            if r["entered"] and goal_before_ft:
                goal_minute = _goal_minute_api(r["fixture_id"], r["minute"])
                goal_src = "api" if goal_minute is not None else None
                if goal_minute is None:
                    # Fall back to our own tape: the first later observation
                    # showing a higher score. Late by up to one poll, and it
                    # cannot see stoppage time — hence the recorded source.
                    scored = [m for m, g in obs
                              if m >= r["minute"] and g > r["goals_total"]]
                    if scored:
                        goal_minute, goal_src = min(scored), "poll"

            cur.execute(
                """UPDATE pressure_observations
                      SET goals_at_plus_10 = %s, goal_next_10 = %s,
                          final_goals = %s, goal_before_ft = %s,
                          goal_minute = %s, goal_minute_source = %s,
                          settled_at = now()
                    WHERE id = %s""",
                (at_plus_10, goal_next_10, final_goals, goal_before_ft,
                 goal_minute, goal_src, r["id"]),
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
                      count(*) FILTER (WHERE pressure_index IS NOT NULL) AS measured,
                      count(*) FILTER (WHERE fair_base IS NOT NULL) AS priced,
                      count(*) FILTER (WHERE entered) AS entered,
                      count(*) FILTER (WHERE goal_next_10 IS NOT NULL) AS settled_10,
                      avg(pressure_index) AS mean_pressure
                 FROM pressure_observations"""
        )
        s = cur.fetchone()
        print(f"\nrows={s['rows']}  fixtures={s['fixtures']}  measured={s['measured']}  "
              f"with_window={s['with_window']}  priced={s['priced']}  "
              f"entries={s['entered']}  settled@10={s['settled_10']}")
        if s["mean_pressure"] is not None:
            drift = abs(s["mean_pressure"] - PRESSURE_NEUTRAL)
            note = "matches PRESSURE_NEUTRAL" if drift < 2 else (
                f"PRESSURE_NEUTRAL is {PRESSURE_NEUTRAL} — REFIT IT, k != 1 for the average match")
            print(f"mean pressure index = {s['mean_pressure']:.1f}  ({note})")

        # Where the measurement actually goes. 91% of rows carried no pressure
        # at all on 2026-08-19, and most of that was budget rather than
        # coverage — a distinction the agent can only make because the tracker's
        # reason is recorded verbatim. Any formula change multiplies a number we
        # only have on the `measured` fraction, so this line is the ceiling on
        # everything below it.
        cur.execute(
            """SELECT skip_reason AS why, count(*) AS n
                 FROM pressure_observations
                WHERE pressure_index IS NULL
                GROUP BY 1 ORDER BY 2 DESC LIMIT 6"""
        )
        unmeasured = cur.fetchall()
        if unmeasured:
            print("\nno pressure measured, by reason:")
            for r in unmeasured:
                print(f"  {r['n']:7d}  {r['why'] or '(none recorded)'}")

        # xG availability, the confound that looks like a signal: it drives the
        # index harder than play does, and the fixtures that have it score more.
        cur.execute(
            """SELECT has_xg, count(*) AS n, avg(pressure_index)::float8 AS mean_p,
                      percentile_cont(0.9) WITHIN GROUP (ORDER BY pressure_index) AS p90,
                      avg(goal_next_10::int)::float8 AS p_goal
                 FROM pressure_observations
                WHERE pressure_index IS NOT NULL AND has_xg IS NOT NULL
                  AND goal_next_10 IS NOT NULL
                GROUP BY 1 ORDER BY 1"""
        )
        xg_rows = cur.fetchall()
        if xg_rows:
            print("\nxG coverage (recorded since db/035 — earlier rows are NULL):")
            for r in xg_rows:
                print(f"  has_xg={str(r['has_xg']):5s} n={r['n']:6d}  "
                      f"mean pressure={r['mean_p']:5.1f}  p90={r['p90']:5.1f}  "
                      f"P(goal)={r['p_goal']:.3f}")
            print(f"  (MIN_PRESSURE is {MIN_PRESSURE:.0f} — compare it against each p90)")

        # The primary test, as pre-registered: does pressure separate at all?
        # Rows with no measurement are excluded rather than bucketed: they used
        # to fall into a NULL bucket that crashed this report before it ever
        # reached the paper results below.
        cur.execute(
            """SELECT width_bucket(pressure_index, 0, 100, 5) AS b,
                      count(*) AS n,
                      avg(goal_next_10::int)::float8 AS p_goal,
                      min(pressure_index) AS lo, max(pressure_index) AS hi
                 FROM pressure_observations
                WHERE goal_next_10 IS NOT NULL AND pressure_index IS NOT NULL
                GROUP BY 1 ORDER BY 1"""
        )
        rows = cur.fetchall()
        if rows:
            print(f"\nP(goal within {SETTLE_HORIZON_MIN}min) by pressure bucket:")
            for r in rows:
                print(f"  {r['lo']:5.0f}-{r['hi']:5.0f}  n={r['n']:5d}  "
                      f"{r['p_goal']:.3f}" + (f"  ({1 / r['p_goal']:.2f})" if r["p_goal"] else ""))
            print("  (n >= 200 per bucket before reading anything into this, and this "
                  "table is NOT controlled for minute/score/pre-match total — the "
                  "base table already knows those)")

        # Split by obs_version, never pooled. v1 gated on edge >= 2pp AND
        # pressure >= 45; v2 gates on minute + pressure + executability and
        # ignores price. They are different populations wearing one strategy id,
        # and pooling them was reporting a yield for a bet nobody ever placed.
        cur.execute(
            """SELECT po.obs_version AS v, count(*) AS n,
                      count(*) FILTER (WHERE pt.result = 'won') AS won,
                      sum(pt.payout_units - pt.stake_units)::float8 AS pnl,
                      sum(pt.stake_units)::float8 AS staked,
                      stddev_samp(pt.payout_units - pt.stake_units)::float8 AS sd,
                      avg(po.minute)::float8 AS minute,
                      avg(po.best_ask)::float8 AS ask
                 FROM pressure_observations po
                 JOIN paper_trades pt ON pt.id = po.paper_trade_id
                WHERE pt.result IS NOT NULL
                GROUP BY 1 ORDER BY 1"""
        )
        for t in cur.fetchall():
            yld = 100.0 * t["pnl"] / t["staked"] if t["staked"] else 0.0
            # Stakes are a flat 1u, so the yield is the mean P&L per trade and
            # its CI is the ordinary standard error of that mean.
            ci = (1.96 * 100.0 * t["sd"] / math.sqrt(t["n"])) if (t["sd"] and t["n"] > 1) else None
            band = f" CI[{yld - ci:+.1f}, {yld + ci:+.1f}]" if ci else ""
            ask = f"{t['ask']:.2f} ({1 / t['ask']:.2f})" if t["ask"] else "n/a"
            print(f"\npaper v{t['v']}: n={t['n']} won={t['won']} P&L={t['pnl']:+.2f}u "
                  f"yield={yld:+.1f}%{band}")
            print(f"  mean entry minute={t['minute']:.0f}  mean ask={ask}")
        print("\n  (verdict gate: n >= 200 on ONE obs_version AND yield CI clear of "
              "zero AND the pressure arm beating the base arm)")


# ── loop ─────────────────────────────────────────────────────────────────────

def run(once: bool, dry_run: bool, interval: int) -> None:
    # Local imports: both arms import this module at their top level.
    import fav_pressure_agent as fav
    import ht_pressure_agent as ht

    table = lgt.load(lgt.WIDE_TABLE_PATH)
    log.info(f"baseline {table['built_at']} — {len(table['cells'])} cells, "
             f"{table['minutes'][0]}'-{table['minutes'][-1]}'")
    # The first-half arm is a separate strategy with its own table, gates and
    # settlement, but it rides in this loop on purpose: one api-football live
    # call and ONE stats budget per cycle. Two processes would double both, and
    # the quota is what silently killed the pressure measurement once already.
    ht_table = ht.load_table()
    log.info(f"first-half baseline {ht_table['built_at']} — {len(ht_table['cells'])} cells")
    fav_table = fav.load_table()
    log.info(f"favourite baseline {fav_table['built_at']} — {len(fav_table['cells'])} cells")

    conn = None if dry_run else _conn()
    strategy_id = None if dry_run else _strategy_id(conn)
    ht_strategy_id = None if dry_run else ht.strategy_id(conn)
    fav_strategy_id = None if dry_run else fav.strategy_id(conn)
    tracker = LiveMatchTracker()
    pre_cache: dict[str, float] = {}
    ht_state = ht.HTState()
    fav_state = fav.FavState()
    pm_fixtures: list[dict] = []
    last_markets = 0.0

    while True:
        t0 = time.time()
        if t0 - last_markets > REFRESH_MARKETS_S or not pm_fixtures:
            pm_fixtures = _fetch_events()
            last_markets = t0
            log.info(f"PM universe -> {len(pm_fixtures)} football fixtures")

        signals = tracker.poll(priority=_enrich_priority(tracker, pm_fixtures))
        rows = observe(signals, table, pm_fixtures, pre_cache,
                       tracker.window_minutes, tracker.enrich_status)
        ht_rows = ht.observe(signals, ht_table, pm_fixtures, ht_state,
                             tracker.enrich_status)
        fav_rows = fav.observe(signals, fav_table, pm_fixtures, fav_state,
                               tracker.enrich_status)

        opened = 0
        if rows and conn is not None:
            opened = open_trades(conn, strategy_id, rows)
            _write(conn, rows)

        ht_opened = 0
        if ht_rows and conn is not None:
            ht_opened = ht.open_trades(conn, ht_strategy_id, ht_rows)
            ht.write(conn, ht_rows)
        for r in ht_rows:
            if r["entered"]:
                log.info(
                    f"  ENTER  [HT] {r['home'][:16]:16} 0-0 {r['away'][:16]:16} "
                    f"{r['minute']}'  1H O0.5 ask={r['best_ask']:.3f} "
                    f"({1 / r['best_ask']:.2f})  opening="
                    f"{r['opening_pressure']:.0f}  #{r['paper_trade_id']}"
                )

        fav_opened = 0
        if fav_rows and conn is not None:
            fav_opened = fav.open_trades(conn, fav_strategy_id, fav_rows)
            fav.write(conn, fav_rows)
        for r in fav_rows:
            if r["entered"]:
                log.info(
                    f"  ENTER  [FAV] {r['fav_team'][:20]:20} lead@HT {r['minute']}'  "
                    f"ask={r['best_ask']:.3f} ({1 / r['best_ask']:.2f})  "
                    f"fav={r['fav_prob']:.0%} press={r['opening_fav_pressure']:.0f} "
                    f"dom={r['opening_dominance']:+.0f}  #{r['paper_trade_id']}"
                )

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
        log.info(f"live={len(rows):3d} priced={len(priced):3d} entered={opened:2d} "
                 f"| 1H rows={len(ht_rows):3d} entered={ht_opened:2d} "
                 f"| FAV rows={len(fav_rows):3d} entered={fav_opened:2d} "
                 f"| {time.time() - t0:.1f}s")

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
        # Local imports: both arms import this module at their top level.
        import fav_pressure_agent as fav
        import ht_pressure_agent as ht
        conn = _conn()
        try:
            if args.settle:
                # All three arms settle here, so the crontab keeps ONE settle line.
                log.info(f"settled {settle(conn)} observations")
                log.info(f"settled {ht.settle(conn)} first-half observations")
                log.info(f"settled {fav.settle(conn)} favourite observations")
            if args.report:
                report(conn)
                ht.report(conn)
                fav.report(conn)
        finally:
            conn.close()
        return

    run(once=args.once, dry_run=args.dry_run, interval=args.interval)


if __name__ == "__main__":
    main()
