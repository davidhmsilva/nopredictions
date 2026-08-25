"""Tests for the live pressure agent's pricing and gating.

The trading logic is deliberately thin; what needs guarding is the arithmetic
that turns a pressure score into a fair value, and the gates that decide an
entry. Both are places where a silent sign error would produce plausible-looking
paper trades for months.
"""

import os
import sys

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pressure_agent as pa  # noqa: E402
import live_tracker as lt  # noqa: E402
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
    late = _sig(minute=80)
    # v2: the minute is a gate in its own right, and the edge no longer is.
    assert "minute" in pa._why_not(_row(), _book(), _sig(minute=70))
    assert "pressure" in pa._why_not(_row(pressure_index=10), _book(), late)
    assert "window" in pa._why_not(_row(has_window=False), _book(), late)
    assert "ask" in pa._why_not(_row(), _book(ask=0.95), late)
    assert "depth" in pa._why_not(_row(), _book(depth=5.0), late)
    assert "score" in pa._why_not(_row(score_agrees=False, ladder_goals=2), _book(), late)


def test_edge_no_longer_blocks_an_entry():
    """v2 buys on the pressure call alone. A deeply negative edge must not be
    what stops a trade — if it does, the strategy silently reverted to v1."""
    assert pa._why_not(_row(edge_pressure_pp=-30.0), _book(), _sig(minute=80)) == ""


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


# ── observe(), end to end on a synthetic board ───────────────────────────────
# This exists because the arm has been refactored underneath: when the poll moved
# out of observe() so the first-half arm could share it, three references to the
# tracker stayed behind, and every gate test above still passed. The daemon
# crash-looped instead. A single call through the real path catches that class.

def _board(monkeypatch):
    import json
    def mkt(question, prices):
        return {"question": question, "closed": False,
                "outcomes": json.dumps(["Over", "Under"]),
                "clobTokenIds": json.dumps(["tok-over", "tok-under"]),
                "outcomePrices": json.dumps(prices), "conditionId": "cond"}
    fx = {"title": "Manchester City vs. Liverpool", "kickoff": None, "markets": [
        mkt("Manchester City vs. Liverpool: O/U 0.5", ["0.9995", "0.0005"]),
        mkt("Manchester City vs. Liverpool: O/U 1.5", ["0.44", "0.56"]),
        mkt("Manchester City vs. Liverpool: O/U 2.5", ["0.18", "0.82"]),
    ]}
    monkeypatch.setattr(pa, "_fetch_book", lambda tok: {
        **tok, "best_bid": 0.42, "best_ask": 0.44,
        "bid_depth_usd": 500.0, "ask_depth_usd": 500.0})
    return [fx]


def test_observe_runs_end_to_end_and_enters(monkeypatch):
    import late_goals_table as lgt

    board = _board(monkeypatch)
    sig = _sig(fixture_id=1, home="Manchester City", away="Liverpool",
               minute=80, home_goals=1, away_goals=0, has_stats=True,
               has_window=True, home_danger_index=70.0, away_danger_index=40.0)
    rows = pa.observe({1: sig}, lgt.load(lgt.WIDE_TABLE_PATH), board, {})

    assert len(rows) == 1
    assert rows[0]["target_line"] == 1.5          # over (current total + 0.5)
    assert rows[0]["fair_base"] and rows[0]["fair_pressure"]
    assert rows[0]["would_enter"], rows[0]["skip_reason"]


def test_observe_records_why_the_stats_are_missing(monkeypatch):
    """"No coverage" and "we never asked" are different facts, and conflating
    them is what put 36,917 mislabelled rows in the table on 2026-08-15."""
    import late_goals_table as lgt

    board = _board(monkeypatch)
    sig = _sig(fixture_id=1, home="Manchester City", away="Liverpool",
               minute=80, has_stats=False)
    rows = pa.observe({1: sig}, lgt.load(lgt.WIDE_TABLE_PATH), board, {},
                      enrich_status={1: lt.DAILY_EXHAUSTED})
    assert "daily quota exhausted" in rows[0]["skip_reason"]
    assert rows[0]["pressure_index"] is None      # never a made-up measurement


def test_rate_limited_is_not_recorded_as_a_spent_day():
    """One label for both refusals pointed at the wrong constraint: the daily
    allowance was 3% used while 176 rows claimed it was gone."""
    assert pa._no_stats_reason(lt.RATE_LIMITED) == \
        "stats unavailable: rate limited (per-minute)"
    assert pa._no_stats_reason(lt.DAILY_EXHAUSTED) == \
        "stats unavailable: daily quota exhausted"
    assert pa._no_stats_reason(lt.RATE_LIMITED) != pa._no_stats_reason(lt.DAILY_EXHAUSTED)


