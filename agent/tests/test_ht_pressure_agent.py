"""
Tests for the first-half pressure agent.

Two things here are load-bearing and would fail silently in production:

  1. Which PM market gets bought. The fixture carries "1st Half O/U 0.5" next to
     "1st Half O/U 3.5 Total Corners" and "<Team> 1st Half O/U 0.5", and a
     classifier that lets either through buys a completely different bet while
     recording it as this one. The full-match version of this bug (team totals
     read as match totals on short club names) really happened.
  2. When the opening measurement is taken. The signal is "the first 15 minutes",
     and a running average quietly standing in for it would mean the strategy is
     evaluated on a quantity it never traded.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_ht_pressure_agent.py -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import first_half_table as fht  # noqa: E402
import ht_pressure_agent as ht  # noqa: E402
from live_tracker import PressureSignals  # noqa: E402


# ── PM market discovery ──────────────────────────────────────────────────────

def _mkt(question: str, closed: bool = False, outcomes=("Over", "Under")) -> dict:
    return {
        "question": question,
        "closed": closed,
        "outcomes": json.dumps(list(outcomes)),
        "clobTokenIds": json.dumps(["tok-over", "tok-under"]),
        "outcomePrices": json.dumps(["0.62", "0.38"]),
        "conditionId": "cond-1",
    }


def _fixture(*questions: str) -> dict:
    return {"title": "CD Tolima vs. Independiente del Valle",
            "markets": [_mkt(q) for q in questions]}


def test_finds_the_first_half_over_05():
    fx = _fixture("CD Tolima vs. Independiente del Valle: 1st Half O/U 0.5")
    got = ht.ht_over05_market(fx)
    assert got and got["token_id"] == "tok-over"      # the OVER token, not Under


@pytest.mark.parametrize("question", [
    # A corner total. Same shape, completely different quantity.
    "CD Tolima vs. Independiente del Valle: 1st Half O/U 3.5 Total Corners",
    "CD Tolima vs. Independiente del Valle: 1st Half O/U 0.5 Total Corners",
    # A TEAM total: pays only if that side scores. This is the bug class that
    # bit the full-match classifier on short club names.
    "CD Tolima vs. Independiente del Valle: CD Tolima 1st Half O/U 0.5",
    # Wrong line, wrong half, wrong scope.
    "CD Tolima vs. Independiente del Valle: 1st Half O/U 1.5",
    "CD Tolima vs. Independiente del Valle: 2nd Half O/U 0.5",
    "CD Tolima vs. Independiente del Valle: O/U 0.5",
    "CD Tolima vs. Independiente del Valle: Both Teams to Score in First Half",
    "CD Tolima vs. Independiente del Valle: Draw at halftime?",
])
def test_rejects_every_neighbouring_market(question):
    assert ht.ht_over05_market(_fixture(question)) is None


def test_ignores_closed_markets():
    fx = {"title": "A vs. B",
          "markets": [_mkt("A vs. B: 1st Half O/U 0.5", closed=True)]}
    assert ht.ht_over05_market(fx) is None


def test_picks_the_right_market_out_of_a_full_board():
    fx = _fixture(
        "CD Tolima vs. Independiente del Valle: 1st Half O/U 3.5 Total Corners",
        "CD Tolima vs. Independiente del Valle: CD Tolima 1st Half O/U 0.5",
        "CD Tolima vs. Independiente del Valle: 1st Half O/U 0.5",
        "CD Tolima vs. Independiente del Valle: 1st Half O/U 1.5",
    )
    got = ht.ht_over05_market(fx)
    assert got and got["question"].endswith(": 1st Half O/U 0.5")


# ── the opening pressure measurement ─────────────────────────────────────────

def _sig(**kw) -> PressureSignals:
    base = dict(fixture_id=1, home="CD Tolima", away="Independiente del Valle",
                minute=15, score="0-0", has_stats=True)
    base.update(kw)
    return PressureSignals(**base)


def test_at_minute_15_the_scale_is_a_no_op():
    """At 15' the cumulative totals ARE the 15-minute window, so the index must
    equal what live_tracker's window version would return on the same numbers —
    that is what puts both agents' thresholds on one axis."""
    from live_tracker import danger_index

    sig = _sig(minute=15, home_shots_on_total=3, home_shots_inside_total=4,
               home_xg_total=0.6, home_corners_total=2, home_possession=60.0,
               away_possession=40.0)
    both, home, away = ht.opening_pressure(sig)
    assert home == pytest.approx(danger_index(3, 4, 0.6, 2, 60.0))
    assert both == pytest.approx((home + away) / 2)


