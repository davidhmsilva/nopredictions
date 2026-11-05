"""Shadow variants (db/088): a change to an agent's rule, run beside it, never as it.

What these pin: a variant never trades and never changes what the agent does;
it sees the same fixtures at the same prices; it enters a fixture once; its
own entry window is its own; a change the runner cannot read, or one that
changes nothing, is refused; and the record is computed as the agent's is.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lab_strategy_runner as lr  # noqa: E402
import lab_variants as lv  # noqa: E402
from tests.test_lab_strategy_runner import _event  # noqa: E402

KO = datetime(2026, 9, 12, 14, tzinfo=timezone.utc)
DRAWS = {"market": "1x2", "side": "draw", "leagues": ["ENG-PR"], "odds_max": 3.0}


# ── what a variant may be ────────────────────────────────────────────────────

def test_a_variant_window_is_its_own_and_an_agent_keeps_the_close():
    assert lr.variant_window({}) == lr.ENTRY_WINDOW_MIN
    assert lr.variant_window({"enter_minutes_before": 180}) == 180
    assert lr.variant_window({"enter_minutes_before": 3}) == lr.ENTRY_WINDOW_MIN
    assert lr.variant_blocker({**DRAWS, "enter_minutes_before": 5000})
    assert lr.variant_blocker({**DRAWS, "enter_minutes_before": 180}) is None


def test_a_change_is_refused_when_it_changes_nothing_or_cannot_be_read():
    assert lv.change_problem(DRAWS, {}, []) == "the change is empty"
    assert "does not read" in lv.change_problem(DRAWS, {"corners_min": 9}, [])
    assert lv.change_problem(DRAWS, {"odds_max": 3.0}, []) == "the change leaves the rule as it is"
    assert lv.change_problem({**DRAWS, "fav_status": None}, {"fav_status": None}, []) \
        == "the change leaves the rule as it is"                    # null and absent are one rule
    assert "NBA" in lv.change_problem(DRAWS, {"market": "nba_ml"}, [])
    assert lv.change_problem(DRAWS, {"odds_max": 4.0}, []) is None
    assert lv.change_problem(DRAWS, {"enter_minutes_before": 180}, []) is None


def test_a_test_budget_and_no_duplicate_tests():
    running = [lv.variant_spec(DRAWS, {"odds_max": 3.0 + i / 10}) for i in range(1, 6)]
    assert "5 variants running" in lv.change_problem(DRAWS, {"odds_max": 9.0}, running)
    assert lv.change_problem(DRAWS, {"odds_max": 3.1}, running[:1]) \
        == "a running variant already tests this rule"


def test_a_null_in_the_change_removes_the_condition():
    assert "odds_max" not in lv.variant_spec(DRAWS, {"odds_max": None})
    assert lv.variant_spec(DRAWS, {"odds_min": 2.5})["odds_min"] == 2.5
    assert DRAWS["odds_max"] == 3.0                                 # the agent's spec untouched


# ── the record ───────────────────────────────────────────────────────────────

def test_an_arms_record_is_one_unit_at_the_ask_and_clv_against_the_close():
    rows = [
        {"ask": 0.25, "won": True, "close_price": 0.30, "close_source": "prices_history_1m"},
        {"ask": 0.50, "won": False, "close_price": 0.45, "close_source": "prices_history_1m"},
        {"ask": 0.40, "won": None, "close_price": None, "close_source": "none"},   # not settled
    ]
    s = lv.arm_stats(rows)
    assert (s["entries"], s["settled"], s["wins"]) == (3, 2, 1)
    assert s["pnl"] == 2.0                                          # +3 on the 4.00, −1
    assert s["roi"] == 1.0
    assert s["clv_n"] == 2 and s["clv"] == round((0.30 / 0.25 - 1 + 0.45 / 0.50 - 1) / 2, 4)
    assert s["clv_ci"] is not None
    assert lv.arm_stats([])["roi"] is None


# ── one cycle with variants ──────────────────────────────────────────────────

def _cycle(monkeypatch, now, variants, done=frozenset(), fail_variant_write=False):
    raw = dict(_event(), tags=[{"label": "EPL", "slug": "epl"}], startTime="2026-09-12T14:00:00Z")
    strategies = [{"id": 31, "name": "draws", "theory": None, "spec": dict(DRAWS)}]
    books = {"d1": {"best_bid": 0.24, "best_ask": 0.25, "ask_depth_usd": 500.0}}   # a 4.00 draw
    fetched, trades, logged = [], [], []
    monkeypatch.setattr(lr, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: now)}))
    monkeypatch.setattr(lr, "_conn", lambda: object())
    monkeypatch.setattr(lr, "running_strategies", lambda conn: strategies)
    monkeypatch.setattr(lr, "running_variants", lambda conn, strats: variants)
    monkeypatch.setattr(lr, "variants_entered", lambda conn, now: set(done))
    monkeypatch.setattr(lr, "fetch_upcoming", lambda now, minutes=None: fetched.append(minutes) or [raw])
    monkeypatch.setattr(lr, "already_in", lambda conn, sid, cids: False)
    monkeypatch.setattr(lr, "_fetch_book", lambda leg: books[leg["token_id"]])
    monkeypatch.setattr(lr, "write_trade", lambda *a: trades.append(a[1]["id"]) or 99)

    def write(conn, rows):
        if fail_variant_write and rows and rows[0]["variant_id"] is not None:
            raise psycopg2.IntegrityError("duplicate key")
        logged.extend(rows)
        return len(rows)
    monkeypatch.setattr(lr, "write_decisions", write)
    n = lr.run_once()
    return n, fetched, trades, logged


def _variant(vid, change):
    return {"id": 31, "variant_id": vid, "spec": lv.variant_spec(DRAWS, change)}


def test_a_variant_enters_in_shadow_and_the_agent_trades_exactly_as_before(monkeypatch):
    n, _, trades, logged = _cycle(monkeypatch, KO - timedelta(minutes=30),
                                  [_variant(7, {"odds_max": 5.0})])
    assert n == 0 and trades == []                                  # the agent: 4.00 > 3.0, no trade
    by = {r["variant_id"]: r for r in logged}
    assert (by[None]["stage"], by[None]["reason"]) == ("book", "odds 4.00 > 3.0")
    assert (by[7]["stage"], by[7]["ask"], by[7]["paper_trade_id"]) == ("entered", 0.25, None)
    assert by[7]["strategy_id"] == 31


def test_a_variant_enters_a_fixture_once(monkeypatch):
    _, _, _, logged = _cycle(monkeypatch, KO - timedelta(minutes=30), [_variant(7, {"odds_max": 5.0})],
                             done={(7, "Arsenal vs. Chelsea", KO)})
    assert [r["variant_id"] for r in logged] == [None]


def test_an_earlier_window_widens_the_board_for_the_variant_only(monkeypatch):
    v = _variant(8, {"odds_max": 5.0, "enter_minutes_before": 180})
    _, fetched, trades, logged = _cycle(monkeypatch, KO - timedelta(minutes=120), [v])
    assert fetched == [180]
    assert trades == []
    assert [(r["variant_id"], r["stage"], r["minutes_to_ko"]) for r in logged] == [(8, "entered", 120.0)]


def test_a_refused_variant_row_cannot_take_the_agents_log_with_it(monkeypatch):
    _, _, _, logged = _cycle(monkeypatch, KO - timedelta(minutes=30), [_variant(7, {"odds_max": 5.0})],
                             fail_variant_write=True)
    assert [r["variant_id"] for r in logged] == [None]


def test_without_variants_the_agents_window_is_unchanged(monkeypatch):
    _, fetched, _, logged = _cycle(monkeypatch, KO - timedelta(minutes=120), [])
    assert fetched == [lr.ENTRY_WINDOW_MIN]
    assert logged == []                                             # 120 min out: not the agent's yet
