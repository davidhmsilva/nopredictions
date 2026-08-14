"""Tests for the live pressure agent's pricing and gating.

The trading logic is deliberately thin; what needs guarding is the arithmetic
that turns a pressure score into a fair value, and the gates that decide an
entry. Both are places where a silent sign error would produce plausible-looking
paper trades for months.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pressure_agent as pa  # noqa: E402
from live_tracker import PressureSignals  # noqa: E402


# ── the pressure multiplier ──────────────────────────────────────────────────

def test_neutral_pressure_is_the_base_table():
    """At PRESSURE_NEUTRAL the agent must reduce exactly to the base rate.

    This is the null the whole experiment is measured against — if k drifts off
    1.0 at neutral pressure, the 'base arm' recorded in every row stops being a
    control.
    """
    assert pa.pressure_factor(pa.PRESSURE_NEUTRAL) == pytest.approx(1.0)
    assert pa.apply_pressure(0.42, 1.0) == pytest.approx(0.42)


def test_pressure_is_monotone_and_bounded():
    ks = [pa.pressure_factor(p) for p in range(0, 101, 5)]
    assert ks == sorted(ks)
    assert min(ks) >= pa.K_MIN and max(ks) <= pa.K_MAX


def test_high_pressure_raises_fair_value_low_pressure_lowers_it():
    base = 0.40
    assert pa.apply_pressure(base, pa.pressure_factor(80)) > base
    assert pa.apply_pressure(base, pa.pressure_factor(10)) < base


def test_apply_pressure_stays_a_probability():
    """A rate multiplier can be large; the probability it produces cannot leave (0,1)."""
    for base in (0.01, 0.5, 0.95, 0.999):
        for k in (0.1, 1.0, 5.0, 50.0):
            out = pa.apply_pressure(base, k)
            assert 0.0 < out < 1.0


def test_apply_pressure_scales_the_rate_not_the_probability():
    """Doubling the rate must not double the probability — that is the whole
    reason the multiplier is applied in lambda space."""
    base = 0.50                       # lambda = ln 2
    doubled = pa.apply_pressure(base, 2.0)
    assert doubled == pytest.approx(0.75)      # 1 - exp(-2 ln2) = 0.75
    assert doubled < 2 * base


def test_degenerate_probabilities_pass_through():
    assert pa.apply_pressure(0.0, 1.5) == 0.0
    assert pa.apply_pressure(1.0, 1.5) == 1.0


# ── the pressure index ───────────────────────────────────────────────────────

def _sig(**kw) -> PressureSignals:
    base = dict(fixture_id=1, home="A", away="B", minute=70, score="1-0")
    base.update(kw)
    return PressureSignals(**base)


def test_no_stats_coverage_is_not_low_pressure():
    """The danger index of an uncovered fixture collapses to its possession term,
    which lands in the same range as a genuinely quiet match. If the two are ever
    conflated, every uncovered fixture becomes a data point saying low pressure
    preceded no goal — and there are more uncovered fixtures than covered ones."""
    uncovered = _sig(home_danger_index=5.0, away_danger_index=5.0, has_stats=False)
    assert not uncovered.has_stats
    quiet = _sig(home_danger_index=5.0, away_danger_index=5.0, has_stats=True)
    assert pa.pressure_index_of(uncovered) == pa.pressure_index_of(quiet)


def test_pressure_index_counts_both_ends():
    """An over line does not care which team scores, so one team pressing hard
    and one team dormant must not read the same as both dormant."""
    both_dormant = pa.pressure_index_of(_sig(home_danger_index=10, away_danger_index=10))
    one_pressing = pa.pressure_index_of(_sig(home_danger_index=70, away_danger_index=10))
    assert one_pressing > both_dormant


# ── PM <-> api-football fixture matching ─────────────────────────────────────

def test_matches_across_naming_conventions():
    pm = [{"title": "Manchester City vs. Liverpool"}]
    assert pa.match_pm_fixture(_sig(home="Manchester City", away="Liverpool"), pm) is pm[0]


def test_requires_both_teams_to_hit():
    """A one-sided match is how you end up pricing the wrong fixture's book."""
    pm = [{"title": "Manchester City vs. Arsenal"}]
    assert pa.match_pm_fixture(_sig(home="Manchester City", away="Liverpool"), pm) is None