def test_the_index_is_a_rate_not_a_total():
    """Twice the shots in twice the time is the same intensity. Without the
    scaling, every fixture would look more dangerous the longer it went on."""
    fast = _sig(minute=15, home_shots_on_total=3, home_xg_total=0.5)
    slow = _sig(minute=30, home_shots_on_total=6, home_xg_total=1.0)
    assert ht.opening_pressure(fast)[0] == pytest.approx(ht.opening_pressure(slow)[0])


def test_both_ends_count():
    """An over does not care who scores: one team camped in the box must not
    read the same as two teams doing nothing."""
    one_side = _sig(home_shots_on_total=5, home_xg_total=1.0)
    neither = _sig()
    assert ht.opening_pressure(one_side)[0] > ht.opening_pressure(neither)[0]


# ── entry gates ──────────────────────────────────────────────────────────────

def _row(**kw) -> dict:
    row = {"opening_pressure": 60.0, "score_agrees": True, "ladder_goals": 0}
    row.update(kw)
    return row


def _book(ask=0.60, depth=200.0) -> dict:
    return {"best_ask": ask, "ask_depth_usd": depth}


def test_why_not_names_the_binding_gate():
    ok = _sig(minute=18)
    assert "0-0" in ht._why_not(_row(), _book(), _sig(home_goals=1, minute=18))
    assert "minute" in ht._why_not(_row(), _book(), _sig(minute=12))
    assert "minute" in ht._why_not(_row(), _book(), _sig(minute=40))
    assert "first-15" in ht._why_not(_row(opening_pressure=None), _book(), ok)
    assert "pressure" in ht._why_not(_row(opening_pressure=10.0), _book(), ok)
    assert "ask" in ht._why_not(_row(), _book(ask=0.95), ok)
    assert "depth" in ht._why_not(_row(), _book(depth=1.0), ok)
    assert "score" in ht._why_not(_row(score_agrees=False, ladder_goals=1), _book(), ok)


def test_a_bad_price_is_not_what_blocks_a_trade():
    """This is a prediction, not a price comparison. If a negative edge ever
    starts blocking entries, the strategy has silently become a different one."""
    assert ht._why_not(_row(), _book(ask=0.84), _sig(minute=17)) == ""


def test_the_threshold_is_reachable():
    """MIN_PRESSURE at the sibling's 45 would fire on 0.6% of measured openings —
    about once a month. A threshold no real match clears is not a strategy."""
    assert ht.MIN_PRESSURE <= 30.0


def test_max_ask_is_below_the_broken_calibration_zone():
    """PM asks above 0.85 resolved at 0.66 on n=382."""
    assert ht.MAX_ASK <= 0.85


def test_entry_window_is_inside_the_fair_value_grid():
    """A minute the table cannot price returns no fair value at all, so the
    entry window must not admit one."""
    table = fht.load()
    for minute in (ht.ENTRY_MIN_MINUTE, ht.ENTRY_MAX_MINUTE):
        p, n = fht.lookup(table, minute, None)
        assert p is not None and n > 0


# ── the fair-value table ─────────────────────────────────────────────────────

def test_table_is_monotone_in_the_clock_and_in_the_bucket():
    """Less time left cannot mean more goals, and a match the market expected
    goals from cannot be less likely to produce one from the same state."""
    table = fht.load()
    pooled = [fht.lookup(table, m, None)[0] for m in range(15, 41)]
    assert pooled == sorted(pooled, reverse=True)
    # 0.30 sits in the 'lo' bucket, 0.65 in 'hi' (see late_goals_table.BUCKETS)
    assert fht.lookup(table, 20, 0.30)[0] < fht.lookup(table, 20, 0.65)[0]


def test_off_grid_states_return_nothing_rather_than_a_guess():
    table = fht.load()
    assert fht.lookup(table, 60, None) == (None, 0)
    assert fht.lookup(table, 0, None) == (None, 0)


# ── observe(), end to end on a synthetic board ───────────────────────────────

def _pm_fixture_with_book() -> dict:
    fx = _fixture("CD Tolima vs. Independiente del Valle: 1st Half O/U 0.5")
    # A goalless full-match ladder, so the Gamma cross-check agrees with 0-0.
    fx["markets"] += [_mkt("CD Tolima vs. Independiente del Valle: O/U 0.5"),
                      _mkt("CD Tolima vs. Independiente del Valle: O/U 1.5")]
    for m in fx["markets"][1:]:
        m["outcomePrices"] = json.dumps(["0.40", "0.60"])
    return fx


