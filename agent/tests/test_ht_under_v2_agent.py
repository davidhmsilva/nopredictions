"""
Tests for Low Pressure HT Under 0.5 v2.

What would fail silently in production:
  1. The streak. Six polls below the ceiling IN A ROW: a pressing poll, or a poll
     with no reading, must start the count again, or v2 is v1 with a delay.
  2. The peak. A match that pressed at 19 earlier is not "struggling to create",
     however quiet the last fifteen minutes read.
  3. The goal game. No Over 2.5 price is not a pass.
  4. The order. v2 reads v1's gate off the row, so it must decide before
     v1.open_trades clears that flag on a fixture v1 already holds.
  5. The record. v2's trades go under v2's strategy and v2's columns, never v1's.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_ht_under_v2_agent.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fav_pressure_agent as fav  # noqa: E402
import ht_pressure_agent as ht  # noqa: E402
import ht_under_agent as v1  # noqa: E402
import ht_under_v2_agent as v2  # noqa: E402


def _row(**kw) -> dict:
    row = {
        "fixture_id": -401, "home": "A", "away": "B", "event_title": "A vs. B",
        "minute": 22, "home_goals": 0, "away_goals": 0, "goals_total": 0,
        "pressure_now": 8.0, "best_bid": 0.47, "best_ask": 0.50,
        "bid_depth_usd": 200.0, "ask_depth_usd": 200.0, "score_agrees": True,
        "condition_id": "c1", "token_id": "t-over", "under_token_id": "t-under",
        "fair_base": 0.48, "pre_over25": 0.58,
        "under_ask": None, "under_would_enter": False, "under_entered": False,
        "under_paper_trade_id": None, "under_skip_reason": None,
        "under2_streak": None, "under2_ask": None, "under2_would_enter": False,
        "under2_entered": False, "under2_paper_trade_id": None, "under2_skip_reason": None,
    }
    row.update(kw)
    return row


def _book(ask=0.52, bid=0.49, depth=150.0):
    return {"best_ask": ask, "best_bid": bid, "ask_depth_usd": depth, "bid_depth_usd": depth}


def _poll(state, **kw) -> dict:
    """One cycle for one fixture, in production order: v1 decides, then v2."""
    r = _row(**kw)
    v1.decide([r], fetch_book=lambda tok: _book(ask=0.54))
    v2.decide([r], state)
    return r


def _quiet(state, polls, start=17, **kw) -> dict:
    r = None
    for i in range(polls):
        r = _poll(state, minute=start + i, **kw)
    return r


def test_the_frozen_numbers():
    assert (v2.QUIET_STREAK, v2.PEAK_CEILING, v2.MIN_OVER25) == (6, 19.0, 0.52)
    assert v2.PEAK_CEILING == ht.MIN_PRESSURE
    assert v2.STRATEGY_NAME != v1.STRATEGY_NAME


def test_it_waits_for_six_quiet_polls_in_a_row():
    state = v2.UnderV2State()
    fifth = _quiet(state, 5)
    assert fifth["under_would_enter"]                    # v1 would already be in
    assert not fifth["under2_would_enter"]
    assert fifth["under2_skip_reason"] == "quiet for 5 polls, needs 6"
    sixth = _poll(state, minute=22)
    assert sixth["under2_would_enter"] and sixth["under2_streak"] == 6
    assert sixth["under2_ask"] == 0.54                   # v1's read of the Under book
    assert sixth["under2_skip_reason"] is None


def test_a_pressing_poll_starts_the_count_again():
    state = v2.UnderV2State()
    _quiet(state, 5)
    loud = _poll(state, minute=22, pressure_now=15.0)    # above 13.6, below 19
    assert loud["under2_streak"] == 0
    after = _quiet(state, 5, start=23)
    assert not after["under2_would_enter"]
    assert _poll(state, minute=28)["under2_would_enter"]


def test_no_reading_is_not_a_quiet_poll():
    state = v2.UnderV2State()
    _quiet(state, 5)
    blind = _poll(state, minute=22, pressure_now=None)
    assert blind["under2_streak"] == 0 and not blind["under2_would_enter"]


def test_a_match_that_pressed_earlier_is_out_for_the_half():
    state = v2.UnderV2State()
    _poll(state, minute=16, pressure_now=19.0)
    r = _quiet(state, 8, start=17)
    assert r["under2_streak"] == 8 and not r["under2_would_enter"]
    assert r["under2_skip_reason"] == "pressed earlier (peak 19.0 >= 19.0)"


def test_it_has_to_be_a_goal_game():
    state = v2.UnderV2State()
    low = _quiet(state, 6, pre_over25=0.51)
    assert not low["under2_would_enter"]
    assert low["under2_skip_reason"] == "not a goal game (Over 2.5 0.51 < 0.52)"
    state = v2.UnderV2State()
    none = _quiet(state, 6, pre_over25=None)
    assert none["under2_skip_reason"] == "no Over 2.5 price for this fixture"
    state = v2.UnderV2State()
    assert _quiet(state, 6, pre_over25=0.52)["under2_would_enter"]


def test_v1s_gate_is_v2s_gate():
    state = v2.UnderV2State()
    _quiet(state, 5)
    goal = _poll(state, minute=22, goals_total=1, home_goals=1)
    assert not goal["under2_would_enter"]
    assert goal["under2_skip_reason"].startswith("v1 gate: not 0-0")
    state = v2.UnderV2State()
    _quiet(state, 5, start=36)
    late = _poll(state, minute=41)
    assert late["under2_skip_reason"] == "minute 41 outside 15-40"
    assert late["under2_streak"] is None


def test_the_streak_follows_the_teams_not_the_fixture_id():
    state = v2.UnderV2State()
    _quiet(state, 5, fixture_id=1234)
    assert _poll(state, minute=22, fixture_id=-401)["under2_would_enter"]


def test_two_fixtures_do_not_share_a_streak():
    state = v2.UnderV2State()
    _quiet(state, 5)
    other = _poll(state, minute=22, home="C", away="D")
    assert other["under2_streak"] == 1 and not other["under2_would_enter"]


def test_old_fixtures_are_forgotten():
    state = v2.UnderV2State()
    _quiet(state, 3)
    state.seen[("A", "B")]["at"] -= 7 * 3600
    state.prune()
    assert state.seen == {}


def test_v1_holding_the_fixture_does_not_stop_v2():
    """v1.open_trades clears under_would_enter on a fixture it already entered.
    v2 decided before that, so its own flag stands."""
    state = v2.UnderV2State()
    r = _quiet(state, 6)
    v1.open_trades(_Conn(already_v1=True), 38, [r])
    assert not r["under_would_enter"] and r["under2_would_enter"]
    assert v2.open_trades(_Conn(), 51, [r]) == 1


# ── trades and settlement ────────────────────────────────────────────────────

class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._one = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append((sql, params))
        if "under2_entered AND observed_at" in sql:
            self._one = (1,) if self.conn.already else None
        elif "under_entered AND observed_at" in sql:
            self._one = (1,) if self.conn.already_v1 else None
        elif sql.lstrip().startswith("INSERT INTO paper_trades"):
            self._one = (992,)
        elif "FROM strategies" in sql:
            self._one = self.conn.strategy

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self.conn.pending


class _Conn:
    autocommit = True

    def __init__(self, already=False, already_v1=False, pending=(), strategy=None):
        self.already, self.already_v1 = already, already_v1
        self.pending, self.strategy, self.log = list(pending), strategy, []

    def cursor(self, cursor_factory=None):
        return _Cur(self)

    def commit(self):
        pass

    def writes(self, fragment):
        return [p for s, p in self.log if fragment in s]


def _ready() -> dict:
    return _quiet(v2.UnderV2State(), 6)


def test_open_trades_buys_once_per_fixture_under_its_own_columns():
    r = _ready()
    assert v2.open_trades(_Conn(), 51, [r]) == 1
    assert r["under2_entered"] and r["under2_paper_trade_id"] == 992
    assert not r["under_entered"] and r["under_paper_trade_id"] is None
    again = _ready()
    assert v2.open_trades(_Conn(already=True), 51, [again]) == 0
    assert again["under2_skip_reason"] == "already entered this fixture"


def test_the_trade_goes_under_v2s_strategy_at_the_under_price():
    conn = _Conn()
    v2.open_trades(conn, 51, [_ready()])
    (params,) = conn.writes("INSERT INTO paper_trades")
    assert params[0] == 51 and params[1].startswith("1st Half Under 0.5")
    assert params[2] == 0.54 and abs(params[3] - 1 / 0.54) < 1e-9
    assert "Quiet for 6 polls" in params[7] and "PAPER" in params[7]
    assert params[-1] == "t-under"


def test_a_missing_strategy_observes_and_never_buys():
    assert v2.strategy_id(_Conn(strategy=None)) is None
    assert v2.strategy_id(_Conn(strategy=(51,))) == 51
    conn = _Conn()
    assert v2.open_trades(conn, None, [_ready()]) == 0
    assert conn.log == []


def test_the_row_carries_v2s_columns_to_the_table():
    cols = ["under2_streak", "under2_ask", "under2_would_enter", "under2_entered",
            "under2_paper_trade_id", "under2_skip_reason"]
    assert all(c in ht._COLS for c in cols)
    assert len(ht._COLS) == len(set(ht._COLS))


def test_settle_pays_on_the_under_token_only(monkeypatch):
    monkeypatch.setattr(fav, "_pm_verdicts",
                        lambda cids: {"c1": {"t-over": False, "t-under": True}, "c2": {}})
    conn = _Conn(pending=[{"id": 5, "condition_id": "c1", "under_token_id": "t-under"},
                          {"id": 6, "condition_id": "c2", "under_token_id": "t-u2"}])
    assert v2.settle(conn) == 1
    assert conn.writes("UPDATE paper_trades") == [("won", True, 5)]
    assert "under2_paper_trade_id" in conn.log[0][0]
