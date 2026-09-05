#!/usr/bin/env python3
"""
First-half pressure agent — the Live Pressure Overs thesis, moved to the market
it was really about: PM's "1st Half O/U 0.5" while the match is still 0-0.

THE RULE (as specified)
-----------------------
A fixture is still 0-0, the clock has passed 15 minutes, and the first 15 minutes
were played at high pressure. Buy Over 0.5 for the first half, 1u, paper.

It is a PREDICTION, not a price comparison — the same design as the sibling
agent's obs_version 2. The fair value from the empirical table is computed and
stored on every row, with and without the pressure term, because that is the null
this has to beat; it does not gate the entry.

WHY THIS MARKET IS HARDER THAN IT LOOKS — read before touching the gates
-----------------------------------------------------------------------
Backing this over generically LOSES. On 236 fixtures reconstructed from the CLOB,
PM's price sat ABOVE the realised frequency at every minute tested from 5' to 40',
by roughly 4pp (CI crossed zero, so it is a hypothesis, not a result). The market
is, if anything, rich — so the pressure filter is not being asked to find a fair
price, it is being asked to beat a price that already starts against us by ~4pp
plus the taker fee plus the spread.

Two more things the earlier work established, both of which shape this file:

  * Liquidity is micro. One live example: $938 across the whole 1st-half 0.5 book
    against $32,718 on the same fixture's full-match O/U 2.5, with a 4pp spread.
    Hence MIN_DEPTH_USD well below the sibling's.
  * A FIXED entry price is adversely selected by the clock. A resting bid at 1.70
    from minute 10 filled in 83% of matches — at a median minute of 16, not 10,
    and the median fill minute rises monotonically with the price demanded. That
    is why this agent takes the ask at a decided minute instead of resting a
    price and waiting to be hit.

WHAT "PRESSING" MEANS HERE — obs_version 3, 2026-09-05
------------------------------------------------------
Up to and including minute 18 there is nothing to take a delta against — the
match so far IS the window — so pressure is read off the cumulative totals,
scaled to a 15-minute rate so the number lands on the same 0-100 axis (see
live_tracker.danger_index). After 18' it is the ROLLING 15-minute window, the
same measure the sibling full-match arm has always used.

Until 2026-09-05 the 15-18' reading was frozen and was the only thing that could
ever open a trade. That threw away every fixture whose pressure arrived late:
Manchester City vs Coventry read +16.7 dominance at 15' (85% possession, zero
shots) against a gate of 20, and +32 at 22' with the reading already frozen. Now
the gate is re-applied at every poll out to minute 40, so a late surge enters at
a longer price — the fair value has fallen with the clock and so has the ask.

Two things that did NOT change, deliberately:

  * a fixture with no window baseline still gets no reading and no entry. A
    fallback that looks like the real measurement is how a strategy ends up
    evaluated on a quantity it never traded — the same discipline as has_window.
  * the frozen 15-18' reading is still recorded on every row (opening_pressure).
    It is the control arm for "does the opening quarter of an hour carry
    something a later window does not", pre-registered as H-PRESSURE-LATE.

⚠️ Entering later buys longer odds, NOT a cheaper market: `real - ask` on clean
books is about equally negative at every minute (-3.4pp at 15-19', -4.9 at 20-24',
-3.1 at 25-29', -2.3 at 30-34', -5.4 at 35-40'; all CIs cross zero). See db/040.

Usage:
    python ht_pressure_agent.py --once            # one cycle (own tracker poll)
    python ht_pressure_agent.py --once --dry-run  # no DB writes, no trades
    python ht_pressure_agent.py                   # forever, 60s
    python ht_pressure_agent.py --settle          # backfill outcomes
    python ht_pressure_agent.py --report          # what has been collected

In production it does NOT run as its own process: pressure_agent.py drives both
arms off one tracker poll, because two processes would mean two api-football live
calls and two stat budgets a cycle, and the quota is the binding constraint.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

import first_half_table as fht                                     # noqa: E402
from edge_engine import taker_fee_pp                               # noqa: E402
from late_goals_observer import (                                  # noqa: E402
    _fetch_book,
    _fetch_events,
    infer_goals,
    ladder_consistent,
    ladder_of,
    pm_over25,
)
from live_tracker import LiveMatchTracker, PressureSignals, danger_index  # noqa: E402
from pressure_agent import (                                       # noqa: E402
    _no_stats_reason,
    apply_pressure,
    match_pm_fixture,
    pressure_factor,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("ht_pressure")

DATABASE_URL = os.getenv("DATABASE_URL")
STRATEGY_NAME = "Live Pressure HT Over 0.5"
OBS_VERSION = 3

CYCLE_S = 60
REFRESH_MARKETS_S = 300

# ── the measurement window ───────────────────────────────────────────────────
# Up to FIRST15_MAX the reading is the cumulative one — the match so far scaled
# to a 15-minute rate — and the first such reading is ALSO frozen into
# opening_pressure, which is now the control arm rather than the gate. Past this
# minute the live reading switches to the rolling 15-minute window; a genuine
# baseline exists by then and a cumulative average would dilute a late surge into
# a quiet opening, which is exactly the fixture this version exists to catch.
FIRST15_MIN, FIRST15_MAX = 15, 18

# ── observation window ───────────────────────────────────────────────────────
# Rows are written for the whole first half so the base rate has its own control
# group — every 0-0 fixture we watched, entered or not. Below 10' there is not
# enough play to measure anything and the stat feed is mostly still empty.
OBSERVE_MIN_MINUTE = 10
OBSERVE_MAX_MINUTE = 46

# ── entry gates ──────────────────────────────────────────────────────────────
ENTRY_MIN_MINUTE = 15           # the rule, as specified
# RAISED 25 -> 40 on 2026-09-05 with the reading unfrozen (obs_version 3). While
# the gate ran off a frozen first-15 number, minutes past 25' were only ever
# cover for a missing book, and the note that used to sit here — "past 25' the
# fair value has fallen by a third and the bet stops being the one the first-15
# reading was about" — was correct. It is no longer the same bet BECAUSE the
# reading is no longer the first-15 one: at 32' the gate is asking whether the
# match is being pressed at 32'. The empirical table runs to 44' and the ask is
# about equally rich at every minute out to 40 (db/040), so the ceiling is set by
# where the table and the book still exist, not by a view about the clock.
ENTRY_MAX_MINUTE = 40
# LOWERED 25 -> 19 on 2026-08-20, by the user's decision and not by any fit.
# Percentiles of this exact index on 181 real openings, xG renormalised (see
# live_tracker.danger_index): median 15, p73 19, p89 25, p95 29. The bar moved
# from the top decile to the top quartile — 11.0% of fixtures cleared 25, 26.5%
# clear 19 — which is roughly 2.4x the entries and the difference between
# reaching the n>=200 verdict gate in months rather than years.
#
# Be honest about the cost: "high pressure" now means "busier than three fixtures
# in four", not "one of the liveliest openings of the day", and the hypothesis
# pre-registered in db/033 is written about the strong claim. opening_pressure is
# stored on every row precisely so "does it separate at 25 but not at 19" stays a
# question the data can answer later, rather than one guessed at now.
#
# The axis is shared with the sibling agent but the distributions are not: a
# 15-minute window late in a stretched match accumulates far more than the
# opening quarter of an hour of a 0-0. Neither threshold has ever been fitted to
# an outcome — that is what this table is being recorded to make possible.
#
# obs_version 3 applies this same number to the ROLLING reading, and it was
# checked rather than assumed. The rolling index was reconstructed offline from
# the cumulative stats already on this table (4,557 poll-rows, minutes 19-44,
# still 0-0, deltas against the row nearest minute-15):
#
#   both ends   p25  8.3   p50 13.3   p75 18.8   p90 24.4   p95 27.7
#
# — within a point of the opening-15 distribution above (median 15, p73 19, p89
# 25) and flat across the clock (p75 17.7 at 25-29' against 19.4 at 40-44'). So
# 19 still means "busier than three fixtures in four" on the new axis: 23.2% of
# rolling readings clear it. No threshold was refitted to an outcome.
MIN_PRESSURE = 19.0
MAX_ASK = 0.85                  # PM asks above 0.85 resolve far below their price
# The sibling asks for $50. This book is an order of magnitude thinner (see the
# header), and 1u of paper stands in for a $1-2.50 real order, so $25 across the
# top five levels is the honest floor. Below it there is no fill to speak of.
MIN_DEPTH_USD = 25.0
# obs_version 2 (2026-08-31): a spread cap, ported from the sibling's v4 but with
# the threshold MEASURED HERE rather than inherited — this book is not that book.
# 3,420 rows / 552 fixtures, minute 15-25, still 0-0, `real - ask` clustered by
# fixture:
#
#   spread  0-3pp    +0.41pp        spread 10-20pp    -7.67pp
#   spread  3-6pp    -0.77pp        spread 20pp+     -38.93pp CI[-47.4,-30.4]
#   spread 6-10pp    -6.01pp CI[-11.0, -1.0]
#
# The break is at 6pp, same as the sibling. Depth is NOT ported: here it comes
# out non-monotone ($0-25 +2.19, $200-1000 -7.12, $1000+ +0.40), so there is no
# measurement supporting a floor above the $25 argued for in the header.
#
# CA Mineiro vs EC Vitória (2026-08-29) is the case this exists for: at 15' this
# market quoted bid 0.55 / ask 0.99 on $30k of depth, and traded at 0.56 two
# minutes later. Depth was never the tell — an ask of 0.99 with $30k behind it
# clears any depth floor. The spread is the tell.
MAX_SPREAD = 0.06
STAKE_UNITS = 1.0

# ── PM market discovery ──────────────────────────────────────────────────────
# "<Home> vs. <Away>: 1st Half O/U 0.5" — the merged fixture also carries
# "1st Half O/U 3.5 Total Corners" (a different quantity entirely) and
# "<Team> 1st Half O/U 0.5" (a team total, which pays on one side scoring). Both
# have to fail. The classifier that let team totals through on short club names
# is documented in sim_scanner._totals_scope; this is the same rule, narrowed to
# the single line this agent trades.
_HT_OVER05_RE = re.compile(r"^1st\s+half\s+o/u\s+0\.5$", re.I)


def ht_over05_market(fixture: dict) -> dict | None:
    """The fixture's 1st-half over-0.5 market, or None.

    Returns Gamma metadata only — the price used for a decision always comes
    from the CLOB book, never from outcomePrices.
    """
    for mkt in fixture.get("markets") or []:
        if mkt.get("closed"):
            continue
        q = (mkt.get("question") or "").strip()
        rem = q.split(": ", 1)[1] if ": " in q else q
        if not _HT_OVER05_RE.match(rem.strip()):
            continue
        try:
            outcomes = json.loads(mkt.get("outcomes") or "[]")
            token_ids = json.loads(mkt.get("clobTokenIds") or "[]")
            prices = json.loads(mkt.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for i, name in enumerate(outcomes):
            if not str(name).strip().lower().startswith("over"):
                continue
            if i >= len(token_ids) or not token_ids[i]:
                continue
            return {
                "question": q,
                "token_id": token_ids[i],
                "condition_id": mkt.get("conditionId"),
                "gamma_price": _f(prices[i]) if i < len(prices) else None,
            }
    return None


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_table() -> dict:
    """The empirical first-half baseline. Named so the driver in pressure_agent
    does not have to know which module the table lives in."""
    return fht.load()


# ── pressure ─────────────────────────────────────────────────────────────────

def opening_pressure(sig: PressureSignals) -> tuple[float, float, float]:
    """(both ends, home, away) pressure so far, expressed as a 15-minute rate.

    At minute 15 the scale factor is 1 and this is literally "the first fifteen
    minutes". Later it is an average intensity, which is NOT the same signal —
    hence only the reading taken inside the FIRST15 window is ever traded on.
    """
    # Divided by the minute the STATS were true, not the minute of this poll:
    # with a 180s TTL against a 60s cycle the two differ by up to three minutes,
    # and using the poll's minute would deflate the index by up to 20% on exactly
    # the fixtures that were measured least recently.
    scale = 15.0 / max(sig.stats_minute or sig.minute, 1)
    # xG coverage is a property of the FEED for this fixture, so the flag is read
    # across both sides: one team having created nothing is not the same as
    # api-football publishing no xG for the competition.
    has_xg = bool(sig.home_xg_total or sig.away_xg_total)
    home = danger_index(
        sig.home_shots_on_total * scale, sig.home_shots_inside_total * scale,
        sig.home_xg_total * scale, sig.home_corners_total * scale,
        sig.home_possession, has_xg=has_xg)
    away = danger_index(
        sig.away_shots_on_total * scale, sig.away_shots_inside_total * scale,
        sig.away_xg_total * scale, sig.away_corners_total * scale,
        sig.away_possession, has_xg=has_xg)
    # Both ends, because an over does not care who scores.
    return (home + away) / 2.0, home, away


def window_pressure(sig: PressureSignals) -> tuple[float, float, float] | None:
    """(both ends, home, away) over the ROLLING 15-minute window, or None.

    None when live_tracker found no baseline near (minute - 15), or when the
    baseline and the latest snapshot carry the SAME paid fetch — differencing a
    stat block against itself reports a dead match, and scaling a frozen one
    reports steady play as a surge. Both cases are already folded into
    has_window; this function does not second-guess it, it just refuses to
    invent a number.

    Possession is the one input that is not a delta: api-football publishes it as
    a running match percentage, not a per-minute count, so the window carries the
    match's possession to date. It is 10% of the index, and the alternative — a
    possession delta computed from two running averages — is not a quantity.
    """
    if not sig.has_window:
        return None
    # The FEED's xG coverage, read off the totals: a window with no xG in it is a
    # window where nothing was created, which is a real reading. A fixture whose
    # competition publishes no xG at all is a different statement, and the one
    # danger_index needs to renormalise for.
    has_xg = bool(sig.home_xg_total or sig.away_xg_total)
    home = danger_index(
        sig.home_shots_on_window, sig.home_shots_inside_window,
        sig.home_xg_window, sig.home_corners_window,
        sig.home_possession, has_xg=has_xg)
    away = danger_index(
        sig.away_shots_on_window, sig.away_shots_inside_window,
        sig.away_xg_window, sig.away_corners_window,
        sig.away_possession, has_xg=has_xg)
    return (home + away) / 2.0, home, away


def current_pressure(sig: PressureSignals) -> tuple[float, float, float, str] | None:
    """The reading the entry gate is applied to: (both ends, home, away, source).

    One function, used by both first-half arms, so the two cannot drift apart on
    what "pressure now" means. `source` goes onto the row because the two halves
    are different measurements sharing an axis, and a later fit has to be able to
    separate them.
    """
    if sig.minute <= FIRST15_MAX:
        both, home, away = opening_pressure(sig)
        return both, home, away, "opening"
    win = window_pressure(sig)
    if win is None:
        return None
    return win[0], win[1], win[2], "window"


# ── cross-cycle state ────────────────────────────────────────────────────────

@dataclass
class HTState:
    """What has to survive between polls.

    first15  — fixture_id -> the frozen opening measurement.
    pre      — PM fixture title -> {p, prematch}. The bucket the fair value is
               read from is the PRE-MATCH total, so a value first seen at 20'
               into a goalless match is not it: that price has already drifted
               down and would push the fixture into the 'lo' bucket and quietly
               lower our own fair value. Captured before kickoff or not used.
    """
    first15: dict[int, dict] = field(default_factory=dict)
    pre: dict[str, dict] = field(default_factory=dict)

    def prune(self, older_than_s: float = 6 * 3600) -> None:
        """Forget fixtures that finished hours ago.

        This process runs for weeks at a time and neither dict is ever emptied
        otherwise: every fixture watched since the last restart would stay
        resident, and every PM title alongside them.
        """
        cutoff = time.time() - older_than_s
        for fid in [k for k, v in self.first15.items() if v["at"] < cutoff]:
            del self.first15[fid]
        for title in [k for k, v in self.pre.items() if v["at"] < cutoff]:
            del self.pre[title]


def _note_pre_match_total(state: HTState, fx: dict, cells: dict) -> dict:
    title = fx["title"]
    have = state.pre.get(title)
    if have and have["prematch"]:
        return have
    kickoff = fx.get("kickoff")
    before_kickoff = bool(kickoff and datetime.now(timezone.utc) < kickoff)
    p = pm_over25(cells)
    if p is None:
        return have or {"p": None, "prematch": False, "at": time.time()}
    if have is None or (before_kickoff and not have["prematch"]):
        state.pre[title] = {"p": p, "prematch": before_kickoff, "at": time.time()}
    return state.pre[title]


# ── one cycle ────────────────────────────────────────────────────────────────

def observe(signals: dict[int, PressureSignals], table: dict,
            pm_fixtures: list[dict], state: HTState,
            enrich_status: dict[int, str] | None = None) -> list[dict]:
    """One row per live first-half fixture. Entry decisions are set, not taken.

    `enrich_status` is the tracker's own account of why a fixture has no stats.
    Without it every unmeasured fixture is recorded as "no coverage", which is
    the mislabelling that put 36,917 rows into the sibling's table claiming a
    competition had no stats when the truth was that we never asked.
    """
    state.prune()
    rows: list[dict] = []
    for sig in signals.values():
        if not (OBSERVE_MIN_MINUTE <= sig.minute <= OBSERVE_MAX_MINUTE):
            continue

        row = _base_row(sig)
        goals = sig.home_goals + sig.away_goals

        # Freeze the opening measurement the first time we are in the window and
        # actually have stats. Without stats every component is zero and the
        # index collapses to its possession term — indistinguishable from a
        # genuinely quiet match, which is how 36,917 rows once entered the
        # sibling's record as evidence.
        if sig.has_stats:
            row["pressure_index"], row["home_danger"], row["away_danger"] = opening_pressure(sig)
            # The live reading — cumulative to 18', rolling window after — and
            # the minute the stats behind it were actually true at, which is up
            # to three minutes behind this poll under the enrich TTL.
            now = current_pressure(sig)
            row["has_window"] = sig.has_window
            if now is not None:
                row["pressure_now"] = now[0]
                row["pressure_source"] = now[3]
                row["pressure_minute"] = sig.stats_minute or sig.minute
            # xG carries 40% of the index and api-football only supplies it on
            # about half the fixtures it covers with stats at all. Without it a
            # genuine shooting gallery reads ~40% quieter than it was, so a
            # fixture missing xG is effectively out of the running — recorded
            # here so the eventual fit can control for it instead of treating
            # two different measurements as one.
            row["has_xg"] = bool(sig.home_xg_total or sig.away_xg_total)
            if (FIRST15_MIN <= sig.minute <= FIRST15_MAX
                    and sig.fixture_id not in state.first15):
                state.first15[sig.fixture_id] = {
                    "pressure": row["pressure_index"],
                    "minute": sig.minute,
                    "at": time.time(),
                    "home_danger": row["home_danger"],
                    "away_danger": row["away_danger"],
                    "goals": goals,
                }
        else:
            row["skip_reason"] = _no_stats_reason(
                (enrich_status or {}).get(sig.fixture_id, ""))

        opening = state.first15.get(sig.fixture_id)
        if opening:
            row["opening_pressure"] = opening["pressure"]
            row["opening_minute"] = opening["minute"]

        fx = match_pm_fixture(sig, pm_fixtures)
        if fx is None:
            # Overwrites any stats reason on purpose: a fixture PM does not list
            # is deliberately never given a stats call, so "no stats" is the
            # consequence and this is the cause.
            row["skip_reason"] = "no PM fixture"
            rows.append(row)
            continue
        row["event_title"] = fx["title"]

        cells = ladder_of(fx)
        pre = _note_pre_match_total(state, fx, cells)
        row["pre_over25"] = pre["p"]
        row["pre_is_prematch"] = pre["prematch"]

        # The full-match over ladder is not traded here; it is read only to see
        # whether Gamma agrees the score is still 0-0. It was wrong on 29% of
        # polls on the late-goals v1 series, so it is evidence, never authority.
        ladder = {ln: c["over_price"] for ln, c in cells.items()}
        if ladder and ladder_consistent(ladder):
            lower, upper, certain = infer_goals(ladder)
            if certain:
                row["ladder_goals"] = lower
                row["score_agrees"] = (lower == goals)

        mkt = ht_over05_market(fx)
        if not mkt:
            row["skip_reason"] = row["skip_reason"] or "no PM 1st-half 0.5 market"
            rows.append(row)
            continue
        row["condition_id"] = mkt["condition_id"]
        row["token_id"] = mkt["token_id"]

        # The CLOB, not Gamma. Booking paper fills at prices nobody was quoting
        # is what turned the convergence trader's +141% paper into +3.4% real.
        book = _fetch_book(mkt)
        if not book:
            row["skip_reason"] = row["skip_reason"] or "no book"
            rows.append(row)
            continue
        row.update(best_bid=book["best_bid"], best_ask=book["best_ask"],
                   bid_depth_usd=book["bid_depth_usd"],
                   ask_depth_usd=book["ask_depth_usd"])

        # Fair value is only defined from 0-0: the table is conditional on it,
        # and once a goal is in the market is settled anyway.
        if goals == 0:
            fair_base, fair_n = fht.lookup(
                table, sig.minute, pre["p"] if pre["prematch"] else None)
            if fair_base is None:
                row["skip_reason"] = row["skip_reason"] or f"minute {sig.minute} off the grid"
            else:
                k = pressure_factor(row["pressure_now"] or row["opening_pressure"]
                                    or row["pressure_index"] or 0.0)
                fair_pressure = apply_pressure(fair_base, k)
                fee = taker_fee_pp(book["best_ask"])
                row.update(
                    fair_base=fair_base, fair_pressure=fair_pressure, fair_n=fair_n,
                    pressure_factor=k, fee_pp=fee,
                    edge_base_pp=100.0 * (fair_base - book["best_ask"]) - fee,
                    edge_pressure_pp=100.0 * (fair_pressure - book["best_ask"]) - fee,
                )

        row["would_enter"] = bool(
            goals == 0
            and ENTRY_MIN_MINUTE <= sig.minute <= ENTRY_MAX_MINUTE
            # The reading as of THIS poll — cumulative to 18', rolling window
            # after. Never the running average past 18': it dilutes a late surge
            # into the quiet opening that preceded it.
            and row["pressure_now"] is not None
            and row["pressure_now"] >= MIN_PRESSURE
            and sig.has_stats
            and book["best_ask"] <= MAX_ASK
            and book["best_bid"] is not None
            and (book["best_ask"] - book["best_bid"]) <= MAX_SPREAD
            and (book["ask_depth_usd"] or 0) >= MIN_DEPTH_USD
            # A score we cannot pin makes the whole setup meaningless — the bet
            # is defined by the match being 0-0.
            and row["score_agrees"] is not False
        )
        if not row["would_enter"] and not row["skip_reason"]:
            row["skip_reason"] = _why_not(row, book, sig)
        rows.append(row)

    return rows


def _why_not(row: dict, book: dict, sig: PressureSignals) -> str:
    goals = sig.home_goals + sig.away_goals
    if goals:
        return f"not 0-0 ({sig.home_goals}-{sig.away_goals})"
    if sig.minute < ENTRY_MIN_MINUTE:
        return f"minute {sig.minute} < {ENTRY_MIN_MINUTE}"
    if sig.minute > ENTRY_MAX_MINUTE:
        return f"minute {sig.minute} > {ENTRY_MAX_MINUTE}"
    if row["pressure_now"] is None:
        return (f"no {FIRST15_MIN}-minute window at {sig.minute}' "
                f"(fixture picked up late)")
    if row["pressure_now"] < MIN_PRESSURE:
        return (f"{row['pressure_source'] or 'pressure'} pressure "
                f"{row['pressure_now']:.0f} < {MIN_PRESSURE}")
    if book["best_ask"] > MAX_ASK:
        return f"ask {book['best_ask']:.2f} > {MAX_ASK}"
    if book["best_bid"] is None:
        return "one-sided book: no bid"
    if (book["best_ask"] - book["best_bid"]) > MAX_SPREAD:
        return f"spread {100 * (book['best_ask'] - book['best_bid']):.0f}pp > {100 * MAX_SPREAD:.0f}pp"
    if (book["ask_depth_usd"] or 0) < MIN_DEPTH_USD:
        return f"depth ${book['ask_depth_usd']:.0f} < ${MIN_DEPTH_USD:.0f}"
    if row["score_agrees"] is False:
        return f"score disagreement: api={goals} ladder={row['ladder_goals']}"
    return ""


def _base_row(sig: PressureSignals) -> dict:
    return {
        "obs_version": OBS_VERSION,
        "fixture_id": sig.fixture_id,
        "league": sig.league,
        "home": sig.home, "away": sig.away,
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
        "has_stats": sig.has_stats, "has_xg": False,
        "home_danger": None, "away_danger": None,
        "pressure_index": None,          # cumulative, scaled to 15 min, this poll
        "opening_pressure": None,        # the frozen first-15 reading — CONTROL
        "opening_minute": None,
        "pressure_now": None,            # what the gate reads — obs_version 3
        "pressure_source": None,         # 'opening' | 'window'
        "pressure_minute": None,
        "has_window": False,
        "pressure_factor": None,
        "pre_over25": None, "pre_is_prematch": False,
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
    "has_stats", "has_xg",
    "home_danger", "away_danger", "pressure_index", "opening_pressure",
    "opening_minute", "pressure_now", "pressure_source", "pressure_minute",
    "has_window", "pressure_factor", "pre_over25", "pre_is_prematch",
    "best_bid", "best_ask", "bid_depth_usd", "ask_depth_usd",
    "fair_base", "fair_pressure", "fair_n", "fee_pp",
    "edge_base_pp", "edge_pressure_pp", "would_enter", "entered",
    "paper_trade_id", "skip_reason",
]


def write(conn, rows: list[dict]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO ht_pressure_observations ({','.join(_COLS)}) VALUES %s",
            [[r.get(c) for c in _COLS] for r in rows],
        )
    conn.commit()


def strategy_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
        got = cur.fetchone()
    if not got:
        raise SystemExit(f"strategy '{STRATEGY_NAME}' missing — apply db/033")
    return got[0]


def open_trades(conn, sid: int, rows: list[dict]) -> int:
    """One 1u paper position per qualifying fixture.

    The unique index on (fixture_id) WHERE entered is what stops the agent
    re-buying the same market every cycle while a team keeps pressing — there is
    only one line here, so a second entry would be a stake-size artifact.
    """
    opened = 0
    for r in rows:
        if not r["would_enter"]:
            continue
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM ht_pressure_observations "
                "WHERE fixture_id = %s AND entered LIMIT 1",
                (r["fixture_id"],),
            )
            if cur.fetchone():
                r["would_enter"], r["skip_reason"] = False, "already entered this fixture"
                continue

            fair_txt = (
                f"base fair {r['fair_base']:.3f} ({1 / r['fair_base']:.2f}, "
                f"n={r['fair_n']}) -> pressure fair {r['fair_pressure']:.3f} "
                f"({1 / r['fair_pressure']:.2f}) at k={r['pressure_factor']:.2f}, "
                f"i.e. {r['edge_pressure_pp']:+.1f}pp after {r['fee_pp']:.2f}pp fee"
                if r["fair_base"] else "no fair value on the grid for this state"
            )
            span = (f"the first {r['pressure_minute']} minutes"
                    if r["pressure_source"] == "opening"
                    else f"the 15 minutes to {r['pressure_minute']}'")
            reasoning = (
                f"{r['home']} 0-0 {r['away']} {r['minute']}' — Over 0.5 first half "
                f"at {r['best_ask']:.3f} ({1 / r['best_ask']:.2f}). "
                f"Pressure {r['pressure_now']:.0f}/100 over {span} "
                f"(danger H={r['home_danger']:.0f} A={r['away_danger']:.0f}). "
                f"PREDICTION: a goalless match being played at this intensity is "
                f"more likely to produce a goal before the break than the market "
                f"is paying for. Bought on "
                f"that call alone, not on a price comparison. For the record, "
                f"{fair_txt} — recorded as the null, NOT a gate. "
                f"PAPER — and note PM's price on this market has historically sat "
                f"~4pp ABOVE the realised frequency, so this starts from behind."
            )
            cur.execute(
                """INSERT INTO paper_trades
                     (strategy_id, outcome, entry_price, entry_odds, stake_units,
                      model_probability, expected_edge, reasoning, confidence,
                      pm_token_id, pm_live)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)
                   RETURNING id""",
                (sid,
                 f"1st Half Over 0.5 — {r['event_title'] or r['home'] + ' vs ' + r['away']}",
                 r["best_ask"], 1.0 / r["best_ask"], STAKE_UNITS,
                 r["fair_pressure"], (r["edge_pressure_pp"] or 0.0) / 100.0,
                 reasoning, "paper", r["token_id"]),
            )
            r["paper_trade_id"] = cur.fetchone()[0]
            r["entered"] = True
            opened += 1
        conn.commit()
    return opened


# ── settlement ───────────────────────────────────────────────────────────────

def _first_half_goals_api(fixture_id: int) -> tuple[int | None, int | None]:
    """(goals before half time, minute of the first one) from api-football.

    The authority for this market, and the only source that gets 45+2 right:
    api-football reports it as elapsed=45, extra=2, while our own 60s tape may
    simply never see a goal scored during the break-bound stoppage. One call per
    fixture per settle run.
    """
    key = os.getenv("FOOTBALL_API_KEY", "")
    if not key:
        return None, None
    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures/events",
            params={"fixture": fixture_id, "type": "Goal"},
            headers={"x-apisports-key": key},
            timeout=8,
        )
        if resp.status_code != 200:
            return None, None
        body = resp.json()
        if body.get("errors"):
            return None, None                 # quota/rate — fall back to the tape
        goals, first = 0, None
        for ev in body.get("response", []):
            detail = (ev.get("detail") or "").lower()
            if "missed" in detail:            # a missed penalty is not a goal
                continue
            t = ev.get("time") or {}
            elapsed = t.get("elapsed")
            if elapsed is None or elapsed > 45:
                continue
            goals += 1
            minute = elapsed + (t.get("extra") or 0)
            first = minute if first is None else min(first, minute)
        return goals, first
    except Exception:
        return None, None


# A fixture is only settled once its tape has actually reached the break. Calling
# a match we stopped watching at 30' a "no goal" would bias every result toward
# the null — the same trap the late-goals observer fell into by trusting a clock
# nobody checked.
TAPE_HT_MINUTE = 43


def settle(conn) -> int:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, fixture_id, minute, paper_trade_id, entered
                 FROM ht_pressure_observations
                WHERE settled_at IS NULL
                  AND observed_at < now() - interval '35 minutes'
                ORDER BY id"""
        )
        pending = cur.fetchall()

    if not pending:
        return 0

    fixture_ids = sorted({r["fixture_id"] for r in pending})
    with conn.cursor() as cur:
        cur.execute(
            """SELECT fixture_id, minute, goals_total
                 FROM ht_pressure_observations
                WHERE fixture_id = ANY(%s)""",
            (fixture_ids,),
        )
        tape: dict[int, list[tuple[int, int]]] = {}
        for fid, minute, goals in cur.fetchall():
            tape.setdefault(fid, []).append((minute, goals))

    api_cache: dict[int, tuple[int | None, int | None]] = {}
    settled = 0
    with conn.cursor() as cur:
        for r in pending:
            fid = r["fixture_id"]
            obs = tape.get(fid, [])
            last_minute = max((m for m, _ in obs), default=r["minute"])
            tape_goals = max((g for m, g in obs if m <= 45), default=0)

            if fid not in api_cache:
                api_cache[fid] = _first_half_goals_api(fid)
            api_goals, api_minute = api_cache[fid]

            if api_goals is not None:
                ht_goals, goal_minute, src = api_goals, api_minute, "api"
            elif tape_goals > 0:
                # The tape can prove a goal happened; it cannot prove one did not.
                scored = [m for m, g in obs if g > 0]
                ht_goals, goal_minute, src = tape_goals, (min(scored) if scored else None), "poll"
            elif last_minute >= TAPE_HT_MINUTE:
                ht_goals, goal_minute, src = 0, None, "poll"
            else:
                continue                      # not settleable yet, and not guessed

            won = ht_goals > 0
            cur.execute(
                """UPDATE ht_pressure_observations
                      SET ht_goals = %s, goal_before_ht = %s, goal_minute = %s,
                          goal_minute_source = %s, settled_at = now()
                    WHERE id = %s""",
                (ht_goals, won, goal_minute, src, r["id"]),
            )
            settled += 1

            # payout_units is GROSS by project convention: lost = 0,
            # won = stake * entry_odds. The odds are read back from the trade
            # rather than recomputed, which is what stops the NET-style bug that
            # had to be repaired across 38 rows in May from coming back.
            if r["paper_trade_id"]:
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
    conn.commit()
    return settled


