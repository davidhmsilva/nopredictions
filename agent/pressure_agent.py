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
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

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
import af_budget
import db_txn
import venues                                                     # noqa: E402
from live_tracker import (                                          # noqa: E402
    DAILY_EXHAUSTED,
    PRESSURE_WINDOW_MIN,
    RATE_LIMITED,
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
# v5 (2026-09-11): the AXIS moved again — a fixture with no xG in the feed gets
# an ESTIMATED xG from its shots (live_tracker.estimate_xg) instead of the xG
# term being dropped and the rest renormalised. api-football has sent no xG
# since 09-02, so that is every row. Never pool with v4.
OBS_VERSION = 6

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
#
# obs_version 3 (2026-08-26): the AXIS moved, not the rule. The danger index now
# renormalises its remaining weights when the feed carries no xG, as the other
# two arms already did. Until now this arm dropped that flag to protect the
# record built against the un-renormalised score — and the measured cost of that
# protection was the arm: on tradeable rows at minute 75+ over the preceding
# seven days, fixtures without xG peaked at 29.4 against this MIN_PRESSURE of
# 45, so 0 of 372 rows could ever enter and 32 of 53 fixtures (60%) were
# excluded by which competition publishes xG rather than by how they played.
# MIN_PRESSURE is deliberately UNCHANGED at 45: the point is to let the fixtures
# that were never measurable reach the same bar, not to lower it.
#
# v2 and v3 are two different measurements of the same quantity. Never pool
# them; --report already splits on obs_version, and has_xg is on every row.
#
# ⚠️ PRESSURE_NEUTRAL = 22.0 was measured as the mean index on the OLD axis, and
# renormalising lifts every no-xG fixture, so the true mean is now higher. That
# biases k upward on those rows — but k gates nothing since v2, and fair_base
# and pressure_index are both stored raw, so every fair_pressure stays
# recomputable. Re-measuring the centre is a separate calibration decision and
# is not made here.
MIN_EDGE_PP = 2.0               # recorded only — NOT a gate since v2

# ── book quality ─────────────────────────────────────────────────────────────
# obs_version 4 (2026-08-30): the entry rule gains two gates, both about the
# BOOK and neither about the match. Nothing else moved — same axis, same
# pressure gate, same minute window — so v3 and v4 differ only in which fills
# were reachable.
#
# Measured on 6,449 deduped observations (one row per fixture/minute/score) over
# 494 fixtures at minute 70-89, obs_version >= 2, CIs clustered by fixture.
# `real − ask` is the realised P(>=1 more goal) minus the price we would have
# paid:
#
#   spread  0-3pp    +3.64pp        depth    $0-200     -4.72pp
#   spread  3-6pp    +0.92pp        depth  $200-1000    -0.49pp
#   spread 6-10pp    -4.26pp        depth $1000-5000    +3.63pp
#   spread 10-20pp   -6.33pp  CI[-12.1, -0.6]
#   spread 20pp+    -38.38pp  CI[-44.3,-32.5]   (depth column: after spread<=6pp)
#
# The 20pp+ bucket is not a market at all: 430 rows over 221 fixtures quoting an
# ask around 0.90 that resolves at 0.529, with spreads of 0.34, 0.40, 0.72 — a
# lone sell order parked far from anyone's bid. MAX_ASK cannot catch those,
# because the defect is the empty ladder and not the level of the price.
#
# What this actually buys: the apparent "PM's late over is expensive" reading
# (real - ask of -5.1pp at 81-83', -6.0pp at 84-86', both CIs clear of zero) is
# ENTIRELY an artefact of those books. On a clean book the ask is fair to
# slightly cheap at every minute in the window — +2.1 / +2.9 / +4.7 / +3.0 /
# +3.9pp across 70-74 / 75-78 / 79-82 / 83-86 / 87-89 — so the gate removes a
# measured bleed rather than discovering an edge, and every one of those
# positive numbers still has a CI crossing zero.
#
# ⚠️ Confound, stated because it is not controlled: deep, tight books belong to
# the larger competitions, so part of the improvement is competition mix and
# not book quality as such. The defensible claim is the negative one.
#
# Cost: 56 of the 85 v2+v3 entries survive both gates (74 the spread, 59 the
# depth). Those 56 hit 48.2% against an ask of 45.8%, where all 85 hit 45.9%
# against 46.3%. Entries were already running a handful a day, so expect this to
# push the verdict gate further out — which is an argument for widening the
# pressure gate (measured to select nothing), not for keeping cheap fills.
MIN_DEPTH_USD = 1000.0          # was 50.0 — see the depth column above
MAX_SPREAD = 0.06               # ask - bid; above this the quote is not a price
MIN_MINUTE = 20                 # below this the stat window is not informative
MAX_MINUTE = 88                 # past this there is no time for a goal to arrive
ENTRY_MIN_MINUTE = 75           # v2: only predict a goal in the closing stretch
MIN_PRESSURE = 45.0             # do not trade "a goal might happen eventually"
# Consecutive transport failures before the forever-loop gives up and lets the
# wrapper restart it. Ten cycles is ten minutes at the default interval: long
# enough that a flapping connection does not churn the process, short enough
# that a fault which never clears costs one kickoff window and not a weekend.
DEAD_POLLS_BEFORE_EXIT = 10
MAX_ASK = 0.85                  # PM asks above 0.85 resolve far below their price
STAKE_UNITS = 1.0
SETTLE_HORIZON_MIN = 10         # the "goal is coming" horizon


def _conn():
    # autocommit, via db_txn — a SELECT on a psycopg2 default connection opens a
    # transaction that stays open until something commits, and this process then
    # sleeps on it. See db_txn.py for the 8-minute one that was found in
    # production. Writes that must land together use db_txn.atomic().
    return db_txn.connect(DATABASE_URL)


# A dropped socket is not an error here, it is the normal cost of this Mac
# sleeping: the connection to Supabase dies while the process is frozen and the
# first write after the wake raises. That used to kill the daemon four times a
# day (2026-08-21), and each restart only happened once the machine was awake
# again, so a 30s wrapper delay turned into hours of lost polls.
_DB_DROPPED = (psycopg2.OperationalError, psycopg2.InterfaceError)


def _db_alive(conn) -> bool:
    if conn is None or conn.closed:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except psycopg2.Error:
        return False


def _reconnect(conn):
    """Return a fresh connection, or None if the DB is still unreachable."""
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    try:
        return _conn()
    except psycopg2.Error as exc:
        log.warning(f"db reconnect failed: {exc.__class__.__name__}: {exc}")
        return None


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
_LISTED_MISS_GEN: dict[int, int] = {}
_PM_UNIVERSE_GEN = 0


def _pm_universe_refreshed() -> None:
    """A new PM universe was pulled, so every cached "not listed" is stale.

    PM opens a live board when it feels like it, not at kick-off. The universe
    is re-pulled every REFRESH_MARKETS_S; a miss is only ever valid against the
    pull it was computed on.
    """
    global _PM_UNIVERSE_GEN
    _PM_UNIVERSE_GEN += 1


def _pm_listed(tracker: LiveMatchTracker, fid: int, pm_fixtures: list[dict]) -> bool:
    """Does PM have a board for this fixture at all?

    A fixture PM does not list can never be traded no matter how well we measure
    it, so it must never win a paid stats call.

    A HIT is cached for good. A MISS is cached only against the PM universe that
    produced it, because "the answer cannot change while the match is running"
    was wrong: PM lists many live boards after kick-off, and this runs long before
    the first re-pull. A permanently cached miss sent the fixture down the
    unlisted branch of _enrich_priority, which returns -1 past
    ht.OBSERVE_MAX_MINUTE — so it never won another paid stats call, its stat
    block was carried forward until the rolling window differenced one fetch
    against itself, and get_signals set stats_frozen with no pressure index at
    all. The pricing path meanwhile re-read the universe every 300s, found the
    board and recorded best_ask, so the row looked tradeable and could never be
    traded. Over the seven days to 2026-08-26 that cost 177 of the 230 fixtures
    (77%) that had a PM book at minute 75+, and Elche-Barcelona finished with
    one shot on record.
    """
    if _LISTED_CACHE.get(fid) is True:
        return True
    if fid in _LISTED_CACHE and _LISTED_MISS_GEN.get(fid) == _PM_UNIVERSE_GEN:
        return False

    info = tracker.fixture_info.get(fid) or {}
    home, away = info.get("home", ""), info.get("away", "")
    hit = False
    for fx in pm_fixtures:
        split = split_title(fx["title"])
        if split and pair_score(split[0], split[1], home, away) > 0:
            hit = True
            break
    _LISTED_CACHE[fid] = hit
    if hit:
        _LISTED_MISS_GEN.pop(fid, None)
    else:
        _LISTED_MISS_GEN[fid] = _PM_UNIVERSE_GEN
    return hit


def _enrich_priority(tracker: LiveMatchTracker, pm_fixtures: list[dict]):
    """Rank fixtures for a paid /fixtures/statistics call.

    The stats budget is the binding constraint (see live_tracker), so it goes
    where it can still change a decision. There are now TWO decision windows on
    one budget:

      * the first-half arms measure from 15' and, since obs_version 3, enter any
        time out to 40' off a rolling window. The 15-18' reading still cannot be
        back-filled — miss it and that fixture has no opening control for the
        day — so the first half outranks everything else. Widening the band to
        40' widens what sits at the top of this ranking; measured usage is under
        one stats call per cycle against a budget of 40, so it displaces nothing.
      * this arm predicts from ENTRY_MIN_MINUTE on, and needs a rolling-window
        baseline built shortly before that.

    A fixture PM does not list can never be traded, and used to be refused a call
    outright. It is ranked LAST rather than skipped: 87% of the live fixtures we
    see are unlisted (715 of 822 over three days), the pressure model can only
    ever be fitted on data recorded forward, and an unlisted 0-0 teaches that fit
    exactly as much as a listed one.

    ⚠️ The old claim here — "they only ever get calls the tradeable fixtures did
    not want, measured usage is under one stats call per cycle against a budget
    of 40" — was measured on 2026-08-19 and is no longer true. The budget is
    still never exhausted (a busy Saturday cycle spends ~7 of 40, throttled by
    ENRICH_TTL_S, not by the budget), but the DAY is: on 2026-09-05 the
    allowance ran out at 16:11 UTC, one hour into the only window whose boards
    we can trade, and 89% of everything recorded that day was unlisted
    (39,974 fixture-minutes against 4,711, 712 fixtures against 78). Research
    was not displacing a decision inside a cycle. It was displacing the whole
    evening.

    So before the evening window opens, research is SAMPLED — deterministically
    on the fixture id, so a match we follow is followed all the way through and
    its rolling windows stay intact. After it opens, everything is served again.
    A fixture sampled out is recorded as such (`skip_reason`), never as
    "no coverage".
    """
    import ht_pressure_agent as ht      # local: ht imports this module at its top

    def rank(fid: int, snap) -> float:
        minute = snap.minute
        listed = _pm_listed(tracker, fid, pm_fixtures)
        if not listed:
            # Below every tradeable rank, above nothing at all. Kept inside the
            # first half, where all three arms' questions live; a 70th minute we
            # can never act on is not worth a paid call.
            if minute > ht.OBSERVE_MAX_MINUTE:
                return -1
            if not af_budget.research_allowed(fid, tracker.calls):
                tracker.enrich_status[fid] = _RESEARCH_SAMPLED
                return -1
            return 10 - minute / 100.0
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


# Written by _enrich_priority and read back by _no_stats_reason. A fixture we
# chose not to pay for in order to keep the evening allowance is not a fixture
# api-football would not answer about, and the row has to be able to say so.
_RESEARCH_SAMPLED = "research sampled out (daytime budget)"


def _no_stats_reason(status: str) -> str:
    """Say which of the two very different failures actually happened."""
    if status == _RESEARCH_SAMPLED:
        return "stats not fetched: research sampled out (daytime budget)"
    if status in ("empty", "league proven uncovered"):
        return "no api-football stats coverage"
    # Two refusals that used to share one label, and mean opposite things about
    # whether more budget would help. On 2026-08-20 the daily allowance was 3%
    # used (2,147 of 75,000) while 176 rows claimed it was spent, so the old
    # label was not merely coarse — it pointed at the wrong constraint.
    if status in (RATE_LIMITED, "quota"):
        return "stats unavailable: rate limited (per-minute)"
    if status in (DAILY_EXHAUSTED, "api quota spent"):
        return "stats unavailable: daily quota exhausted"
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
        #
        # `stats_frozen` is the same failure wearing a stat block. The window
        # baseline and the latest snapshot hold one fetch between them, so every
        # delta is 0 and the index sits at its possession term — 5.0, the exact
        # number this comment was written about. It has to be nulled here too,
        # for the same reason and not as a special case.
        if not sig.has_stats or sig.stats_frozen:
            row["pressure_index"] = None
            row["home_danger"] = row["away_danger"] = None
            row["skip_reason"] = (
                "stats frozen: window baseline is the same fetch"
                if sig.has_stats
                else _no_stats_reason((enrich_status or {}).get(sig.fixture_id, "")))
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

        # obs_version 6: the same bet, at whichever exchange is cheaper.
        #
        # Kalshi lists this exact ladder (KX*TOTAL, match goals, regulation
        # time -- the same settlement rule, verified 2026-07-22) on most of the
        # competitions this arm trades. Reading it costs nothing here: the
        # index is swept on a background thread and this is a dictionary
        # lookup.
        #
        # ⚠️ It changes the PRICE, not the rule. Every gate above and below is
        #    untouched, so the same fixtures enter -- they just enter cheaper
        #    where Kalshi is cheaper. That is why this is an execution change
        #    with no new fair value to fit.
        exec_ = _best_venue(sig, row, book)
        # ⚠️ `best_bid`/`best_ask` stay Polymarket's book, on every row, always
        #    — the whole history reads them that way. `entry_ask` is the price
        #    the trade is BOOKED at, and it is what paper_trades gets. A Kalshi
        #    entry settled against a Polymarket price would be the yield of a
        #    venue it never traded at (db/057).
        row.update(venue=exec_.venue, alt_venue_ask=exec_.alt_ask,
                   venue_saving_pp=exec_.saving_pp,
                   entry_ask=exec_.ask, entry_ticker=exec_.ticker)
        entry_ask = exec_.ask
        entry_bid, entry_depth = exec_.bid, exec_.depth_usd

        if row["fair_base"] is None:
            row["skip_reason"] = f"state off the fair-value grid ({sig.minute}', {goals} goals)"
            rows.append(row)
            continue

        # The edges need a price, so they are the only part that waits for the
        # book. fair_base / fair_pressure were set above.
        # The fee is the CHOSEN venue's: Kalshi's is 40% higher, so pricing a
        # Kalshi fill at Polymarket's rate would overstate every edge on it.
        fee = 100.0 * venues.taker_fee(entry_ask, exec_.venue)
        row.update(
            fee_pp=fee,
            edge_base_pp=100.0 * (row["fair_base"] - entry_ask) - fee,
            edge_pressure_pp=100.0 * (row["fair_pressure"] - entry_ask) - fee,
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
            and entry_ask <= MAX_ASK
            # v4: the book has to be a book. A missing bid or a wide spread
            # means the ask is one parked order, not a price — those quote 0.90
            # and resolve at 0.53.
            #
            # v6: read off the venue we would actually buy at, not always
            # Polymarket's. A Kalshi fill gated on Polymarket's spread would
            # be a fill nobody checked.
            and entry_bid is not None
            and (entry_ask - entry_bid) <= MAX_SPREAD
            and (entry_depth or 0) >= MIN_DEPTH_USD
            # A score we cannot pin makes the fair value meaningless: it is a
            # lookup keyed on the score.
            and row["score_agrees"] is not False
        )
        if not row["would_enter"] and not row["skip_reason"]:
            row["skip_reason"] = _why_not(
                row, {"best_ask": entry_ask, "best_bid": entry_bid,
                      "ask_depth_usd": entry_depth}, sig)
        rows.append(row)

    return rows


def _best_venue(sig: PressureSignals, row: dict, book: dict):
    """Where to buy this over line, and what the alternative was.

    Kalshi's side is a lookup into an index refreshed on a BACKGROUND thread,
    so a cold process — or a Kalshi outage — costs nothing but the comparison.
    The trade books on Polymarket exactly as it did before, which is the right
    failure: a missing second quote is not a wrong one.
    """
    pm = venues.Quote(bid=book["best_bid"], ask=book["best_ask"],
                      ask_depth_usd=book["ask_depth_usd"])
    kal, fx = None, None
    try:
        idx = venues.shared_index(background=True)
        # ⚠️ The signal carries no kick-off, so it is reconstructed from the
        #    clock: a match at minute m started about m minutes ago. The join
        #    only needs the EASTERN DATE, and m is at worst a couple of hours
        #    out (half time, stoppage), so the date is right except for a
        #    fixture straddling ET midnight — where the lookup simply misses
        #    and the trade books on Polymarket.
        kickoff = datetime.now(timezone.utc) - timedelta(minutes=sig.minute or 0)
        # ⚠️ A stale Kalshi quote is refused outright: one goal moves an
        #    over line 20-30pp, and a fifteen-minute-old price across a
        #    goal reported a 26pp phantom saving in production.
        fx = idx.fixture(sig.home, sig.away, kickoff,
                         max_quote_age_s=venues.MAX_INPLAY_QUOTE_AGE_S)
        if fx is not None and row.get("target_line") is not None:
            kal = fx.over(float(row["target_line"]))
    except Exception as e:          # noqa: BLE001 - never take the poll down
        log.debug("kalshi lookup failed for %s v %s: %s", sig.home, sig.away, e)

    chosen = venues.choose(pm, kal)
    if chosen is None or chosen.venue == venues.POLYMARKET:
        return _Exec(venues.POLYMARKET, book["best_ask"], book["best_bid"],
                     book["ask_depth_usd"],
                     chosen.alt_ask if chosen else None,
                     chosen.saving_pp if chosen else None, None)
    ticker = None
    if fx is not None and row.get("target_line") is not None:
        leg = fx.totals.get(f'{float(row["target_line"]):.1f}')
        ticker = leg[0] if leg else None
    return _Exec(venues.KALSHI, kal.ask, kal.bid, kal.ask_depth_usd,
                 chosen.alt_ask, chosen.saving_pp, ticker)


class _Exec(NamedTuple):
    venue: str
    ask: float
    bid: float | None
    depth_usd: float | None
    alt_ask: float | None
    saving_pp: float | None
    #: Kalshi's market ticker, so the fill is checkable against the exchange.
    ticker: str | None


def _why_not(row: dict, book: dict, sig: PressureSignals) -> str:
    if sig.minute < ENTRY_MIN_MINUTE:
        return f"minute {sig.minute} < {ENTRY_MIN_MINUTE}"
    if row["pressure_index"] < MIN_PRESSURE:
        return f"pressure {row['pressure_index']:.0f} < {MIN_PRESSURE}"
    if not row["has_window"]:
        return "no window baseline yet"
    if book["best_ask"] > MAX_ASK:
        return f"ask {book['best_ask']:.2f} > {MAX_ASK}"
    if book["best_bid"] is None:
        return "one-sided book: no bid"
    if (book["best_ask"] - book["best_bid"]) > MAX_SPREAD:
        return f"spread {100 * (book['best_ask'] - book['best_bid']):.0f}pp > {100 * MAX_SPREAD:.0f}pp"
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
        # True when the window baseline carried the same fetch as this row, so
        # every delta above is structurally zero and the index below would be
        # an artefact rather than a reading. Retracted rows from the
        # 2026-08-20..08-25 defect carry it too (db/036); NULL means there was
        # no pressure reading to judge.
        "stats_frozen": sig.stats_frozen if sig.has_stats else None,
        # xG is 40% of the index and api-football serves it on about half the
        # fixtures it covers, so a match with no xG reads far quieter than it
        # played and effectively cannot clear MIN_PRESSURE. None, not False,
        # when there were no stats at all: "measured, no xG" and "never
        # measured" are different facts and only one of them is evidence.
        # Which feed measured this, and whether it could see inside the box.
        # ESPN (the free fallback) cannot, so the two are different measurements
        # of the same quantity and must stay separable in the data.
        "stats_source": sig.stats_source,
        "has_inside": sig.has_inside,
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
        # Where the entry was priced, and what the other exchange wanted for
        # the same bet. Recorded on every row so the Polymarket-only
        # counterfactual stays recoverable per entry (db/055, H-BEST-VENUE).
        "venue": venues.POLYMARKET, "alt_venue_ask": None, "venue_saving_pp": None,
        "entry_ask": None, "entry_ticker": None,
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
    "stats_source", "has_inside",
    "stats_frozen",
    "home_danger", "away_danger", "pressure_index", "pressure_factor",
    "pre_over25", "target_line", "best_bid", "best_ask", "bid_depth_usd",
    "ask_depth_usd", "fair_base", "fair_pressure", "fair_n", "fee_pp",
    "edge_base_pp", "edge_pressure_pp", "would_enter", "entered",
    "paper_trade_id", "skip_reason",
    "venue", "alt_venue_ask", "venue_saving_pp", "entry_ask", "entry_ticker",
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
                # Matched on the TEAMS as well as the id. When api-football goes
                # down mid-match the tracker falls back to ESPN, which namespaces
                # the fixture id negative — so the same real match arrives under
                # a second id and an id-only guard lets the same line be bought
                # twice. The target_line still scopes it: this arm deliberately
                # takes a second position when the score moves the rung.
                "SELECT 1 FROM pressure_observations "
                "WHERE (fixture_id = %s OR (home = %s AND away = %s)) "
                "  AND target_line = %s AND entered "
                "  AND observed_at > now() - interval '6 hours' "
                "LIMIT 1",
                (r["fixture_id"], r["home"], r["away"], r["target_line"]),
            )
            if cur.fetchone():
                r["would_enter"], r["skip_reason"] = False, "already entered this line"
                continue

            reasoning = (
                f"{r['home']} {r['home_goals']}-{r['away_goals']} {r['away']} "
                f"{r['minute']}' — Over {r['target_line']} at "
                f"{r['entry_ask']:.3f} ({1 / r['entry_ask']:.2f}) "
                f"on {venues.VENUE_NAME[r['venue']]}"
                + (f" ({r['entry_ticker']})" if r['entry_ticker'] else "")
                + (f", against {r['alt_venue_ask']:.3f} on the other exchange "
                   f"— {r['venue_saving_pp']:+.1f}pp net of both fees"
                   if r['venue_saving_pp'] is not None else "")
                + ". "
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
                 # The price PAID, at the venue named on the row. Booking a
                 # Kalshi fill at Polymarket's ask would settle it against a
                 # venue it never traded at (db/057).
                 r["entry_ask"], 1.0 / r["entry_ask"], STAKE_UNITS,
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
        af_budget.process_counter().record("events")
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


def _final_goals_api(fixture_ids: list[int]) -> dict[int, int]:
    """True full-time goal total per fixture, straight from api-football.

    Only finished fixtures are returned, so a missing key means "not settleable
    from the API" and never "0 goals". Batched 20 ids per call, which makes a
    whole settle run a handful of requests against a 75k/day budget.

    This exists because `max()` over our own tape is NOT a final score. The
    score feed flaps — Aberdeen 0-1 Rangers (2026-08-30) read 1-1 for three
    polls at 83-85' and back to 0-1 after — and a flap can only ever push the
    max UP, so every tape error lands as a fabricated WIN on an over. That
    booked pt#5827 (Over 1.5, 4.35) as won on a match that finished 0-1.

    Negative ids are ESPN's namespace (see espn_stats) and are never sent, and
    the first refusal ends the run: every later batch would be refused too.
    """
    key = os.getenv("FOOTBALL_API_KEY", "")
    ids = [f for f in fixture_ids if f > 0]
    if not key or not ids:
        return {}
    out: dict[int, int] = {}
    for i in range(0, len(ids), 20):
        batch = ids[i:i + 20]
        try:
            af_budget.process_counter().record("ids")
            resp = requests.get(
                "https://v3.football.api-sports.io/fixtures",
                params={"ids": "-".join(str(f) for f in batch)},
                headers={"x-apisports-key": key},
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            body = resp.json()
            if body.get("errors"):
                # Quota or rate. Asking again is how a refused key keeps being
                # spent: on 2026-09-14, once the day's allowance was gone, it
                # answered every `ids=` batch with "Free plans do not have
                # access to the Ids parameter".
                break
            for f in body.get("response", []):
                if (f.get("fixture", {}).get("status", {}).get("short")
                        not in ("FT", "AET", "PEN")):
                    continue
                ft = (f.get("score") or {}).get("fulltime") or {}
                if ft.get("home") is None or ft.get("away") is None:
                    continue
                out[f["fixture"]["id"]] = ft["home"] + ft["away"]
        except Exception:
            continue
    return out


def settle(conn) -> int:
    """Fill in both horizons for rows whose fixture has moved on.

    goal_next_10 is read off our own later observations of the same fixture —
    the score at minute+10 is a row we recorded. goal_before_ft needs the FINAL
    score, and that comes from api-football, never from our own tape: see
    `_final_goals_api` for the flap that made the tape maximum unsafe.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, fixture_id, minute, goals_total, target_line,
                      paper_trade_id, entered
                 FROM pressure_observations
                WHERE settled_at IS NULL
                  AND observed_at < now() - interval '15 minutes'
                ORDER BY id"""
        )
        pending = cur.fetchall()

    if not pending:
        # Quiet runs are when the early rows of finished matches are waiting.
        _log_completion(complete_finals(conn))
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

    # The truth, for every pending fixture the API will speak about.
    api_final = _final_goals_api(sorted({r["fixture_id"] for r in pending}))

    settled = 0
    # Every remaining network call, before a single write. `_goal_minute_api`
    # used to run inside the write loop; the decision it depends on is cheap to
    # recompute, and recomputing it is what keeps HTTP out of a transaction.
    # See db_txn.py for the eight-minute idle transaction this shape produced.
    minute_cache: dict[int, int | None] = {}
    for r in pending:
        if not r["entered"]:
            continue
        true_final = api_final.get(r["fixture_id"])
        if true_final is None:
            continue
        if true_final > r["goals_total"]:          # i.e. goal_before_ft
            minute_cache[r["id"]] = _goal_minute_api(r["fixture_id"], r["minute"])

    with conn.cursor() as cur:
        for r in pending:
            obs = series.get(r["fixture_id"], [])
            later = [(m, g) for m, g in obs if m >= r["minute"] + SETTLE_HORIZON_MIN]
            last_minute = max((m for m, _ in obs), default=r["minute"])

            true_final = api_final.get(r["fixture_id"])

            at_plus_10 = goal_next_10 = None
            if later:
                at_plus_10 = min(later, key=lambda x: x[0])[1]
                goal_next_10 = at_plus_10 > r["goals_total"]
                # A tape that claims more goals than the match ever had is
                # lying at this minute too. Refuse the row rather than record
                # a fabricated positive into the calibration arm.
                if true_final is not None and at_plus_10 > true_final:
                    at_plus_10 = goal_next_10 = None

            # The API is the only settlement source. The tape is a fallback for
            # fixtures it will not answer for, and only once our own polls ran
            # past 88' — a fixture we stopped watching at 70' because the Mac
            # went to sleep is not a settled no-goal, and calling it one would
            # bias every result toward the null.
            final_goals = goal_before_ft = None
            final_src = None
            if true_final is not None:
                final_goals, final_src = true_final, "api"
            elif last_minute >= MAX_MINUTE:
                final_goals, final_src = max(g for _, g in obs), "poll"
            if final_goals is not None:
                goal_before_ft = final_goals > r["goals_total"]

            if goal_next_10 is None and goal_before_ft is None:
                continue

            # WHEN the goal came, not just whether — but only for a bet that
            # actually won, so this stays a couple of dozen API calls a day.
            # The call itself happened in the pre-pass above; this only reads
            # the answer, because a network round trip here would hold the row
            # lock open across it.
            goal_minute = goal_src = None
            if r["entered"] and goal_before_ft:
                goal_minute = minute_cache.get(r["id"])
                goal_src = "api" if goal_minute is not None else None
                if goal_minute is None:
                    # Fall back to our own tape: the first later observation
                    # showing a higher score. Late by up to one poll, and it
                    # cannot see stoppage time — hence the recorded source.
                    scored = [m for m, g in obs
                              if m >= r["minute"] and g > r["goals_total"]]
                    if scored:
                        goal_minute, goal_src = min(scored), "poll"

            # Both writes or neither — a settled observation whose trade never
            # resolved is invisible to the next run, because `pending` filters
            # on settled_at. No network call may enter this block.
            with db_txn.atomic(conn):
                cur.execute(
                    """UPDATE pressure_observations
                          SET goals_at_plus_10 = %s, goal_next_10 = %s,
                              final_goals = %s, goal_before_ft = %s,
                              final_goals_source = %s,
                              goal_minute = %s, goal_minute_source = %s,
                              settled_at = now()
                        WHERE id = %s""",
                    (at_plus_10, goal_next_10, final_goals, goal_before_ft,
                     final_src, goal_minute, goal_src, r["id"]),
                )

                # payout_units is GROSS by project convention — lost = 0,
                # won = stake * entry_odds. Booking it net is the bug that had
                # to be repaired across 38 rows on 2026-05-27 and recurred once
                # since, so the odds are read back from the trade rather than
                # recomputed here. The TRADE settles on the line it was bought
                # on, not on whether the score moved off what we happened to
                # read at entry. If the tape was wrong at entry the line is
                # wrong too, and comparing the final total against the line is
                # the only reading that matches what the token actually pays.
                #
                # And only on the API's final. A tape final is what a refused
                # key leaves behind (every settle on the evening of 2026-09-14),
                # and a tape flap can only ever fabricate a WIN on an over
                # (pt#5827). Such a trade waits for complete_finals(), which
                # pays it from the API, or from the tape once the API has had
                # FINAL_API_MAX_AGE_H to answer.
                if final_src == "api" and r["paper_trade_id"]:
                    won = (final_goals > float(r["target_line"])
                           if r["target_line"] is not None else goal_before_ft)
                    cur.execute(
                        """UPDATE paper_trades
                              SET result = %s,
                                  payout_units = CASE WHEN %s
                                                      THEN stake_units * entry_odds
                                                      ELSE 0 END,
                                  resolved_at = now()
                            WHERE id = %s AND result IS NULL""",
                        ("won" if won else "lost", won, r["paper_trade_id"]),
                    )
            settled += 1
    # Rows settled on earlier runs, before the whistle, get their final now.
    # The fixtures this run already put to the API are not asked twice.
    _log_completion(complete_finals(conn, asked={r["fixture_id"] for r in pending}))
    return settled


# How long after a fixture was last seen the API is still asked for its final.
# api-football has the full-time score minutes after the whistle, so a fixture
# it has not finished inside a day was postponed, abandoned or never covered.
# Asking about it on every run is how a question nobody can answer becomes a
# quota problem (see fav_pressure_agent._halftime_scores for the 143k-call
# day). It is also how long a trade waits for the API before a tape final may
# pay it.
FINAL_API_MAX_AGE_H = 24

# Fixtures per UPDATE when one statement completes many at once.
_FINAL_CHUNK = 500


def _fixture_final(api_total, stored_api, last_minute, tape_max):
    """(final_goals, source) for one fixture, or (None, None) if not known yet.

    The API's answer from this run comes first. Then an API final already
    stored on another row of the same fixture: the final is a fact about the
    match, not about the minute a row happened to be observed. Only then our
    own tape, and only once it ran past MAX_MINUTE, the rule settle() applies.
    Two stored API finals that disagree resolve nothing; the API is asked
    again instead of one of them being picked.
    """
    if api_total is not None:
        return api_total, "api"
    known = set(stored_api or [])
    if len(known) == 1:
        return known.pop(), "api"
    if known:
        return None, None
    if last_minute is not None and last_minute >= MAX_MINUTE and tape_max is not None:
        return tape_max, "poll"
    return None, None


def complete_finals(conn, *, asked=frozenset(), max_age_h=FINAL_API_MAX_AGE_H,
                    use_api=True, allow_tape=True, api_tag="api") -> dict:
    """Give the full-time horizon to rows that were settled before it existed.

    settle() writes a row as soon as EITHER horizon is known, and goal_next_10
    is known ten minutes after the row, long before the whistle for anything
    observed before ~70'. Those rows went out with final_goals NULL, and
    because `pending` filters on settled_at nothing ever came back for them:
    546k rows by 2026-09-14, 76% of everything observed before 70'. Those are
    the next-goal rows the factory could neither label nor settle, and 17 of
    its trades were stuck on them. A tape final (`poll`) is provisional in the
    same way. The tape stops when the Mac sleeps and flaps upward on a goal
    that does not stand, so an API final replaces it whenever one exists.

    `asked` holds the fixtures this run's settle() already put to the API, so
    none is asked twice. `max_age_h=None` makes every row a candidate (the
    one-off backfill); `use_api=False` and `allow_tape=False` restrict a run to
    API finals the database already holds.
    """
    age = ("AND observed_at > now() - make_interval(hours => %(h)s)"
           if max_age_h is not None else "")
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT DISTINCT fixture_id
                  FROM pressure_observations
                 WHERE settled_at IS NOT NULL
                   AND (final_goals IS NULL OR final_goals_source = 'poll')
                   {age}""",
            {"h": max_age_h} if max_age_h is not None else None,
        )
        fids = {r[0] for r in cur.fetchall()}
        # A fixture whose trade is still waiting is asked about however old.
        cur.execute(
            """SELECT DISTINCT o.fixture_id
                 FROM pressure_observations o
                 JOIN paper_trades pt ON pt.id = o.paper_trade_id
                WHERE o.entered AND o.settled_at IS NOT NULL AND pt.result IS NULL"""
        )
        fids |= {r[0] for r in cur.fetchall()}
    done = {"fixtures": 0, "rows": 0, "api_rows": 0, "poll_rows": 0,
            "trades": 0, "asked": 0}
    if not fids:
        return done

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT fixture_id,
                      array_agg(DISTINCT final_goals) FILTER (
                          WHERE final_goals_source IN ('api', 'api_repair')) AS stored_api,
                      max(minute) AS last_minute,
                      max(goals_total) AS tape_max,
                      max(observed_at) < now() - make_interval(hours => %s) AS aged_out
                 FROM pressure_observations
                WHERE fixture_id = ANY(%s)
                GROUP BY fixture_id""",
            (FINAL_API_MAX_AGE_H, sorted(fids)),
        )
        facts = {r["fixture_id"]: r for r in cur.fetchall()}
        cur.execute(
            """SELECT o.id, o.fixture_id, o.minute, o.goals_total, o.target_line,
                      o.paper_trade_id
                 FROM pressure_observations o
                 JOIN paper_trades pt ON pt.id = o.paper_trade_id
                WHERE o.entered AND o.settled_at IS NOT NULL AND pt.result IS NULL
                  AND o.fixture_id = ANY(%s)""",
            (sorted(fids),),
        )
        waiting = cur.fetchall()

    # Every network call happens here, before a single write (see db_txn).
    need = sorted(f for f, x in facts.items()
                  if f not in asked and len(set(x["stored_api"] or [])) != 1)
    api = _final_goals_api(need) if (use_api and need) else {}
    done["asked"] = sum(1 for f in need if f > 0) if use_api else 0
    finals = {}
    for f, x in facts.items():
        final, src = _fixture_final(api.get(f), x["stored_api"],
                                    x["last_minute"] if allow_tape else None,
                                    x["tape_max"])
        if final is not None:
            finals[f] = (final, src)
    goal_minutes = {}
    for t in waiting:
        final, src = finals.get(t["fixture_id"], (None, None))
        if src == "api" and final > t["goals_total"]:
            goal_minutes[t["id"]] = _goal_minute_api(t["fixture_id"], t["minute"])

    api_vals = [(f, v, api_tag) for f, (v, s) in finals.items() if s == "api"]
    tape_vals = [(f, v) for f, (v, s) in finals.items() if s == "poll"]
    with conn.cursor() as cur:
        # One statement per chunk of fixtures. A single UPDATE is atomic on its
        # own, and a backfill completes ten thousand fixtures.
        for i in range(0, len(api_vals), _FINAL_CHUNK):
            chunk = api_vals[i:i + _FINAL_CHUNK]
            psycopg2.extras.execute_values(
                cur,
                """UPDATE pressure_observations o
                      SET final_goals = v.f,
                          goal_before_ft = (v.f > o.goals_total),
                          final_goals_source = v.tag,
                          goals_at_plus_10 = CASE WHEN o.goals_at_plus_10 > v.f
                                                  THEN NULL ELSE o.goals_at_plus_10 END,
                          goal_next_10 = CASE WHEN o.goals_at_plus_10 > v.f
                                              THEN NULL ELSE o.goal_next_10 END
                     FROM (VALUES %s) AS v(fid, f, tag)
                    WHERE o.fixture_id = v.fid
                      AND o.settled_at IS NOT NULL
                      AND (o.final_goals IS NULL OR o.final_goals_source = 'poll')""",
                chunk, page_size=len(chunk),
            )
            done["api_rows"] += cur.rowcount
        # A tape final only fills a hole; it never replaces anything.
        for i in range(0, len(tape_vals), _FINAL_CHUNK):
            chunk = tape_vals[i:i + _FINAL_CHUNK]
            psycopg2.extras.execute_values(
                cur,
                """UPDATE pressure_observations o
                      SET final_goals = v.f,
                          goal_before_ft = (v.f > o.goals_total),
                          final_goals_source = 'poll'
                     FROM (VALUES %s) AS v(fid, f)
                    WHERE o.fixture_id = v.fid
                      AND o.settled_at IS NOT NULL
                      AND o.final_goals IS NULL""",
                chunk, page_size=len(chunk),
            )
            done["poll_rows"] += cur.rowcount

        # Trades, on the same rule settle() uses: the line the token was bought
        # on against the final, and a tape final only once the API had its day.
        for t in waiting:
            final, src = finals.get(t["fixture_id"], (None, None))
            if final is None:
                continue
            if src != "api" and not facts[t["fixture_id"]]["aged_out"]:
                continue
            won = (final > float(t["target_line"]) if t["target_line"] is not None
                   else final > t["goals_total"])
            gm = goal_minutes.get(t["id"])
            with db_txn.atomic(conn):
                cur.execute(
                    """UPDATE paper_trades
                          SET result = %s,
                              payout_units = CASE WHEN %s
                                                  THEN stake_units * entry_odds
                                                  ELSE 0 END,
                              resolved_at = now()
                        WHERE id = %s AND result IS NULL""",
                    ("won" if won else "lost", won, t["paper_trade_id"]),
                )
                done["trades"] += cur.rowcount
                if gm is not None:
                    cur.execute(
                        """UPDATE pressure_observations
                              SET goal_minute = %s, goal_minute_source = 'api'
                            WHERE id = %s AND goal_minute IS NULL""",
                        (gm, t["id"]),
                    )
    done["fixtures"] = len(finals)
    done["rows"] = done["api_rows"] + done["poll_rows"]
    return done


