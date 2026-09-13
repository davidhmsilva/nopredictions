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

import fav_pressure_agent as fav  # noqa: E402
import first_half_table as fht  # noqa: E402
import ht_pressure_agent as ht  # noqa: E402
from live_tracker import PressureSignals  # noqa: E402


# ── settlement (2026-09-13) ──────────────────────────────────────────────────
# The outcome is score.halftime. It used to be the /fixtures/events goal count,
# which reads an EMPTY list — no event coverage, or an ESPN (negative) id — as
# "no goal": 11,426 rows over 278 fixtures disagreed with the half-time score.
# And never our own tape: the minute sits at 45 through the break and into the
# second half.

class _SettleCur:
    def __init__(self, conn):
        self.conn, self._rows = conn, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append((sql, params))
        if "SELECT id, fixture_id" in sql:
            self._rows = self.conn.pending
        elif "max(observed_at)" in sql:
            self._rows = self.conn.last_seen
        else:
            self._rows = []

    def fetchall(self):
        return self._rows


class _SettleConn:
    autocommit = True                   # db_txn.atomic reads it

    def __init__(self, pending, last_seen):
        self.pending, self.last_seen, self.log = pending, last_seen, []

    def cursor(self, cursor_factory=None):
        return _SettleCur(self)

    def commit(self):
        pass

    def writes(self, fragment):
        return [p for s, p in self.log if fragment in s]


def _pending(fid, trade=None):
    return {"id": 1, "fixture_id": fid, "minute": 20, "paper_trade_id": trade,
            "entered": trade is not None}


def _seen(**delta):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) - timedelta(**delta)


def test_an_empty_events_list_is_not_a_goalless_half(monkeypatch):
    monkeypatch.setattr(fav, "_halftime_scores", lambda ids: {7: (1, 0)})
    monkeypatch.setattr(ht, "_first_half_goals_api", lambda fid: (0, None))
    conn = _SettleConn([_pending(7)], [(7, _seen(minutes=50))])
    assert ht.settle(conn) == 1
    assert conn.writes("SET ht_goals") == [(1, True, None, "api", 1)]