# ── the fair value is a property of the STATE, not of the book ───────────────
# fair_base used to be computed only after a PM fixture matched, a target line
# existed and the CLOB answered — so it landed on 9,262 of 153,962 rows. It is
# the null the pressure term is judged against, and the residual test ("does
# pressure know anything the empirical table did not already know") has to run
# on every measured row, not on the subset that happened to be listed and
# quoted.

def test_state_is_priced_even_with_no_pm_market(monkeypatch):
    import late_goals_table as lgt

    sig = _sig(fixture_id=1, home="Some", away="Team", minute=80,
               home_goals=1, away_goals=0, has_stats=True, has_window=True,
               home_danger_index=70.0, away_danger_index=40.0)
    rows = pa.observe({1: sig}, lgt.load(lgt.WIDE_TABLE_PATH), [], {})

    assert rows[0]["skip_reason"] == "no PM fixture"
    assert rows[0]["fair_base"] is not None      # the state alone prices it
    assert rows[0]["fair_pressure"] is not None
    assert rows[0]["pressure_factor"] is not None
    # No book, so no edge — an edge needs a price and there is none.
    assert rows[0]["edge_base_pp"] is None
    assert rows[0]["best_ask"] is None


def test_off_grid_state_still_records_no_fair_value(monkeypatch):
    """A minute outside the baseline grid must stay unpriced rather than snap
    to the nearest cell — the lookup already refuses beyond 3 minutes."""
    import late_goals_table as lgt

    sig = _sig(fixture_id=1, home="Some", away="Team", minute=10,
               has_stats=True, has_window=True,
               home_danger_index=70.0, away_danger_index=40.0)
    rows = pa.observe({1: sig}, lgt.load(lgt.WIDE_TABLE_PATH), [], {})
    assert rows[0]["fair_base"] is None


# ── xG availability is a confound, so it has to be a column ─────────────────

def test_has_xg_tells_no_xg_apart_from_never_measured():
    """xG is 40% of the index and api-football serves it on about half the
    fixtures. A match without it reads ~15 where a covered one reads ~30, so
    with MIN_PRESSURE at 45 it cannot enter — that has to be visible in the fit,
    not silently baked into two rows that look identical."""
    measured_with = pa._base_row(_sig(has_stats=True, home_xg_total=0.7), 15)
    measured_without = pa._base_row(_sig(has_stats=True), 15)
    never_measured = pa._base_row(_sig(has_stats=False), 15)

    assert measured_with["has_xg"] is True
    assert measured_without["has_xg"] is False
    assert never_measured["has_xg"] is None      # not False — we never looked


# ── the centre moved; the slope must not have ───────────────────────────────

def test_recentring_did_not_steepen_the_response():
    """k = 1 + GAIN*(p - NEUTRAL)/NEUTRAL, so the response per point of pressure
    is GAIN/NEUTRAL. Moving NEUTRAL from the guessed 35 to the measured 22
    without moving GAIN would have steepened that 59% — a change to how hard
    pressure bites, disguised as a fix to where it is centred."""
    slope = pa.PRESSURE_GAIN / pa.PRESSURE_NEUTRAL
    assert slope == pytest.approx(0.50 / 35.0, rel=0.02)


# ── surviving a connection dropped under us ──────────────────────────────────

class _FakeCursor:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a):
        if self.exc:
            raise self.exc


class _FakeConn:
    """Just enough connection to drive the health check."""

    def __init__(self, exc=None, closed=0):
        self.exc = exc
        self.closed = closed
        self.close_calls = 0

    def cursor(self):
        return _FakeCursor(self.exc)

    def close(self):
        self.close_calls += 1


def test_db_alive_reports_a_dropped_socket():
    """A sleep kills the socket; the agent must notice before it writes.

    psycopg2 raises OperationalError or InterfaceError depending on where the
    connection died, and the daemon used to die with it — four times on
    2026-08-21, each restart costing hours because the Mac was asleep.
    """
    assert pa._db_alive(_FakeConn()) is True
    assert pa._db_alive(None) is False
    assert pa._db_alive(_FakeConn(closed=1)) is False
    for exc in (psycopg2.OperationalError("server closed the connection"),
                psycopg2.InterfaceError("connection already closed")):
        assert pa._db_alive(_FakeConn(exc=exc)) is False
        assert isinstance(exc, pa._DB_DROPPED)


def test_reconnect_closes_the_corpse_and_returns_a_new_connection(monkeypatch):
    fresh = _FakeConn()
    dead = _FakeConn(exc=psycopg2.OperationalError("gone"))
    monkeypatch.setattr(pa, "_conn", lambda: fresh)
    assert pa._reconnect(dead) is fresh
    assert dead.close_calls == 1