def report(conn) -> None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT count(*) AS rows,
                      count(DISTINCT fixture_id) AS fixtures,
                      count(*) FILTER (WHERE has_stats) AS with_stats,
                      count(*) FILTER (WHERE opening_pressure IS NOT NULL) AS measured,
                      count(*) FILTER (WHERE best_ask IS NOT NULL) AS with_book,
                      count(*) FILTER (WHERE entered) AS entered,
                      count(*) FILTER (WHERE goal_before_ht IS NOT NULL) AS settled,
                      avg(opening_pressure) AS mean_opening
                 FROM ht_pressure_observations"""
        )
        s = cur.fetchone()
        print(f"\nrows={s['rows']}  fixtures={s['fixtures']}  with_stats={s['with_stats']}  "
              f"first15_measured={s['measured']}  with_book={s['with_book']}  "
              f"entries={s['entered']}  settled={s['settled']}")
        if s["mean_opening"] is not None:
            print(f"mean opening pressure = {s['mean_opening']:.1f} "
                  f"(MIN_PRESSURE is set to {MIN_PRESSURE} — refit it from this)")

        # Why entries are not happening matters as much as the entries: on this
        # market the likely answer is the book, not the signal.
        cur.execute(
            """SELECT skip_reason, count(*) AS n
                 FROM ht_pressure_observations
                WHERE skip_reason IS NOT NULL AND minute BETWEEN %s AND %s
                GROUP BY 1 ORDER BY 2 DESC LIMIT 10""",
            (ENTRY_MIN_MINUTE, ENTRY_MAX_MINUTE),
        )
        rows = cur.fetchall()
        if rows:
            print("\nwhy no entry, inside the entry window:")
            for r in rows:
                print(f"  {r['n']:6d}  {r['skip_reason']}")

        # The pre-registered primary test: does the opening measurement separate
        # at all? One row per fixture — polls of the same match are not
        # independent observations of the same coin.
        cur.execute(
            """SELECT width_bucket(opening_pressure, 0, 100, 5) AS b,
                      count(*) AS n,
                      avg(goal_before_ht::int)::float8 AS p,
                      min(opening_pressure) AS lo, max(opening_pressure) AS hi
                 FROM (SELECT DISTINCT ON (fixture_id)
                              fixture_id, opening_pressure, goal_before_ht
                         FROM ht_pressure_observations
                        WHERE opening_pressure IS NOT NULL
                          AND goal_before_ht IS NOT NULL
                        ORDER BY fixture_id, minute) f
                GROUP BY 1 ORDER BY 1"""
        )
        rows = cur.fetchall()
        if rows:
            print("\nP(goal before HT) by opening-pressure bucket, one row per fixture:")
            for r in rows:
                odds = f"  ({1 / r['p']:.2f})" if r["p"] else ""
                print(f"  {r['lo']:5.0f}-{r['hi']:5.0f}  n={r['n']:5d}  {r['p']:.3f}{odds}")

        # The same test on what obs_version 3 actually gates: the HIGHEST live
        # reading the fixture reached inside the entry window, which is what
        # decides whether it ever fires. One row per fixture for the same reason
        # as above.
        cur.execute(
            """SELECT width_bucket(peak, 0, 100, 5) AS b,
                      count(*) AS n,
                      avg(goal_before_ht::int)::float8 AS p,
                      min(peak) AS lo, max(peak) AS hi
                 FROM (SELECT fixture_id, max(pressure_now) AS peak,
                              bool_or(goal_before_ht) AS goal_before_ht
                         FROM ht_pressure_observations
                        WHERE pressure_now IS NOT NULL
                          AND goal_before_ht IS NOT NULL
                          AND minute BETWEEN %s AND %s
                        GROUP BY fixture_id) f
                GROUP BY 1 ORDER BY 1""",
            (ENTRY_MIN_MINUTE, ENTRY_MAX_MINUTE),
        )
        rows = cur.fetchall()
        if rows:
            print("\nP(goal before HT) by PEAK live reading in the entry window, "
                  "one row per fixture:")
            for r in rows:
                odds = f"  ({1 / r['p']:.2f})" if r["p"] else ""
                print(f"  {r['lo']:5.0f}-{r['hi']:5.0f}  n={r['n']:5d}  {r['p']:.3f}{odds}")

        # Entries split by which measurement fired them — the H-PRESSURE-LATE
        # comparison, as soon as there is anything in it.
        cur.execute(
            """SELECT pressure_source, count(*) AS n,
                      avg(minute)::float8 AS mean_minute,
                      avg(best_ask)::float8 AS mean_ask,
                      avg(goal_before_ht::int)::float8 AS hit
                 FROM ht_pressure_observations
                WHERE entered AND pressure_source IS NOT NULL
                GROUP BY 1 ORDER BY 1"""
        )
        rows = cur.fetchall()
        if rows:
            print("\nentries by measurement (H-PRESSURE-LATE):")
            for r in rows:
                hit = f"{r['hit']:.3f}" if r["hit"] is not None else "  -  "
                print(f"  {r['pressure_source']:8s} n={r['n']:4d}  mean minute "
                      f"{r['mean_minute']:4.1f}  mean ask {r['mean_ask']:.3f} "
                      f"({1 / r['mean_ask']:.2f})  hit {hit}")
            print("  (n >= 200 per bucket before reading anything into this)")

        cur.execute(
            """SELECT count(*) AS n,
                      count(*) FILTER (WHERE pt.result = 'won') AS won,
                      sum(pt.payout_units - pt.stake_units)::float8 AS pnl,
                      sum(pt.stake_units)::float8 AS staked
                 FROM ht_pressure_observations o
                 JOIN paper_trades pt ON pt.id = o.paper_trade_id
                WHERE pt.result IS NOT NULL"""
        )
        t = cur.fetchone()
        if t and t["n"]:
            yld = 100.0 * t["pnl"] / t["staked"] if t["staked"] else 0.0
            print(f"\npaper: n={t['n']} won={t['won']} P&L={t['pnl']:+.2f}u yield={yld:+.1f}%")
            print("  (verdict gate: n >= 200 AND yield CI clear of zero after the fee "
                  "AND the pressure arm beating the base arm)")


# ── standalone loop ──────────────────────────────────────────────────────────
# Production runs this arm inside pressure_agent's cycle. This loop exists so the
# agent can be exercised on its own — one poll, one decision, no second process
# competing for the same api-football budget.

def run(once: bool, dry_run: bool, interval: int) -> None:
    table = load_table()
    log.info(f"first-half baseline {table['built_at']} — {len(table['cells'])} cells")

    conn = None if dry_run else _conn()
    sid = None if dry_run else strategy_id(conn)
    tracker = LiveMatchTracker()
    state = HTState()
    pm_fixtures: list[dict] = []
    last_markets = 0.0

    while True:
        t0 = time.time()
        if t0 - last_markets > REFRESH_MARKETS_S or not pm_fixtures:
            pm_fixtures = _fetch_events()
            last_markets = t0
            log.info(f"PM universe -> {len(pm_fixtures)} football fixtures")

        signals = tracker.poll(priority=enrich_priority(tracker, pm_fixtures))
        rows = observe(signals, table, pm_fixtures, state, tracker.enrich_status)

        opened = 0
        if rows and conn is not None:
            opened = open_trades(conn, sid, rows)
            write(conn, rows)

        for r in rows:
            if r["entered"]:
                log.info(
                    f"  ENTER  {r['home'][:18]:18} 0-0 {r['away'][:18]:18} "
                    f"{r['minute']}'  HT O0.5 ask={r['best_ask']:.3f} "
                    f"({1 / r['best_ask']:.2f})  press={r['pressure_now'] or r['opening_pressure']:.0f}"
                    f"[{(r['pressure_source'] or 'opening')[:3]}]  "
                    f"#{r['paper_trade_id']}"
                )
        log.info(f"first-half fixtures={len(rows):3d} entered={opened:2d} "
                 f"{time.time() - t0:.1f}s")

        if once:
            break
        time.sleep(max(5, interval - (time.time() - t0)))

    if conn:
        conn.close()


def enrich_priority(tracker: LiveMatchTracker, pm_fixtures: list[dict]):
    """Rank fixtures for a paid /fixtures/statistics call, first-half first.

    Only used by the standalone loop; when both arms share a process the ranking
    lives in pressure_agent, which has to serve two decision windows at once.
    """
    from pressure_agent import _pm_listed

    def rank(fid: int, snap) -> float:
        if not _pm_listed(tracker, fid, pm_fixtures):
            return -1
        if FIRST15_MIN - 3 <= snap.minute <= ENTRY_MAX_MINUTE:
            return 200 - snap.minute
        if snap.minute < FIRST15_MIN:
            return 100 - snap.minute
        return -1

    return rank


def _conn():
    return psycopg2.connect(DATABASE_URL)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="First-half pressure agent — PM 1st Half Over 0.5 (paper only)")
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