def test_an_unanswered_fixture_waits_and_is_never_read_off_the_tape(monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(fav, "_halftime_scores", lambda ids: {})
    monkeypatch.setattr(ht, "_first_half_goals_api", lambda fid: called.append(fid) or (0, None))
    conn = _SettleConn([_pending(7, trade=42)], [(7, _seen(minutes=50))])
    assert ht.settle(conn) == 0
    assert conn.writes("UPDATE") == []
    assert called == []                 # no minute is asked without a goal


def test_an_espn_id_is_closed_without_an_outcome_past_the_horizon(monkeypatch):
    monkeypatch.setattr(fav, "_halftime_scores", lambda ids: {})
    conn = _SettleConn([_pending(-401841222)],
                       [(-401841222, _seen(hours=fav.SETTLE_API_MAX_AGE_H + 1))])
    assert ht.settle(conn) == 0
    assert conn.writes("'no_api'") == [(1,)]
    assert conn.writes("SET ht_goals") == []


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
    # opening_pressure is the frozen control from obs_version 3 on; pressure_now
    # is what the gate reads.
    row = {"opening_pressure": 60.0, "pressure_now": 60.0,
           "pressure_source": "opening", "score_agrees": True, "ladder_goals": 0}
    row.update(kw)
    return row


def _book(ask=0.55, depth=200.0, bid=None) -> dict:
    # best_bid has been part of the gate since obs_version 2 added MAX_SPREAD;
    # the default sits one tick inside it so a test that is about something
    # else is not silently blocked by the spread. The default ask moved 0.60 ->
    # 0.55 with obs_version 4 for the same reason: 0.60 is 1.67, under MIN_ODDS,
    # and would have made the price gate the answer to every question.
    return {"best_ask": ask, "best_bid": ask - 0.01 if bid is None else bid,
            "ask_depth_usd": depth}


def test_why_not_names_the_binding_gate():
    ok = _sig(minute=18)
    assert "0-0" in ht._why_not(_row(), _book(), _sig(home_goals=1, minute=18))
    assert "minute" in ht._why_not(_row(), _book(), _sig(minute=12))
    assert "minute" in ht._why_not(_row(), _book(), _sig(minute=44))
    assert "window" in ht._why_not(_row(pressure_now=None), _book(), ok)
    assert "pressure" in ht._why_not(_row(pressure_now=10.0), _book(), ok)
    assert "odds" in ht._why_not(_row(), _book(ask=0.95), ok)
    assert "depth" in ht._why_not(_row(), _book(depth=1.0), ok)
    # obs_version 4: the price gate names itself, in decimal odds, because that
    # is the unit the rule was given in.
    assert "1.75" in ht._why_not(_row(), _book(ask=0.62), ok)
    assert "score" in ht._why_not(_row(score_agrees=False, ladder_goals=1), _book(), ok)


def test_a_bad_price_is_not_what_blocks_a_trade():
    """This is a prediction, not a price comparison. If a negative EDGE ever
    starts blocking entries, the strategy has silently become a different one.

    obs_version 4's MIN_ODDS is a price gate, not an edge gate: it asks what the
    book quotes, never what our own fair value says about it. 0.57 is 1.754 —
    over the bar and comfortably worse than the ~0.55 fair value at this minute,
    which is exactly the combination this test exists to keep enterable."""
    assert ht._why_not(_row(), _book(ask=0.57), _sig(minute=17)) == ""


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
def quote():
    """The CLOB book the synthetic board serves — MUTABLE, so a test can move
    the price between polls. That is the entire obs_version 4 mechanism: the
    signal and the price arrive at different minutes.

    0.55 is 1.818, just clear of MIN_ODDS.
    """
    return {"best_bid": 0.53, "best_ask": 0.55,
            "bid_depth_usd": 300.0, "ask_depth_usd": 300.0}


@pytest.fixture
def board(monkeypatch, quote):
    monkeypatch.setattr(ht, "_fetch_book", lambda tok: {**tok, **quote})
    return [_pm_fixture_with_book()]


def _pressing(minute: int, **kw) -> PressureSignals:
    """A top-decile opening: one side camped in the other's box.

    Both danger indices are averaged, so a one-sided storm reads around 40 — well
    clear of MIN_PRESSURE, which the recorded distribution puts at the ~90th
    percentile of real openings."""
    return _sig(minute=minute, home_shots_on_total=4, home_shots_inside_total=5,
                home_xg_total=0.9, home_corners_total=3, home_possession=64.0,
                away_possession=36.0, **kw)


def _gone_quiet(minute: int, **kw) -> PressureSignals:
    """A real rolling reading of a match that has stopped happening. `has_window`
    matters: without it the reading is None, which is a different row and a
    different skip reason."""
    return _sig(minute=minute, has_window=True,
                home_shots_on_window=0, home_shots_inside_window=0,
                home_xg_window=0.0, home_corners_window=0,
                home_possession=51.0, away_possession=49.0,
                home_xg_total=0.1, **kw)


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
    to be what stops the trade, not the clock. Past 18' the live reading needs a
    rolling window, and this fixture has no baseline to take one against."""
    state = ht.HTState()
    rows = ht.observe({1: _pressing(22)}, fht.load(), board, state)
    assert rows[0]["opening_pressure"] is None
    assert rows[0]["pressure_now"] is None
    assert not rows[0]["would_enter"]
    assert "window" in rows[0]["skip_reason"]


def test_up_to_18_the_live_reading_is_the_opening_one(board):
    """The two versions have to be continuous where they overlap: an entry at
    15-18' under obs_version 3 must be the same number obs_version 2 traded."""
    state = ht.HTState()
    rows = ht.observe({1: _pressing(17)}, fht.load(), board, state)
    assert rows[0]["pressure_source"] == "opening"
    assert rows[0]["pressure_now"] == rows[0]["opening_pressure"]


def test_pressure_arriving_late_can_still_enter(board):
    """The whole point of obs_version 3. A quiet opening followed by a surge at
    30' was unenterable when the 15-18' reading was the gate."""
    state = ht.HTState()
    quiet = _sig(minute=16, home_possession=50.0, away_possession=50.0)
    ht.observe({1: quiet}, fht.load(), board, state)
    assert state.first15[1]["pressure"] < ht.MIN_PRESSURE

    surge = _sig(minute=30, has_window=True,
                 home_shots_on_window=3, home_shots_inside_window=4,
                 home_xg_window=0.7, home_corners_window=3,
                 home_possession=62.0, away_possession=38.0,
                 home_xg_total=0.7)
    rows = ht.observe({1: surge}, fht.load(), board, state)
    assert rows[0]["pressure_source"] == "window"
    assert rows[0]["pressure_now"] >= ht.MIN_PRESSURE
    assert rows[0]["opening_pressure"] < ht.MIN_PRESSURE   # the control disagrees
    assert rows[0]["would_enter"]


def test_a_dead_window_is_not_a_reading(board):
    """has_window is false when the baseline and the latest snapshot carry the
    same paid fetch. Differencing a stat block against itself reads as a dead
    match; scaling it reads as a surge. Neither is a reading, and neither may
    arm a fixture.

    The opening here is deliberately QUIET, so nothing armed earlier: this test
    is about the dead window being unable to open a position on its own. What an
    already-armed fixture may do is a different question, answered next."""
    state = ht.HTState()
    quiet = _sig(minute=16, home_possession=50.0, away_possession=50.0)
    ht.observe({1: quiet}, fht.load(), board, state)
    assert 1 not in state.armed

    rows = ht.observe({1: _pressing(30, has_window=False)}, fht.load(), board, state)
    assert rows[0]["pressure_now"] is None
    assert not rows[0]["would_enter"]


def test_an_armed_fixture_does_not_enter_without_a_live_reading(board, quote):
    """obs_version 5. This asserted the opposite until 2026-09-06.

    v4 entered on `(pressing or armed)`, so a fixture that pressed once could be
    bought minutes later purely because the price had walked out — with the game
    already quiet. Measured on v4's own 11 entries: 2 were bought with the
    reading COLLAPSED below the gate (Henan v Chengdu armed at 26' on 19.6, in
    at 34' on 12.7). The latch keeps a fixture under observation; it is not a
    reading.
    """
    state = ht.HTState()
    quote["best_ask"], quote["best_bid"] = 0.62, 0.60      # 1.61 — under the bar
    ht.observe({1: _pressing(16)}, fht.load(), board, state)
    assert state.armed[1]["minute"] == 16

    quote["best_ask"], quote["best_bid"] = 0.54, 0.52      # 1.85 — the price lands
    rows = ht.observe({1: _pressing(30, has_window=False)}, fht.load(), board, state)
    assert rows[0]["pressure_now"] is None                 # no live reading
    assert not rows[0]["would_enter"]
    # The arming is still on the record — the two rules have to stay comparable
    # on the same tape.
    assert rows[0]["entry_trigger"] == "armed"
    assert rows[0]["armed_at_minute"] == 16
    assert rows[0]["armed_pressure"] >= ht.MIN_PRESSURE


def test_an_armed_fixture_whose_pressure_died_says_so(board, quote):
    """The population v5 gives up, and it has to be countable in the histogram
    rather than folded into the generic pressure line."""
    state = ht.HTState()
    quote["best_ask"], quote["best_bid"] = 0.62, 0.60      # short: arms only
    ht.observe({1: _pressing(16)}, fht.load(), board, state)

    quote["best_ask"], quote["best_bid"] = 0.54, 0.52      # price arrives
    rows = ht.observe({1: _gone_quiet(30)}, fht.load(), board, state)
    assert not rows[0]["would_enter"]
    assert "pressure has gone" in rows[0]["skip_reason"]


# ── obs_version 4: the price gate and the monitoring state ───────────────────

def test_a_pressed_opening_at_a_short_price_arms_instead_of_entering(board, quote):
    """The user's rule: never take this line under 1.75. A fixture that presses
    at a shorter price has not failed — its price has not arrived."""
    state = ht.HTState()
    quote["best_ask"], quote["best_bid"] = 0.62, 0.60      # 1.61
    rows = ht.observe({1: _pressing(16)}, fht.load(), board, state)
    assert not rows[0]["would_enter"]
    assert rows[0]["armed_at_minute"] == 16
    assert rows[0]["armed_pressure"] >= ht.MIN_PRESSURE
    # entry_trigger describes the pressure state AT THIS POLL, not the fact of
    # entering: the match is still pressing while it waits for its price.
    assert rows[0]["entry_trigger"] == "live"
    assert "monitoring" in rows[0]["skip_reason"]
    assert "1.75" in rows[0]["skip_reason"]


def test_the_armed_fixture_enters_when_the_price_arrives(board, quote):
    state = ht.HTState()
    quote["best_ask"], quote["best_bid"] = 0.62, 0.60
    assert not ht.observe({1: _pressing(16)}, fht.load(), board, state)[0]["would_enter"]

    # 25' with the ask at 0.50: on real books 95% of clean quotes are past 1.75
    # by this minute, because the fair value has fallen — not because the market
    # got cheaper.
    quote["best_ask"], quote["best_bid"] = 0.50, 0.48
    rows = ht.observe({1: _pressing(25, has_window=True,
                                    home_shots_on_window=3,
                                    home_shots_inside_window=4,
                                    home_xg_window=0.7,
                                    home_corners_window=3)},
                      fht.load(), board, state)
    assert rows[0]["would_enter"], rows[0]["skip_reason"]
    assert rows[0]["entry_trigger"] == "live"          # still pressing as well
    assert rows[0]["armed_at_minute"] == 16            # but armed nine minutes ago


def test_only_the_first_arming_is_kept(board, quote):
    """The reading that made a fixture a candidate is the one a later entry has
    to be judged against. Overwriting it with whatever the index happened to say
    on the minute the price crossed would leave the record describing the price,
    not the signal."""
    state = ht.HTState()
    quote["best_ask"], quote["best_bid"] = 0.62, 0.60
    ht.observe({1: _pressing(16)}, fht.load(), board, state)
    first = state.armed[1]["pressure"]

    hotter = _sig(minute=17, home_shots_on_total=9, home_shots_inside_total=9,
                  home_xg_total=2.0, home_corners_total=6,
                  home_possession=70.0, away_possession=30.0)
    ht.observe({1: hotter}, fht.load(), board, state)
    assert state.armed[1]["pressure"] == first
    assert state.armed[1]["minute"] == 16


def test_a_long_price_alone_never_enters(board, quote):
    """MIN_ODDS is a second gate, not a substitute for the first. A quiet match
    at 3.00 is a quiet match."""
    state = ht.HTState()
    quote["best_ask"], quote["best_bid"] = 0.33, 0.31     # 3.03
    quiet = _sig(minute=30, home_possession=50.0, away_possession=50.0,
                 has_window=True)
    rows = ht.observe({1: quiet}, fht.load(), board, state)
    assert not rows[0]["would_enter"]
    assert rows[0]["entry_trigger"] is None
    assert 1 not in state.armed


def test_the_latch_expires_with_the_entry_window(board, quote):
    """An armed fixture that runs out of clock is the cost of the price gate,
    and the row has to say so — a plain 'minute > 40' would hide it inside the
    ordinary window misses."""
    state = ht.HTState()
    quote["best_ask"], quote["best_bid"] = 0.62, 0.60
    ht.observe({1: _pressing(16)}, fht.load(), board, state)

    quote["best_ask"], quote["best_bid"] = 0.20, 0.18
    rows = ht.observe({1: _pressing(44)}, fht.load(), board, state)
    assert not rows[0]["would_enter"]
    assert "armed at 16" in rows[0]["skip_reason"]


def test_a_goal_beats_the_latch(board, quote):
    """Arming is not a promise. The bet is defined by the match being 0-0."""
    state = ht.HTState()
    quote["best_ask"], quote["best_bid"] = 0.62, 0.60
    ht.observe({1: _pressing(16)}, fht.load(), board, state)

    quote["best_ask"], quote["best_bid"] = 0.50, 0.48
    rows = ht.observe({1: _pressing(25, home_goals=1)}, fht.load(), board, state)
    assert not rows[0]["would_enter"]
    assert "0-0" in rows[0]["skip_reason"]


def test_min_odds_and_max_entry_price_are_the_same_number(board):
    """1/0.5714 is 1.75008, not 1.75. A book quoting exactly the bar must not be
    refused by a rounding artifact."""
    assert 1.0 / ht.MAX_ENTRY_PRICE == pytest.approx(ht.MIN_ODDS)
    ok = _sig(minute=17)
    assert ht._why_not(_row(), _book(ask=ht.MAX_ENTRY_PRICE), ok) == ""


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
