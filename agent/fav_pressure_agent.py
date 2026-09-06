#!/usr/bin/env python3
"""
Favourite-pressure agent — back the pre-match favourite to be AHEAD AT HALF TIME,
but only once it has shown it is actually living up to the price.

THE RULE (as specified)
-----------------------
A fixture has a clear pre-match favourite. The match is still 0-0 after 15
minutes. Over those first 15 minutes the favourite has both pressed hard in
absolute terms AND out-pressed the underdog. Buy PM's "<Favourite> leading at
halftime?" (Yes), 1u, paper.

The second half of that sentence is the whole point, and it is what makes this
different from simply backing favourites: the price already contains the
favourite's strength, so a favourite doing exactly what its price expects is not
information. What might be information is the JOINT event — favourite, and
visibly on top, and still level. Two of those three are public and instant (the
price, the scoreline); the third arrives on a slower feed.

WHAT IS BEING BOUGHT
--------------------
"Leading at halftime" is not "wins the match". From 0-0 at 15' it resolves NO on
every draw at the break, which is the modal outcome — the empirical table puts a
half-time draw at 43-57% depending on the state. Fair value from 0-0 at 15' runs
2.11 (strong away favourite) to 4.26 (weak home favourite), so this is a
value-priced bet on a specific 30-minute window, not a favourite-backing engine.

FAVOURITE IDENTIFICATION — the part that must never be sloppy
-------------------------------------------------------------
Getting the side wrong here does not cost accuracy, it inverts the bet. So:

  * the favourite comes from PM's own pre-kickoff 1X2 ("Will <team> win…?" plus
    the draw market, de-vigged across all three). Pre-KICKOFF is a hard
    requirement, not a preference: at 0-0 those prices drift with the clock, and
    a favourite read off a drifted book is a different quantity from the one the
    fair-value table is bucketed on;
  * team names are resolved against API-FOOTBALL's home/away, never against PM's
    own title order, using the alias-aware scorer in fixture_match. One side must
    clear MIN_SIDE_SCORE and beat the other outright, or the fixture is recorded
    and skipped;
  * the same resolution is repeated independently for the "<team> leading at
    halftime?" market, so the market bought and the team measured have to agree.

Usage:
    python fav_pressure_agent.py --once            # one cycle (own tracker poll)
    python fav_pressure_agent.py --once --dry-run  # no DB writes, no trades
    python fav_pressure_agent.py --settle          # backfill outcomes
    python fav_pressure_agent.py --report          # what has been collected

Production drives it from pressure_agent.py, on the same poll as the other two
arms — one api-football live call and one stats budget per cycle.
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

import favourite_ht_table as fvt                                   # noqa: E402
from edge_engine import taker_fee_pp                               # noqa: E402
from fixture_match import MIN_SIDE_SCORE, team_score               # noqa: E402
from ht_pressure_agent import current_pressure, opening_pressure   # noqa: E402
from late_goals_observer import (                                  # noqa: E402
    _fetch_book,
    _fetch_events,
    infer_goals,
    ladder_consistent,
    ladder_of,
    pm_over25,
)
import af_budget
from live_tracker import LiveMatchTracker, PressureSignals          # noqa: E402
from pressure_agent import (                                        # noqa: E402
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
log = logging.getLogger("fav_pressure")

DATABASE_URL = os.getenv("DATABASE_URL")
STRATEGY_NAME = "Live Pressure Favourite HT"
OBS_VERSION = 3

CYCLE_S = 60
REFRESH_MARKETS_S = 300

# ── windows ──────────────────────────────────────────────────────────────────
# FIRST15_MAX is no longer the end of the measurement, only the point where it
# switches from "the match so far" to the rolling 15-minute window — see
# ht_pressure_agent.current_pressure, which both first-half arms share so that
# "pressing now" cannot come to mean two different things. The frozen 15-18'
# reading is still taken and still recorded; from obs_version 3 it is the
# CONTROL, not the gate.
FIRST15_MIN, FIRST15_MAX = 15, 18
OBSERVE_MIN_MINUTE = 10
OBSERVE_MAX_MINUTE = 46
ENTRY_MIN_MINUTE = 15
# RAISED 25 -> 40 on 2026-09-05, with the reading unfrozen. A favourite that only
# starts turning the screw at 28' is exactly the fixture the frozen version could
# not touch, and it is the cheaper end of the market: the table runs to 44' and
# fair value for a favourite leading at HT falls from ~0.29 at 15' to ~0.10 at
# 40', so the same bet is available at 3.5 instead of 2.2. ⚠️ Longer odds are not
# a better price — the ask is about equally rich at every minute out to 40
# (-3.5pp at 15-19', -1.8 at 25-29', -2.9 at 35-40', all CIs crossing zero, and
# -8.49pp across the whole book unfiltered). See db/040.
ENTRY_MAX_MINUTE = 40

# ── entry gates ──────────────────────────────────────────────────────────────
# A real favourite, not an arithmetic one. Under 0.50 in a three-way market the
# "favourite" is a coin flip with a draw attached, and the thesis is about a side
# the market genuinely expects to win.
MIN_FAV_PROB = 0.50

# Both thresholds are calibrated to FREQUENCY on 165 real fixtures that already
# carry a 15-18' stats row, recomputing this exact index WITH the xG
# renormalisation (see live_tracker.danger_index):
#   per-side danger     — p50 15, p75 24, p90 32, p95 40
#   |gap| between sides — p50 14, p75 23, p90 31
# MIN_FAV_PRESSURE LOWERED 30 -> 19 on 2026-08-20, by the user's decision and not
# by any fit. Measured percentiles are above; 19 sits at about p62 per side.
#
# ⚠️ At 19 this threshold barely binds any more and the dominance term decides
# almost everything: a side scoring 19 with a gap of 20 is arithmetically
# impossible — the underdog would need a negative index — so every entry still
# needs a side of at least 20, whatever this constant says. The pair moves the
# candidate rate from 19.3% of fixtures to 34.8%, which is real loosening, but it
# comes from retiring the side term rather than from a considered view of how
# dominant a favourite has to look. Dropping MIN_DOMINANCE to 14 (the gap's own
# median) would take it to 47.0%; that was not asked for and is left alone.
#
# obs_version 3 applies both numbers to the ROLLING reading as well, and the
# transfer was checked rather than assumed. Reconstructed offline from the
# cumulative stats already on this table (4,556 poll-rows, minutes 19-44, still
# 0-0, deltas against the row nearest minute-15):
#
#   per side  p25  6.2  p50 11.7  p75 19.6  p90 30.4     (19 clears 26.3%)
#   |gap|     p25  4.6  p50 10.0  p75 18.8  p90 28.8     (20 clears ~26%)
#
# — within a point or two of the opening-15 percentiles quoted above, and flat
# across the clock. So the pair keeps selecting about the top quartile on the new
# axis, which is what it was set to do. Nothing was refitted to an outcome.
MIN_FAV_PRESSURE = 19.0         # the favourite is pressing in absolute terms
MIN_DOMINANCE = 20.0            # ...and out-pressing the underdog (now the real gate)

MAX_ASK = 0.85                  # PM asks above 0.85 resolve far below their price
MIN_DEPTH_USD = 25.0            # half-time markets are thin; see ht_pressure_agent
# obs_version 2 (2026-08-31): a spread cap. The sibling arms settled on 6pp; this
# market breaks at 3pp and the number is NOT copied across. 4,654 rows / 635
# fixtures, minute 15-25, still 0-0, `real - ask` clustered by fixture:
#
#   spread  0-3pp    -1.83pp CI[-5.62,+1.96]      spread 10-20pp   -11.22pp
#   spread  3-6pp    -6.97pp CI[-11.27,-2.67]     spread 20pp+     -43.86pp
#   spread 6-10pp    -7.88pp CI[-13.64,-2.12]
#
# 0-3pp is the only bucket whose CI still contains zero, so it is the only one
# where the price is arguably fair. Depth is not ported (see ht_pressure_agent).
#
# ⚠️ This gate does not make the market cheap, and the number that matters is the
# one it cannot fix: across ALL 635 fixtures the ask sits 8.49pp above the
# realised rate, CI[-11.94,-5.04]. Even inside the tightest bucket it is -1.83pp.
# A "<Favourite> leading at halftime?" bought at the ask starts behind, and the
# dominance signal has to beat that before anything here is worth having.
MAX_SPREAD = 0.03
STAKE_UNITS = 1.0

# ── PM market shapes (verified live 2026-08-19 across 6 fixtures, 12/12 each) ──
#   "Will <team> win on <date>?"            — 1X2 leg, Yes/No
#   "Will <A> vs. <B> end in a draw?"       — the draw leg
#   "<team> leading at halftime?"           — what this agent buys, Yes/No
# Note the halftime market carries a BARE team name with no "A vs. B:" prefix,
# while the draw one is prefixed. Both shapes are handled.
_WIN_RE = re.compile(r"^will\s+(?P<team>.+?)\s+win(?:\s+on\s+[\d-]+)?\?$", re.I)
_DRAW_RE = re.compile(r"^will\s+.+\s+vs\.?\s+.+\s+end\s+in\s+a\s+draw\?$", re.I)
_LEAD_RE = re.compile(r"^(?P<team>.+?)\s+leading\s+at\s+half\s*-?\s*time\?$", re.I)


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _yes_price(mkt: dict) -> float | None:
    """Gamma's Yes price for a binary market. A mid, not an executable price."""
    try:
        outcomes = json.loads(mkt.get("outcomes") or "[]")
        prices = json.loads(mkt.get("outcomePrices") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    for i, name in enumerate(outcomes):
        if str(name).strip().lower() == "yes" and i < len(prices):
            return _f(prices[i])
    return None


def _yes_token(mkt: dict) -> str | None:
    try:
        outcomes = json.loads(mkt.get("outcomes") or "[]")
        tokens = json.loads(mkt.get("clobTokenIds") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    for i, name in enumerate(outcomes):
        if str(name).strip().lower() == "yes" and i < len(tokens) and tokens[i]:
            return tokens[i]
    return None


def resolve_side(name: str, home: str, away: str) -> str | None:
    """'home' / 'away' for a PM team name, or None when it is not unambiguous.

    Alias-aware scoring against API-FOOTBALL's names — the authority on which
    side is which. Ambiguity fails closed: on this market a side error does not
    degrade the bet, it inverts it.
    """
    h, a = team_score(name, home), team_score(name, away)
    if max(h, a) < MIN_SIDE_SCORE or h == a:
        return None
    return "home" if h > a else "away"


def parse_1x2_legs(fixture: dict) -> dict | None:
    """PM's three 1X2 legs as {team name -> Yes price} plus the draw.

    Deliberately does NOT resolve sides. This runs before kickoff, when the
    fixture is not on api-football's live feed yet and the only names available
    are PM's own — and PM's title order is not something this agent trusts.
    Sides are resolved later, against api-football, by favourite_from_legs.
    """
    legs: dict[str, float] = {}
    draw: float | None = None

    for mkt in fixture.get("markets") or []:
        if mkt.get("closed"):
            continue
        q = (mkt.get("question") or "").strip()
        rem = q.split(": ", 1)[1] if ": " in q else q
        if _DRAW_RE.match(rem) or _DRAW_RE.match(q):
            draw = _yes_price(mkt)
            continue
        m = _WIN_RE.match(rem) or _WIN_RE.match(q)
        if not m:
            continue
        price = _yes_price(mkt)
        if price is not None:
            legs[m.group("team").strip()] = price

    if draw is None or len(legs) != 2:
        return None
    return {"legs": legs, "draw": draw}


def favourite_from_legs(capture: dict, home: str, away: str) -> dict | None:
    """The favourite, from captured legs, de-vigged across all three.

    Returns None unless both team names resolve to OPPOSITE sides. A two-leg
    de-vig would inflate every probability by the missing leg's share and push
    fixtures into a stronger bucket than the market ever implied; two names
    resolving to the same side means the resolution is wrong, not that one side
    is doubly likely.
    """
    by_side: dict[str, float] = {}
    for name, price in capture["legs"].items():
        side = resolve_side(name, home, away)
        if side is None or side in by_side:
            return None
        by_side[side] = price

    if "home" not in by_side or "away" not in by_side:
        return None
    total = by_side["home"] + by_side["away"] + capture["draw"]
    if total <= 0:
        return None

    side = "home" if by_side["home"] >= by_side["away"] else "away"
    return {
        "side": side,
        "team": home if side == "home" else away,
        "p_fav": by_side[side] / total,
        "p_home_raw": by_side["home"],
        "p_away_raw": by_side["away"],
        "p_draw_raw": capture["draw"],
    }


def favourite_from_markets(fixture: dict, home: str, away: str) -> dict | None:
    """Parse and resolve in one step. Convenience for tests and one-off checks —
    the live path splits the two, because they happen hours apart."""
    capture = parse_1x2_legs(fixture)
    return None if capture is None else favourite_from_legs(capture, home, away)


def leading_at_ht_market(fixture: dict, home: str, away: str, side: str) -> dict | None:
    """The "<team> leading at halftime?" market for one side, Yes leg.

    The side is resolved from the market's own team name rather than assumed from
    PM's title order — that order is not something this agent ever trusts.
    """
    for mkt in fixture.get("markets") or []:
        if mkt.get("closed"):
            continue
        q = (mkt.get("question") or "").strip()
        rem = q.split(": ", 1)[1] if ": " in q else q
        m = _LEAD_RE.match(rem) or _LEAD_RE.match(q)
        if not m:
            continue
        if resolve_side(m.group("team").strip(), home, away) != side:
            continue
        token = _yes_token(mkt)
        if not token:
            continue
        return {"question": q, "token_id": token,
                "condition_id": mkt.get("conditionId"),
                "gamma_price": _yes_price(mkt)}
    return None


def load_table() -> dict:
    return fvt.load()


# ── cross-cycle state ────────────────────────────────────────────────────────

@dataclass
class FavState:
    """What has to survive between polls.

    opening — fixture_id -> the frozen 15-18' reading, per side.
    odds    — PM fixture title -> the 1X2 legs as first captured, with a flag for
              whether that capture happened before kickoff.
    """
    opening: dict[int, dict] = field(default_factory=dict)
    odds: dict[str, dict] = field(default_factory=dict)

    def prune(self, older_than_s: float = 8 * 3600) -> None:
        cutoff = time.time() - older_than_s
        for fid in [k for k, v in self.opening.items() if v["at"] < cutoff]:
            del self.opening[fid]
        for title in [k for k, v in self.odds.items() if v["at"] < cutoff]:
            del self.odds[title]


def capture_prematch_odds(state: FavState, pm_fixtures: list[dict]) -> int:
    """Record every listed fixture's 1X2 while it is still PRE-KICKOFF.

    This has to sweep the whole PM universe, not just the fixtures currently
    live: by the time a match reaches minute 15 its 1X2 has been drifting with
    the goalless clock for a quarter of an hour, and a favourite read off that
    book is not the pre-match favourite the fair-value table is bucketed on.
    _fetch_events lists a fixture from about four hours before kickoff, so the
    normal case is a capture made long before a ball is kicked. Costs nothing —
    the markets are already in the payload the other arms fetched.

    Returns how many fixtures were captured this cycle (for logging only).
    """
    now = datetime.now(timezone.utc)
    captured = 0
    for fx in pm_fixtures:
        title = fx.get("title")
        if not title or title in state.odds:
            continue
        legs = parse_1x2_legs(fx)
        if legs is None:
            continue
        kickoff = fx.get("kickoff")
        legs.update(prematch=bool(kickoff and now < kickoff), at=time.time())
        state.odds[title] = legs
        captured += 1
    return captured


# ── one cycle ────────────────────────────────────────────────────────────────

def observe(signals: dict[int, PressureSignals], table: dict,
            pm_fixtures: list[dict], state: FavState,
            enrich_status: dict[int, str] | None = None) -> list[dict]:
    state.prune()
    capture_prematch_odds(state, pm_fixtures)
    rows: list[dict] = []

    for sig in signals.values():
        if not (OBSERVE_MIN_MINUTE <= sig.minute <= OBSERVE_MAX_MINUTE):
            continue

        row = _base_row(sig)
        goals = sig.home_goals + sig.away_goals

        fx = match_pm_fixture(sig, pm_fixtures)
        if fx is None:
            row["skip_reason"] = "no PM fixture"
            rows.append(row)
            continue
        row["event_title"] = fx["title"]

        capture = state.odds.get(fx["title"])
        fav = favourite_from_legs(capture, sig.home, sig.away) if capture else None
        if fav is None:
            row["skip_reason"] = ("no PM 1X2 to read a favourite from" if not capture
                                  else "1X2 team names do not resolve to two sides")
            rows.append(row)
            continue
        fav["prematch"] = capture["prematch"]
        row.update(fav_side=fav["side"], fav_team=fav["team"],
                   fav_prob=fav["p_fav"], fav_is_prematch=fav["prematch"])

        # Pressure, per side. Two readings are taken: the frozen 15-18' one (the
        # control, unchanged) and the live one the gate now runs on — cumulative
        # to 18', the rolling 15-minute window after. A fixture picked up at 30'
        # with no window baseline gets neither, and is recorded and skipped.
        if sig.has_stats:
            _, home_danger, away_danger = opening_pressure(sig)
            row["home_danger"], row["away_danger"] = home_danger, away_danger
            row["has_xg"] = bool(sig.home_xg_total or sig.away_xg_total)
            row["has_window"] = sig.has_window
            now = current_pressure(sig)
            if now is not None:
                fav_now = now[1] if fav["side"] == "home" else now[2]
                dog_now = now[2] if fav["side"] == "home" else now[1]
                row.update(fav_pressure_now=fav_now, dog_pressure_now=dog_now,
                           dominance_now=fav_now - dog_now,
                           pressure_source=now[3],
                           pressure_minute=sig.stats_minute or sig.minute)
            if (FIRST15_MIN <= sig.minute <= FIRST15_MAX
                    and sig.fixture_id not in state.opening):
                state.opening[sig.fixture_id] = {
                    "home": home_danger, "away": away_danger,
                    "minute": sig.minute, "at": time.time(),
                }
        else:
            row["skip_reason"] = _no_stats_reason(
                (enrich_status or {}).get(sig.fixture_id, ""))

        opening = state.opening.get(sig.fixture_id)
        if opening:
            fav_d = opening[fav["side"]]
            dog_d = opening["away" if fav["side"] == "home" else "home"]
            row.update(opening_fav_pressure=fav_d, opening_dog_pressure=dog_d,
                       opening_dominance=fav_d - dog_d,
                       opening_minute=opening["minute"])

        cells = ladder_of(fx)
        row["pre_over25"] = pm_over25(cells)
        ladder = {ln: c["over_price"] for ln, c in cells.items()}
        if ladder and ladder_consistent(ladder):
            lower, upper, certain = infer_goals(ladder)
            if certain:
                row["ladder_goals"] = lower
                row["score_agrees"] = (lower == goals)

        mkt = leading_at_ht_market(fx, sig.home, sig.away, fav["side"])
        if not mkt:
            row["skip_reason"] = row["skip_reason"] or "no PM halftime-leader market"
            rows.append(row)
            continue
        row["condition_id"] = mkt["condition_id"]
        row["token_id"] = mkt["token_id"]

        book = _fetch_book(mkt)
        if not book:
            row["skip_reason"] = row["skip_reason"] or "no book"
            rows.append(row)
            continue
        row.update(best_bid=book["best_bid"], best_ask=book["best_ask"],
                   bid_depth_usd=book["bid_depth_usd"],
                   ask_depth_usd=book["ask_depth_usd"])

        if goals == 0:
            fair_base, fair_n = fvt.lookup(
                table, sig.minute, fav["side"] == "home",
                fav["p_fav"] if fav["prematch"] else None)
            if fair_base is None:
                row["skip_reason"] = row["skip_reason"] or f"minute {sig.minute} off the grid"
            else:
                # The treatment arm. apply_pressure scales a RATE, and "leads at
                # half time" is not a survival probability, so this is a
                # deliberately crude monotone transform — recorded as the
                # pressure arm of the comparison, never used to price anything.
                k = pressure_factor(row["fav_pressure_now"]
                                    or row["opening_fav_pressure"] or 0.0)
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
            and fav["prematch"]
            and fav["p_fav"] >= MIN_FAV_PROB
            and sig.has_stats
            and row["fav_pressure_now"] is not None
            and row["fav_pressure_now"] >= MIN_FAV_PRESSURE
            and row["dominance_now"] >= MIN_DOMINANCE
            and book["best_ask"] <= MAX_ASK
            and book["best_bid"] is not None
            and (book["best_ask"] - book["best_bid"]) <= MAX_SPREAD
            and (book["ask_depth_usd"] or 0) >= MIN_DEPTH_USD
            and row["score_agrees"] is not False
        )
        if not row["would_enter"] and not row["skip_reason"]:
            row["skip_reason"] = _why_not(row, book, sig, fav)
        rows.append(row)

    return rows


def _why_not(row: dict, book: dict, sig: PressureSignals, fav: dict) -> str:
    goals = sig.home_goals + sig.away_goals
    if goals:
        return f"not 0-0 ({sig.home_goals}-{sig.away_goals})"
    if sig.minute < ENTRY_MIN_MINUTE:
        return f"minute {sig.minute} < {ENTRY_MIN_MINUTE}"
    if sig.minute > ENTRY_MAX_MINUTE:
        return f"minute {sig.minute} > {ENTRY_MAX_MINUTE}"
    if not fav["prematch"]:
        return "favourite read in play, not before kickoff"
    if fav["p_fav"] < MIN_FAV_PROB:
        return f"no clear favourite (p={fav['p_fav']:.2f} < {MIN_FAV_PROB})"
    if row["fav_pressure_now"] is None:
        return (f"no {FIRST15_MIN}-minute window at {sig.minute}' "
                f"(fixture picked up late)")
    if row["fav_pressure_now"] < MIN_FAV_PRESSURE:
        return (f"favourite not pressing ({row['fav_pressure_now']:.0f} "
                f"< {MIN_FAV_PRESSURE})")
    if row["dominance_now"] < MIN_DOMINANCE:
        return (f"favourite not on top (dominance {row['dominance_now']:+.0f} "
                f"< {MIN_DOMINANCE})")
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
        "fav_side": None, "fav_team": None, "fav_prob": None,
        "fav_is_prematch": False,
        "home_xg": sig.home_xg_total, "away_xg": sig.away_xg_total,
        "home_shots_on": sig.home_shots_on_total, "away_shots_on": sig.away_shots_on_total,
        "home_shots_total": sig.home_shots_total, "away_shots_total": sig.away_shots_total,
        "home_shots_inside": sig.home_shots_inside_total,
        "away_shots_inside": sig.away_shots_inside_total,
        "home_corners": sig.home_corners_total, "away_corners": sig.away_corners_total,
        "home_possession": sig.home_possession, "away_possession": sig.away_possession,
        "home_reds": sig.home_reds, "away_reds": sig.away_reds,
        "has_stats": sig.has_stats, "has_xg": False,
        # ESPN, the free fallback, publishes no team xG and no shots inside the
        # box. Recorded so the two populations never pool in a fit.
        "stats_source": sig.stats_source, "has_inside": sig.has_inside,
        "home_danger": None, "away_danger": None,
        "opening_fav_pressure": None, "opening_dog_pressure": None,
        "opening_dominance": None, "opening_minute": None,   # frozen — CONTROL
        "fav_pressure_now": None, "dog_pressure_now": None,  # what the gate reads
        "dominance_now": None, "pressure_source": None,
        "pressure_minute": None, "has_window": False,
        "pressure_factor": None,
        "pre_over25": None,
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
    "goals_total", "ladder_goals", "score_agrees",
    "fav_side", "fav_team", "fav_prob", "fav_is_prematch",
    "home_xg", "away_xg", "home_shots_on", "away_shots_on",
    "home_shots_total", "away_shots_total", "home_shots_inside", "away_shots_inside",
    "home_corners", "away_corners", "home_possession", "away_possession",
    "home_reds", "away_reds", "has_stats", "has_xg",
    "stats_source", "has_inside",
    "home_danger", "away_danger", "opening_fav_pressure", "opening_dog_pressure",
    "opening_dominance", "opening_minute",
    "fav_pressure_now", "dog_pressure_now", "dominance_now",
    "pressure_source", "pressure_minute", "has_window",
    "pressure_factor", "pre_over25",
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
            f"INSERT INTO fav_ht_observations ({','.join(_COLS)}) VALUES %s",
            [[r.get(c) for c in _COLS] for r in rows],
        )
    conn.commit()


