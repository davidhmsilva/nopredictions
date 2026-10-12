"""
Tests for the Low Pressure HT Under 0.5 arm.

What would fail silently in production:
  1. The token. The Under is read off its own outcome label; a token taken from
     "the other index" would buy the Over while recording an Under.
  2. The gate. The ceiling is frozen per minute; a match pressing above it, or
     with no reading at all, must never be called quiet.
  3. The price. The Under is bought at its OWN book's ask, read when every other
     gate passes, not at 1 - (Over bid).
  4. Settlement. Only PM's resolution of the Under token pays.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_ht_under_agent.py -q
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fav_pressure_agent as fav  # noqa: E402
import ht_pressure_agent as ht  # noqa: E402
import ht_under_agent as ua  # noqa: E402


def _row(**kw) -> dict:
    row = {
        "fixture_id": -401, "home": "A", "away": "B", "event_title": "A vs. B",
        "minute": 22, "home_goals": 0, "away_goals": 0, "goals_total": 0,
        "pressure_now": 8.0, "best_bid": 0.47, "best_ask": 0.50,
        "bid_depth_usd": 200.0, "ask_depth_usd": 200.0, "score_agrees": True,
        "condition_id": "c1", "token_id": "t-over", "under_token_id": "t-under",
        "fair_base": 0.48,
        "under_ask": None, "under_would_enter": False, "under_entered": False,
        "under_paper_trade_id": None, "under_skip_reason": None,
    }
    row.update(kw)
    return row


def _book(ask=0.52, bid=0.49, depth=150.0):
    return {"best_ask": ask, "best_bid": bid, "ask_depth_usd": depth, "bid_depth_usd": depth}


def test_the_ceiling_is_frozen_per_minute():
    assert [ua.pressure_ceiling(m) for m in (15, 19, 20, 24, 25, 29, 30, 34, 35, 40)] == \
        [11.4, 11.4, 13.6, 13.6, 12.8, 12.8, 12.5, 12.5, 13.2, 13.2]
    assert ua.pressure_ceiling(14) is None and ua.pressure_ceiling(41) is None


def test_a_quiet_goalless_match_buys_the_under_at_its_own_ask():
    asked = []
    rows = [_row()]
    n = ua.decide(rows, fetch_book=lambda tok: asked.append(tok["token_id"]) or _book(ask=0.54))
    assert n == 1 and rows[0]["under_would_enter"]
    assert asked == ["t-under"]
    assert rows[0]["under_ask"] == 0.54          # the Under's book, not 1 - 0.47
    assert rows[0]["under_skip_reason"] is None


def test_a_pressing_match_is_not_quiet_and_costs_no_book_read():
    asked = []
    rows = [_row(pressure_now=13.6, minute=22)]   # at the ceiling is not below it
    assert ua.decide(rows, fetch_book=lambda tok: asked.append(1) or _book()) == 0
    assert "not quiet enough for 22'" in rows[0]["under_skip_reason"]
    assert asked == []


def test_no_reading_is_never_called_quiet():
    rows = [_row(pressure_now=None)]
    assert ua.decide(rows, fetch_book=lambda tok: _book()) == 0
    assert rows[0]["under_skip_reason"] == "no pressure reading"


def test_a_goal_or_the_clock_ends_it():
    rows = [_row(home_goals=1, goals_total=1), _row(minute=41), _row(minute=14)]
    assert ua.decide(rows, fetch_book=lambda tok: _book()) == 0
    assert "not 0-0" in rows[0]["under_skip_reason"]
    assert "outside" in rows[1]["under_skip_reason"] and "outside" in rows[2]["under_skip_reason"]


def test_book_gates_on_both_sides():
    wide_over = _row(best_bid=0.40, best_ask=0.50)
    thin_over = _row(ask_depth_usd=5.0)
    no_token = _row(under_token_id=None)
    disagree = _row(score_agrees=False)
    rows = [wide_over, thin_over, no_token, disagree]
    assert ua.decide(rows, fetch_book=lambda tok: _book()) == 0
    assert "Over spread" in wide_over["under_skip_reason"]
    assert "Over ask depth" in thin_over["under_skip_reason"]
    assert no_token["under_skip_reason"] == "no Under token"
    assert disagree["under_skip_reason"] == "score disagreement"
    for book, why in [(None, "no Under book"), (_book(ask=0.60, bid=0.50), "Under spread"),
                      (_book(depth=3.0), "Under ask depth"), (_book(ask=0.90, bid=0.88), "Under ask")]:
        r = [_row()]
        assert ua.decide(r, fetch_book=lambda tok, b=book: b) == 0
        assert why in r[0]["under_skip_reason"]


def test_the_under_token_is_read_off_its_label_not_its_index():
    mkt = {"question": "A vs. B: 1st Half O/U 0.5", "closed": False, "conditionId": "c9",
           "outcomes": json.dumps(["Under", "Over"]),
           "clobTokenIds": json.dumps(["tok-under", "tok-over"]),
           "outcomePrices": json.dumps(["0.45", "0.55"])}
    got = ht.ht_over05_market({"title": "A vs. B", "markets": [mkt]})
    assert got["token_id"] == "tok-over" and got["under_token_id"] == "tok-under"


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
        if "under_entered AND observed_at" in sql:
            self._one = (1,) if self.conn.already else None
        elif sql.lstrip().startswith("INSERT INTO paper_trades"):
            self._one = (991,)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self.conn.pending


class _Conn:
    autocommit = True

    def __init__(self, already=False, pending=()):
        self.already, self.pending, self.log = already, list(pending), []

    def cursor(self, cursor_factory=None):
        return _Cur(self)

    def commit(self):
        pass

    def writes(self, fragment):
        return [p for s, p in self.log if fragment in s]


def test_open_trades_buys_once_per_fixture():
    r = _row(under_would_enter=True, under_ask=0.54)
    assert ua.open_trades(_Conn(), 38, [r]) == 1
    assert r["under_entered"] and r["under_paper_trade_id"] == 991
    again = _row(under_would_enter=True, under_ask=0.54)
    assert ua.open_trades(_Conn(already=True), 38, [again]) == 0
    assert again["under_skip_reason"] == "already entered this fixture"


def test_the_trade_records_the_under_token_and_its_price():
    conn = _Conn()
    ua.open_trades(conn, 38, [_row(under_would_enter=True, under_ask=0.54)])
    (params,) = conn.writes("INSERT INTO paper_trades")
    assert params[0] == 38 and params[1].startswith("1st Half Under 0.5")
    assert params[2] == 0.54 and abs(params[3] - 1 / 0.54) < 1e-9
    assert params[-1] == "t-under"


def test_settle_pays_on_the_under_token_only(monkeypatch):
    monkeypatch.setattr(fav, "_pm_verdicts",
                        lambda cids: {"c1": {"t-over": False, "t-under": True}, "c2": {}})
    conn = _Conn(pending=[{"id": 5, "condition_id": "c1", "under_token_id": "t-under"},
                          {"id": 6, "condition_id": "c2", "under_token_id": "t-u2"},
                          {"id": 7, "condition_id": "c3", "under_token_id": "t-u3"}])
    assert ua.settle(conn) == 1                          # c2 unresolved, c3 not read
    assert conn.writes("UPDATE paper_trades") == [("won", True, 5)]