def test_reconnect_returns_none_when_the_db_is_still_unreachable(monkeypatch):
    """No network yet after the wake — skip the cycle, never crash the loop."""
    def boom():
        raise psycopg2.OperationalError("could not translate host name")

    monkeypatch.setattr(pa, "_conn", boom)
    assert pa._reconnect(_FakeConn()) is None


def test_a_transport_fault_that_never_clears_ends_the_process(monkeypatch):
    """The forever-loop caught every network error and carried on, so on
    2026-08-25 the agent spent seven and a half hours reporting an empty live
    board while its sockets were dead. Only an EXIT reaches the wrapper's
    restart, so a fault that persists has to become one.
    """
    import live_tracker as lt

    polls = []

    class DeadTracker:
        window_minutes = 15
        enrich_status: dict = {}
        last_poll_failed = True

        def poll(self, priority=None):
            polls.append(1)
            # A cap, so that removing the watchdog fails this test instead of
            # hanging the suite forever.
            assert len(polls) <= pa.DEAD_POLLS_BEFORE_EXIT, "the loop never gave up"
            return {}

    monkeypatch.setattr(pa, "LiveMatchTracker", DeadTracker)
    monkeypatch.setattr(pa, "_fetch_events", lambda *a, **k: [])
    monkeypatch.setattr(pa.time, "sleep", lambda *a, **k: None)

    pa.run(once=False, dry_run=True, interval=0)

    assert len(polls) == pa.DEAD_POLLS_BEFORE_EXIT, (
        f"gave up after {len(polls)} polls, expected {pa.DEAD_POLLS_BEFORE_EXIT}")


def test_a_recovering_connection_does_not_end_the_process(monkeypatch):
    """The counter is CONSECUTIVE failures. A blip must not accumulate towards a
    restart across an otherwise healthy afternoon."""
    import itertools

    outcomes = itertools.cycle([True] * (pa.DEAD_POLLS_BEFORE_EXIT - 1) + [False])
    polls = []

    class FlakyTracker:
        window_minutes = 15
        enrich_status: dict = {}
        last_poll_failed = False

        def poll(self, priority=None):
            polls.append(1)
            self.last_poll_failed = next(outcomes)
            if len(polls) > pa.DEAD_POLLS_BEFORE_EXIT * 4:
                raise SystemExit  # survived far past the threshold — that is the point
            return {}

    monkeypatch.setattr(pa, "LiveMatchTracker", FlakyTracker)
    monkeypatch.setattr(pa, "_fetch_events", lambda *a, **k: [])
    monkeypatch.setattr(pa.time, "sleep", lambda *a, **k: None)

    with pytest.raises(SystemExit):
        pa.run(once=False, dry_run=True, interval=0)


def test_a_frozen_window_is_recorded_as_unmeasured_not_as_a_dead_match(monkeypatch):
    """The row the 2026-08-20 defect produced 17,390 times: a stat block, a
    baseline carrying the same fetch, every delta zero, and a danger index of
    exactly 5.0 that read as a measurement of a quiet game. It must carry no
    pressure at all, and must say why."""
    import late_goals_table as lgt

    board = _board(monkeypatch)
    sig = _sig(fixture_id=1, home="Manchester City", away="Liverpool", minute=80,
               has_stats=True, stats_frozen=True, has_window=False,
               home_danger_index=5.0, away_danger_index=5.0)
    row = pa.observe({1: sig}, lgt.load(lgt.WIDE_TABLE_PATH), board, {},
                     enrich_status={})[0]

    assert row["pressure_index"] is None
    assert row["home_danger"] is None and row["away_danger"] is None
    assert row["stats_frozen"] is True
    assert row["skip_reason"] == "stats frozen: window baseline is the same fetch"


def test_stats_frozen_is_null_when_there_was_no_reading_to_judge(monkeypatch):
    """NULL and False are different facts — the distinction db/035 had to be
    written to restore once already."""
    import late_goals_table as lgt

    board = _board(monkeypatch)
    sig = _sig(fixture_id=1, home="Manchester City", away="Liverpool",
               minute=80, has_stats=False)
    row = pa.observe({1: sig}, lgt.load(lgt.WIDE_TABLE_PATH), board, {},
                     enrich_status={})[0]

    assert row["stats_frozen"] is None
    assert row["pressure_index"] is None


def test_every_written_column_exists_on_the_row(monkeypatch):
    """_COLS drives the INSERT by name; a column added to one and not the other
    writes NULL silently for as long as nobody looks."""
    import late_goals_table as lgt

    board = _board(monkeypatch)
    sig = _sig(fixture_id=1, home="Manchester City", away="Liverpool",
               minute=80, has_stats=True)
    row = pa.observe({1: sig}, lgt.load(lgt.WIDE_TABLE_PATH), board, {},
                     enrich_status={})[0]
    missing = [c for c in pa._COLS if c not in row]
    assert not missing, f"_COLS names columns _base_row never sets: {missing}"