def test_short_team_names_do_not_match_everything():
    """Words of 3 characters or fewer are skipped, so a team whose name is all
    short words must fail closed rather than match the first fixture."""
    pm = [{"title": "Manchester City vs. Liverpool"}]
    assert pa.match_pm_fixture(_sig(home="AZ", away="PSV"), pm) is None


# ── entry gates ──────────────────────────────────────────────────────────────

def _row(**kw) -> dict:
    row = {
        "edge_pressure_pp": 5.0, "pressure_index": 60.0, "has_window": True,
        "score_agrees": True, "goals_total": 1, "ladder_goals": 1,
    }
    row.update(kw)
    return row


def _book(ask=0.50, depth=500.0) -> dict:
    return {"best_ask": ask, "ask_depth_usd": depth}


def test_why_not_names_the_binding_gate():
    assert "edge" in pa._why_not(_row(edge_pressure_pp=0.5), _book(), _sig())
    assert "pressure" in pa._why_not(_row(pressure_index=10), _book(), _sig())
    assert "window" in pa._why_not(_row(has_window=False), _book(), _sig())
    assert "ask" in pa._why_not(_row(), _book(ask=0.95), _sig())
    assert "depth" in pa._why_not(_row(), _book(depth=5.0), _sig())
    assert "score" in pa._why_not(_row(score_agrees=False, ladder_goals=2), _book(), _sig())


def test_max_ask_is_below_the_broken_calibration_zone():
    """PM asks above 0.85 resolved at 0.66 on n=382. The gate has to sit at or
    below that, or the agent buys into the one price band we know is broken."""
    assert pa.MAX_ASK <= 0.85


def test_entry_window_stays_inside_the_baseline_grid():
    """A state off the fair-value grid returns no fair value at all, so the
    minute gates must not admit minutes the table cannot price."""
    import late_goals_table as lgt
    assert pa.MIN_MINUTE >= lgt.WIDE_MINUTES[0]
    assert pa.MAX_MINUTE <= lgt.WIDE_MINUTES[-1] + 3   # lookup snaps within 3'


# ── tracker lifecycle ────────────────────────────────────────────────────────

def test_finished_fixtures_stop_being_reported():
    """A fixture that drops off the live feed must stop producing signals.

    self.snapshots is never emptied, so returning every tracked fixture means a
    match that ended hours ago keeps being reported each cycle with its final
    snapshot — frozen minute, frozen score. The pressure agent writes one row
    per signal, so that would fill the observation table with copies of a match
    nobody is playing.
    """
    from live_tracker import LiveMatchTracker, StatSnapshot, MISSING_POLLS_BEFORE_DROP

    t = LiveMatchTracker()
    t.fixture_info[7] = {"home": "A", "away": "B", "league": "L", "fixture_id": 7}
    t.snapshots[7].append(StatSnapshot(minute=90, timestamp=0.0, home_shots_total=9))

    assert t.get_signals(7) is not None          # tracked, so still answerable
    for _ in range(MISSING_POLLS_BEFORE_DROP + 1):
        t._prune(live_now=set())
    assert 7 not in t.snapshots                  # gone after the grace period


def test_halftime_gap_does_not_lose_the_window_baseline():
    """api-football drops some fixtures at half time. Pruning on the first miss
    would discard the history the window deltas are computed against."""
    from live_tracker import LiveMatchTracker, StatSnapshot

    t = LiveMatchTracker()
    t.snapshots[7].append(StatSnapshot(minute=45, timestamp=0.0))
    t._prune(live_now=set())
    assert 7 in t.snapshots
    t._prune(live_now={7})                       # back on the feed
    assert t._missing[7] == 0