def strategy_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
        got = cur.fetchone()
    if not got:
        raise SystemExit(f"strategy '{STRATEGY_NAME}' missing — apply db/034")
    return got[0]


def open_trades(conn, sid: int, rows: list[dict]) -> int:
    opened = 0
    for r in rows:
        if not r["would_enter"]:
            continue
        with conn.cursor() as cur:
            cur.execute(
                # Matched on the TEAMS as well as the id. When api-football
                # goes down mid-match the tracker falls back to ESPN, which
                # namespaces the fixture id negative — so the same real match
                # arrives under a second id and an id-only guard lets it be
                # bought twice. `home`/`away` are whatever the source called
                # them, so this catches the common case (same source, same
                # names) rather than every case; the id check still carries the
                # rest.
                "SELECT 1 FROM fav_ht_observations "
                "WHERE (fixture_id = %s OR (home = %s AND away = %s)) "
                "  AND entered AND observed_at > now() - interval '6 hours' "
                "LIMIT 1",
                (r["fixture_id"], r["home"], r["away"]),
            )
            if cur.fetchone():
                r["would_enter"], r["skip_reason"] = False, "already entered this fixture"
                continue

            fair_txt = (
                f"base fair {r['fair_base']:.3f} ({1 / r['fair_base']:.2f}, "
                f"n={r['fair_n']}) -> pressure fair {r['fair_pressure']:.3f} "
                f"({1 / r['fair_pressure']:.2f}), i.e. {r['edge_pressure_pp']:+.1f}pp "
                f"after {r['fee_pp']:.2f}pp fee"
                if r["fair_base"] else "no fair value on the grid for this state"
            )
            span = (f"the first {r['pressure_minute']} minutes"
                    if r["pressure_source"] == "opening"
                    else f"the 15 minutes to {r['pressure_minute']}'")
            reasoning = (
                f"{r['home']} 0-0 {r['away']} {r['minute']}' — "
                f"{r['fav_team']} to lead at half time at {r['best_ask']:.3f} "
                f"({1 / r['best_ask']:.2f}). Pre-match favourite at "
                f"{r['fav_prob']:.0%} (de-vigged PM 1X2, captured before kickoff), "
                f"and living up to it: pressure "
                f"{r['fav_pressure_now']:.0f} vs {r['dog_pressure_now']:.0f} "
                f"for the underdog (dominance {r['dominance_now']:+.0f}), over "
                f"{span}. PREDICTION: the side the "
                f"market already rated, visibly on top and still level, is more "
                f"likely to be ahead at the break than the price pays for. "
                f"Bought on that call alone, not on a price comparison. For the "
                f"record, {fair_txt} — recorded as the null, NOT a gate. PAPER."
            )
            cur.execute(
                """INSERT INTO paper_trades
                     (strategy_id, outcome, entry_price, entry_odds, stake_units,
                      model_probability, expected_edge, reasoning, confidence,
                      pm_token_id, pm_live)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)
                   RETURNING id""",
                (sid,
                 f"{r['fav_team']} leading at halftime — "
                 f"{r['event_title'] or r['home'] + ' vs ' + r['away']}",
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

def _halftime_score(fixture_id: int) -> tuple[int, int] | None:
    """(home, away) at half time from api-football, or None.

    `score.halftime` is exactly the settlement quantity — no reconstruction from
    events, no assumption about which minute stoppage-time goals land in. One
    call per fixture per settle run.
    """
    key = os.getenv("FOOTBALL_API_KEY", "")
    if not key:
        return None
    try:
        af_budget.process_counter().record("fixture")
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"id": fixture_id},
            headers={"x-apisports-key": key},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body.get("errors"):
            return None
        for f in body.get("response", []):
            ht = (f.get("score") or {}).get("halftime") or {}
            h, a = ht.get("home"), ht.get("away")
            if h is None or a is None:
                return None
            # Only trust it once the half is actually over.
            status = ((f.get("fixture") or {}).get("status") or {}).get("short", "")
            if status in ("1H", "NS", "TBD", "PST", "CANC"):
                return None
            return int(h), int(a)
    except Exception:
        return None
    return None


TAPE_HT_MINUTE = 43


def settle(conn) -> int:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, fixture_id, minute, fav_side, paper_trade_id
                 FROM fav_ht_observations
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
            """SELECT fixture_id, minute, home_goals, away_goals
                 FROM fav_ht_observations WHERE fixture_id = ANY(%s)""",
            (fixture_ids,),
        )
        tape: dict[int, list[tuple[int, int, int]]] = {}
        for fid, minute, hg, ag in cur.fetchall():
            tape.setdefault(fid, []).append((minute, hg, ag))

    api_cache: dict[int, tuple[int, int] | None] = {}
    settled = 0
    with conn.cursor() as cur:
        for r in pending:
            fid = r["fixture_id"]
            if fid not in api_cache:
                api_cache[fid] = _halftime_score(fid)
            ht = api_cache[fid]
            src = "api"

            if ht is None:
                # Our own tape, and only once it actually reached the break —
                # a fixture we stopped watching at 30' is not a settled draw.
                obs = tape.get(fid, [])
                near_ht = [o for o in obs if o[0] >= TAPE_HT_MINUTE]
                if not near_ht:
                    continue
                last = max(near_ht, key=lambda o: o[0])
                ht, src = (last[1], last[2]), "poll"

            if r["fav_side"] is None:
                continue
            fav_goals, opp_goals = (ht[0], ht[1]) if r["fav_side"] == "home" else (ht[1], ht[0])
            won = fav_goals > opp_goals

            cur.execute(
                """UPDATE fav_ht_observations
                      SET ht_home_goals = %s, ht_away_goals = %s,
                          fav_led_at_ht = %s, ht_source = %s, settled_at = now()
                    WHERE id = %s""",
                (ht[0], ht[1], won, src, r["id"]),
            )
            settled += 1

            # payout_units is GROSS by project convention: lost = 0,
            # won = stake * entry_odds, read back off the trade.
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
                      count(*) FILTER (WHERE fav_team IS NOT NULL) AS with_fav,
                      count(*) FILTER (WHERE fav_is_prematch) AS fav_prematch,
                      count(*) FILTER (WHERE opening_fav_pressure IS NOT NULL) AS measured,
                      count(*) FILTER (WHERE best_ask IS NOT NULL) AS with_book,
                      count(*) FILTER (WHERE entered) AS entered,
                      count(*) FILTER (WHERE fav_led_at_ht IS NOT NULL) AS settled
                 FROM fav_ht_observations"""
        )
        s = cur.fetchone()
        print(f"\nrows={s['rows']}  fixtures={s['fixtures']}  with_favourite={s['with_fav']}  "
              f"(pre-kickoff {s['fav_prematch']})  first15_measured={s['measured']}  "
              f"with_book={s['with_book']}  entries={s['entered']}  settled={s['settled']}")

        cur.execute(
            """SELECT skip_reason, count(*) AS n
                 FROM fav_ht_observations
                WHERE skip_reason IS NOT NULL AND minute BETWEEN %s AND %s
                GROUP BY 1 ORDER BY 2 DESC LIMIT 10""",
            (ENTRY_MIN_MINUTE, ENTRY_MAX_MINUTE),
        )
        rows = cur.fetchall()
        if rows:
            print("\nwhy no entry, inside the entry window:")
            for r in rows:
                print(f"  {r['n']:6d}  {r['skip_reason']}")

        # The pre-registered primary test: does the favourite's dominance over
        # the opening separate the outcome? One row per fixture — repeated polls
        # of the same match are not independent.
        cur.execute(
            """SELECT width_bucket(opening_dominance, -40, 60, 5) AS b,
                      count(*) AS n,
                      avg(fav_led_at_ht::int)::float8 AS p,
                      min(opening_dominance) AS lo, max(opening_dominance) AS hi
                 FROM (SELECT DISTINCT ON (fixture_id)
                              fixture_id, opening_dominance, fav_led_at_ht
                         FROM fav_ht_observations
                        WHERE opening_dominance IS NOT NULL
                          AND fav_led_at_ht IS NOT NULL
                        ORDER BY fixture_id, minute) f
                GROUP BY 1 ORDER BY 1"""
        )
        rows = cur.fetchall()
        if rows:
            print("\nP(favourite led at HT) by opening dominance, one row per fixture:")
            for r in rows:
                odds = f"  ({1 / r['p']:.2f})" if r["p"] else ""
                print(f"  {r['lo']:+6.0f}..{r['hi']:+6.0f}  n={r['n']:5d}  {r['p']:.3f}{odds}")
            print("  (n >= 200 per bucket before reading anything into this)")

        # The same test on what obs_version 3 actually gates: the HIGHEST live
        # dominance the fixture reached inside the entry window, which is what
        # decides whether it ever fires.
        cur.execute(
            """SELECT width_bucket(peak, -40, 60, 5) AS b,
                      count(*) AS n,
                      avg(fav_led_at_ht::int)::float8 AS p,
                      min(peak) AS lo, max(peak) AS hi
                 FROM (SELECT fixture_id, max(dominance_now) AS peak,
                              bool_or(fav_led_at_ht) AS fav_led_at_ht
                         FROM fav_ht_observations
                        WHERE dominance_now IS NOT NULL
                          AND fav_led_at_ht IS NOT NULL
                          AND minute BETWEEN %s AND %s
                        GROUP BY fixture_id) f
                GROUP BY 1 ORDER BY 1""",
            (ENTRY_MIN_MINUTE, ENTRY_MAX_MINUTE),
        )
        rows = cur.fetchall()
        if rows:
            print("\nP(favourite led at HT) by PEAK live dominance in the entry "
                  "window, one row per fixture:")
            for r in rows:
                odds = f"  ({1 / r['p']:.2f})" if r["p"] else ""
                print(f"  {r['lo']:+6.0f}..{r['hi']:+6.0f}  n={r['n']:5d}  {r['p']:.3f}{odds}")

        # Entries split by which measurement fired them — H-PRESSURE-LATE.
        cur.execute(
            """SELECT pressure_source, count(*) AS n,
                      avg(minute)::float8 AS mean_minute,
                      avg(best_ask)::float8 AS mean_ask,
                      avg(fav_led_at_ht::int)::float8 AS hit
                 FROM fav_ht_observations
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

        cur.execute(
            """SELECT count(*) AS n,
                      count(*) FILTER (WHERE pt.result = 'won') AS won,
                      sum(pt.payout_units - pt.stake_units)::float8 AS pnl,
                      sum(pt.stake_units)::float8 AS staked
                 FROM fav_ht_observations o
                 JOIN paper_trades pt ON pt.id = o.paper_trade_id
                WHERE pt.result IS NOT NULL"""
        )
        t = cur.fetchone()
        if t and t["n"]:
            yld = 100.0 * t["pnl"] / t["staked"] if t["staked"] else 0.0
            print(f"\npaper: n={t['n']} won={t['won']} P&L={t['pnl']:+.2f}u yield={yld:+.1f}%")
            print("  (verdict gate: n >= 200 AND yield CI clear of zero after the fee "
                  "AND the dominance arm beating the plain-favourite arm)")


# ── standalone loop ──────────────────────────────────────────────────────────

def enrich_priority(tracker: LiveMatchTracker, pm_fixtures: list[dict]):
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


def run(once: bool, dry_run: bool, interval: int) -> None:
    table = load_table()
    log.info(f"favourite baseline {table['built_at']} — {len(table['cells'])} cells")

    conn = None if dry_run else _conn()
    sid = None if dry_run else strategy_id(conn)
    tracker = LiveMatchTracker()
    state = FavState()
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
                    f"  ENTER  {r['fav_team'][:22]:22} lead@HT {r['minute']}'  "
                    f"ask={r['best_ask']:.3f} ({1 / r['best_ask']:.2f})  "
                    f"fav={r['fav_prob']:.0%} press={r['fav_pressure_now']:.0f} "
                    f"dom={r['dominance_now']:+.0f}"
                    f"[{(r['pressure_source'] or 'opening')[:3]}]  #{r['paper_trade_id']}"
                )
        log.info(f"first-half fixtures={len(rows):3d} entered={opened:2d} "
                 f"{time.time() - t0:.1f}s")

        if once:
            break
        time.sleep(max(5, interval - (time.time() - t0)))

    if conn:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Favourite-pressure agent — PM halftime leader (paper only)")
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