@pytest.fixture
def board(monkeypatch):
    monkeypatch.setattr(ht, "_fetch_book", lambda tok: {
        **tok, "best_bid": 0.58, "best_ask": 0.60,
        "bid_depth_usd": 300.0, "ask_depth_usd": 300.0})
    return [_pm_fixture_with_book()]


def _pressing(minute: int, **kw) -> PressureSignals:
    """A top-decile opening: one side camped in the other's box.

    Both danger indices are averaged, so a one-sided storm reads around 40 — well
    clear of MIN_PRESSURE, which the recorded distribution puts at the ~90th
    percentile of real openings."""
    return _sig(minute=minute, home_shots_on_total=4, home_shots_inside_total=5,
                home_xg_total=0.9, home_corners_total=3, home_possession=64.0,
                away_possession=36.0, **kw)


def test_a_pressed_goalless_opening_enters(board):
    state = ht.HTState()
    rows = ht.observe({1: _pressing(16)}, fht.load(), board, state)
    assert len(rows) == 1
    assert rows[0]["would_enter"], rows[0]["skip_reason"]
    assert rows[0]["opening_minute"] == 16
    # Both arms of the null are recorded even though neither gates the entry.
    assert rows[0]["fair_base"] and rows[0]["fair_pressure"]
    assert rows[0]["edge_base_pp"] is not None


def test_the_opening_measurement_is_frozen_not_recomputed(board):
    """The signal is the first 15 minutes. Once taken it must not drift with
    play, or a fixture that opened quietly and woke up at 24' would be recorded
    as a high-pressure opening it never had."""
    state = ht.HTState()
    ht.observe({1: _pressing(16)}, fht.load(), board, state)
    first = state.first15[1]["pressure"]

    quiet_later = _sig(minute=24, home_possession=50.0, away_possession=50.0)
    rows = ht.observe({1: quiet_later}, fht.load(), board, state)
    assert rows[0]["opening_pressure"] == first          # frozen
    assert rows[0]["pressure_index"] < first             # the live one did move


def test_a_fixture_picked_up_late_never_gets_an_opening_reading(board):
    """No back-filling from a longer average — the reading does not exist, and a
    row saying so is worth more than a plausible number that was never measured.

    Minute 22 is inside the entry window on purpose: the missing measurement has
    to be what stops the trade, not the clock."""
    state = ht.HTState()
    rows = ht.observe({1: _pressing(22)}, fht.load(), board, state)
    assert rows[0]["opening_pressure"] is None
    assert not rows[0]["would_enter"]
    assert "first-15" in rows[0]["skip_reason"]


def test_a_goal_ends_it(board):
    state = ht.HTState()
    ht.observe({1: _pressing(16)}, fht.load(), board, state)
    rows = ht.observe({1: _pressing(20, home_goals=1)}, fht.load(), board, state)
    assert not rows[0]["would_enter"]
    assert rows[0]["fair_base"] is None          # the table is conditional on 0-0
    assert "0-0" in rows[0]["skip_reason"]


def test_no_stats_coverage_never_enters(board):
    """Zero stats produce a low index that looks exactly like a quiet match.
    Trading it would enter the record as evidence about pressure it never had."""
    state = ht.HTState()
    rows = ht.observe({1: _sig(minute=16, has_stats=False)}, fht.load(), board, state)
    assert not rows[0]["would_enter"]
    assert rows[0]["opening_pressure"] is None


def test_only_first_half_fixtures_are_recorded(board):
    state = ht.HTState()
    assert ht.observe({1: _pressing(70)}, fht.load(), board, state) == []


def test_a_live_price_is_not_used_as_the_pre_match_bucket(board):
    """The bucket is the PRE-MATCH total. A price first seen at 16' has already
    drifted down with the goalless clock; using it would push the fixture into
    the 'lo' bucket and lower our own fair value, manufacturing a reason not to
    trade out of nothing."""
    state = ht.HTState()
    rows = ht.observe({1: _pressing(16)}, fht.load(), board, state)
    assert rows[0]["pre_is_prematch"] is False
    pooled, _ = fht.lookup(fht.load(), 16, None)
    assert rows[0]["fair_base"] == pytest.approx(pooled)