def _log_completion(done: dict) -> None:
    if done["rows"] or done["trades"]:
        log.info(f"full-time horizon completed on {done['rows']} earlier rows "
                 f"({done['api_rows']} api, {done['poll_rows']} tape) over "
                 f"{done['fixtures']} fixtures; {done['trades']} trades paid; "
                 f"{done['asked']} fixtures put to the API")


def backfill_finals(conn, *, dry_run: bool) -> dict:
    """One-off repair of every row settled before its final.

    It completes those rows from the API finals the table already holds and
    replaces the tape finals those contradict. It makes no API call: on
    2026-09-14, 10,162 of the 10,707 fixtures that needed a final already had
    one on a later row. Before anything is written, the before-state of every
    row whose existing values change goes to reports/. Those are tape finals,
    and +10 horizons that claimed more goals than the match had. The rows it
    completes are tagged `api_repair`, so the run can be told apart and undone.
    """
    import json

    with conn.cursor() as cur:
        # A whole-table pass. The role's default timeout cancels it.
        cur.execute("SET statement_timeout = '30min'")
        cur.execute(
            """WITH truth AS (
                   SELECT fixture_id, min(final_goals) AS f
                     FROM pressure_observations
                    WHERE final_goals_source IN ('api', 'api_repair')
                    GROUP BY fixture_id
                   HAVING min(final_goals) = max(final_goals))
               SELECT o.id, o.final_goals, o.goal_before_ft, o.final_goals_source,
                      o.goals_at_plus_10, o.goal_next_10, o.goals_total, t.f
                 FROM pressure_observations o
                 JOIN truth t USING (fixture_id)
                WHERE o.settled_at IS NOT NULL
                  AND (o.final_goals_source = 'poll'
                       OR (o.final_goals IS NULL AND o.goals_at_plus_10 > t.f))"""
        )
        changed = cur.fetchall()
        cur.execute(
            """SELECT count(*) FILTER (WHERE final_goals IS NULL),
                      count(*) FILTER (WHERE final_goals_source = 'poll'),
                      count(DISTINCT fixture_id)
                 FROM pressure_observations
                WHERE settled_at IS NOT NULL
                  AND (final_goals IS NULL OR final_goals_source = 'poll')"""
        )
        null_rows, poll_rows, fixtures = cur.fetchone()
    poll = [r for r in changed if r[3] == "poll"]
    summary = {
        "fixtures": fixtures,
        "null_rows": null_rows,
        "poll_rows": poll_rows,
        "poll_rows_with_api": len(poll),
        "poll_value_changes": sum(1 for r in poll if r[1] != r[7]),
        "poll_outcome_flips": sum(1 for r in poll if r[6] is not None
                                  and (r[7] > r[6]) != bool(r[2])),
        "horizon_nulled": sum(1 for r in changed if r[4] is not None and r[4] > r[7]),
    }
    log.info(f"backfill: {summary}")
    if dry_run:
        return summary

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reports",
                        f"pressure_final_backfill_{time.strftime('%Y-%m-%d', time.gmtime())}.json")
    with open(path, "w") as fh:
        json.dump({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": ("Before-state of rows whose existing values backfill_finals() "
                     "overwrote. Rows it filled from NULL are the rows tagged "
                     "final_goals_source='api_repair' that are not listed here."),
            "summary": summary,
            "columns": ["id", "final_goals", "goal_before_ft", "final_goals_source",
                        "goals_at_plus_10", "goal_next_10"],
            "rows": [list(r[:6]) for r in changed],
        }, fh)
    log.info(f"backfill: before-state of {len(changed)} rows -> {path}")
    done = complete_finals(conn, max_age_h=None, use_api=False, allow_tape=False,
                           api_tag="api_repair")
    _log_completion(done)
    return {**summary, **done}


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
    dead_polls = 0

    while True:
        t0 = time.time()

        # The alarm for the bug in db_txn.py. With autocommit this cannot fire;
        # if it ever does, something opened a transaction and walked away, and
        # the next thing this process does is sleep on it — holding locks and
        # pinning a snapshot against VACUUM on tables that write every cycle.
        # Roll it back and say so, rather than discovering it in pg_stat_activity
        # a month later.
        if conn is not None and not conn.closed and db_txn.in_transaction(conn):
            log.error("connection was left INSIDE a transaction at the top of a "
                      "cycle — rolling back; this is the db_txn.py bug returning")
            try:
                conn.rollback()
            except psycopg2.Error:
                conn = _reconnect(conn)

        if t0 - last_markets > REFRESH_MARKETS_S or not pm_fixtures:
            pm_fixtures = _fetch_events()
            last_markets = t0
            _pm_universe_refreshed()
            log.info(f"PM universe -> {len(pm_fixtures)} football fixtures")

        signals = tracker.poll(priority=_enrich_priority(tracker, pm_fixtures))

        # On 2026-08-25 at 05:39 this process lost the ability to open a socket
        # — every request raised PermissionError(1, 'Operation not permitted'),
        # a per-process fault that a fresh interpreter does not have. It ran for
        # seven and a half hours logging "live=0" once a minute, which is what a
        # quiet morning looks like, and recorded nothing. The wrapper restarts
        # this agent when it EXITS, so an error we catch and carry on from is an
        # error the wrapper cannot help with. Hand it back the only thing it
        # knows how to fix.
        dead_polls = dead_polls + 1 if tracker.last_poll_failed else 0
        if dead_polls >= DEAD_POLLS_BEFORE_EXIT:
            log.error(f"{dead_polls} consecutive polls could not reach api-football "
                      f"— exiting so the wrapper restarts on a fresh process")
            if conn is not None:
                conn.close()
            return
        rows = observe(signals, table, pm_fixtures, pre_cache,
                       tracker.window_minutes, tracker.enrich_status)
        ht_rows = ht.observe(signals, ht_table, pm_fixtures, ht_state,
                             tracker.enrich_status)
        fav_rows = fav.observe(signals, fav_table, pm_fixtures, fav_state,
                               tracker.enrich_status)

        # Check the socket before writing, not after it throws: a wake costs one
        # extra round trip here instead of a whole cycle of observations.
        if not dry_run and not _db_alive(conn):
            log.warning("db connection is dead (sleep/wake?) — reconnecting")
            conn = _reconnect(conn)
            if conn is None:
                log.warning("no db connection — this cycle is observed but not stored")

        db_dropped = False

        opened = 0
        if rows and conn is not None:
            try:
                opened = open_trades(conn, strategy_id, rows)
                _write(conn, rows)
            except _DB_DROPPED as exc:
                log.warning(f"db dropped mid-write ({exc.__class__.__name__}) — "
                            f"{len(rows)} rows lost, next poll is {interval}s away")
                db_dropped, opened, rows = True, 0, []

        ht_opened = 0
        if ht_rows and conn is not None and not db_dropped:
            try:
                ht_opened = ht.open_trades(conn, ht_strategy_id, ht_rows)
                ht.write(conn, ht_rows)
            except _DB_DROPPED as exc:
                log.warning(f"db dropped mid-write [HT] ({exc.__class__.__name__}) — "
                            f"{len(ht_rows)} rows lost")
                db_dropped, ht_opened, ht_rows = True, 0, []
        for r in ht_rows:
            if r["entered"]:
                log.info(
                    f"  ENTER  [HT] {r['home'][:16]:16} 0-0 {r['away'][:16]:16} "
                    f"{r['minute']}'  1H O0.5 ask={r['best_ask']:.3f} "
                    f"({1 / r['best_ask']:.2f})  opening="
                    f"{r['pressure_now'] or r['opening_pressure']:.0f}"
                    f"[{(r['pressure_source'] or 'opening')[:3]}]  #{r['paper_trade_id']}"
                )

        fav_opened = 0
        if fav_rows and conn is not None and not db_dropped:
            try:
                fav_opened = fav.open_trades(conn, fav_strategy_id, fav_rows)
                fav.write(conn, fav_rows)
            except _DB_DROPPED as exc:
                log.warning(f"db dropped mid-write [FAV] ({exc.__class__.__name__}) — "
                            f"{len(fav_rows)} rows lost")
                db_dropped, fav_opened, fav_rows = True, 0, []
        for r in fav_rows:
            if r["entered"]:
                log.info(
                    f"  ENTER  [FAV] {r['fav_team'][:20]:20} lead@HT {r['minute']}'  "
                    f"ask={r['best_ask']:.3f} ({1 / r['best_ask']:.2f})  "
                    f"fav={r['fav_prob']:.0%} press={r['fav_pressure_now']:.0f} "
                    f"dom={r['dominance_now']:+.0f}"
                    f"[{(r['pressure_source'] or 'opening')[:3]}]  #{r['paper_trade_id']}"
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

        if db_dropped:
            conn = _reconnect(conn)

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
    ap.add_argument("--backfill-finals", action="store_true",
                    help="one-off: complete rows settled before their final, from API "
                         "finals already stored (no API call); with --dry-run, count only")
    args = ap.parse_args()

    if args.backfill_finals:
        conn = _conn()
        try:
            backfill_finals(conn, dry_run=args.dry_run)
        finally:
            conn.close()
        return

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
