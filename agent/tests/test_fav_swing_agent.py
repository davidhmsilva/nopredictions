"""Favourite Swing: the kick-off band, the side, and the goal clock."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import fav_swing_agent as sw  # noqa: E402

T0 = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)


def _pos(h=0, a=0):
    return {"status": "open", "entry_home_goals": h, "entry_away_goals": a, "goals_reversed": 0,
            "goal_seen_at": None, "goal_minute": None, "goal_home_goals": None, "goal_away_goals": None}


def _cap(home_p, away_p):
    return {"at": T0, "legs": {"FC Porto": {"price": home_p, "token_id": "h"},
                               "CD Tondela": {"price": away_p, "token_id": "a"}}}


def test_the_kickoff_band_is_the_raw_price_and_the_side_comes_from_api_football():
    fav = sw.favourite_at_ko(_cap(0.72, 0.10), "FC Porto", "Tondela")      # 1.39
    assert fav["side"] == "home" and fav["token_id"] == "h"
    # api-football lists the teams the other way round: the side follows the NAME
    assert sw.favourite_at_ko(_cap(0.72, 0.10), "Tondela", "FC Porto")["side"] == "away"
    assert sw.favourite_at_ko(_cap(0.80, 0.07), "FC Porto", "Tondela") is None   # 1.25
    assert sw.favourite_at_ko(_cap(0.60, 0.15), "FC Porto", "Tondela") is None   # 1.67
    assert sw.favourite_at_ko(_cap(0.72, 0.10), "FC Porto", "Benfica") is None   # a name that resolves nowhere


def test_a_goal_starts_the_clock_and_five_minutes_sells():
    p = _pos(1, 1)
    assert sw.step(p, 1, 1, 30, T0) == "hold"
    assert sw.step(p, 2, 1, 31, T0) == "goal" and p["status"] == "goal_pending"
    assert sw.step(p, 2, 1, 34, T0 + timedelta(minutes=4)) == "hold"
    assert sw.step(p, 2, 1, 36, T0 + timedelta(minutes=5)) == "sell_due"


def test_an_opponent_goal_also_closes():
    p = _pos()
    assert sw.step(p, 0, 1, 50, T0) == "goal"
    assert sw.step(p, 0, 1, 55, T0 + timedelta(minutes=5)) == "sell_due"


def test_a_goal_that_did_not_stand_cancels_the_clock():
    p = _pos()
    sw.step(p, 1, 0, 40, T0)
    assert sw.step(p, 0, 0, 42, T0 + timedelta(minutes=2)) == "reversed"
    assert p["status"] == "open" and p["goal_seen_at"] is None and p["goals_reversed"] == 1
    assert sw.step(p, 0, 0, 46, T0 + timedelta(minutes=6)) == "hold"


def test_a_second_goal_restarts_the_clock():
    p = _pos()
    sw.step(p, 1, 0, 40, T0)
    assert sw.step(p, 1, 1, 43, T0 + timedelta(minutes=3)) == "regoal"
    assert sw.step(p, 1, 1, 46, T0 + timedelta(minutes=6)) == "hold"     # 3 min since the equaliser
    assert sw.step(p, 1, 1, 48, T0 + timedelta(minutes=8)) == "sell_due"


def test_exit_waits_for_a_clean_book_then_forces():
    wide = {"best_bid": 0.40, "best_ask": 0.60}
    assert not sw.exit_book_ok(wide, 300)
    assert sw.exit_book_ok(wide, sw.EXIT_FORCE_S)
    assert sw.exit_book_ok({"best_bid": 0.80, "best_ask": 0.82}, 300)
    assert not sw.exit_book_ok(None, 5000)


def test_fees_are_paid_both_ways():
    shares = 1 / sw.cost_per_share(0.70)
    flat = shares * sw.sale_per_share(0.70)
    assert flat < 1.0                                    # round trip at the same price loses the fee twice
    assert abs((1 - flat) - (0.05 * 0.3 * 2) / (1 + 0.05 * 0.3)) < 1e-9


# ── db/078: ticks and the control arm ────────────────────────────────────────

def test_a_one_sided_book_is_kept_for_the_ticks_but_not_for_the_exit():
    b = sw.parse_book({"bids": [{"price": "0.81", "size": "100"}, {"price": "0.83", "size": "10"}],
                       "asks": []})
    assert b["best_bid"] == 0.83 and b["best_ask"] is None and b["ask_depth_usd"] is None
    assert abs(b["bid_depth_usd"] - (0.83 * 10 + 0.81 * 100)) < 1e-9
    assert sw.two_sided(b) is None                      # the exit gate sees what _fetch_book saw
    full = sw.parse_book({"bids": [{"price": "0.80", "size": "5"}],
                          "asks": [{"price": "0.84", "size": "5"}, {"price": "0.82", "size": "5"}]})
    assert full["best_ask"] == 0.82 and sw.two_sided(full) is full


def test_the_control_arm_drops_only_the_pressure_gate():
    assert sw.is_pressing(25.0, 4.0)
    assert not sw.is_pressing(25.0, 10.0)               # dominance 15 < 20
    assert not sw.is_pressing(18.0, 0.0)                # own index < 19
    assert not sw.is_pressing(None, None)               # no stats: the pressure arm cannot enter


class _Sig:
    def __init__(self, fid=1, home="FC Porto", away="Tondela", minute=8, hg=0, ag=0, stats=True):
        self.fixture_id, self.home, self.away, self.minute = fid, home, away, minute
        self.home_goals, self.away_goals, self.has_stats = hg, ag, stats


def _wire(monkeypatch, fav_p, dog_p):
    fx = {"title": "FC Porto vs CD Tondela"}
    monkeypatch.setattr(sw, "match_pm_fixture", lambda sig, fxs: fx)
    monkeypatch.setattr(sw, "opening_pressure", lambda sig: (None, fav_p, dog_p))
    monkeypatch.setattr(sw, "_fetch_book", lambda tok: {"best_bid": 0.70, "best_ask": 0.71,
                                                       "ask_depth_usd": 500.0})
    state = sw.SwingState()
    state.ko[fx["title"]] = {**_cap(0.72, 0.10), "kickoff": T0}
    state.capture = lambda fxs: None
    return state


def test_entry_candidates_split_by_arm(monkeypatch):
    state = _wire(monkeypatch, 30.0, 5.0)                # pressing
    out = sw.entry_candidates({1: _Sig()}, [], state, {"pressure": set(), "control": set()})
    assert sorted(c["arm"] for c in out) == ["control", "pressure"]

    state = _wire(monkeypatch, 12.0, 10.0)               # not pressing: control only
    out = sw.entry_candidates({1: _Sig()}, [], state, {"pressure": set(), "control": set()})
    assert [c["arm"] for c in out] == ["control"] and out[0]["fav_pressure"] == 12.0

    state = _wire(monkeypatch, 30.0, 5.0)                # control already holds it
    out = sw.entry_candidates({1: _Sig()}, [], state, {"pressure": set(), "control": {1}})
    assert [c["arm"] for c in out] == ["pressure"]

    state = _wire(monkeypatch, 30.0, 5.0)                # no stats: control, with no reading
    out = sw.entry_candidates({1: _Sig(stats=False)}, [], state, {"pressure": set(), "control": set()})
    assert [c["arm"] for c in out] == ["control"] and out[0]["fav_pressure"] is None


def test_a_control_only_fixture_is_not_rebooked_every_poll(monkeypatch):
    state = _wire(monkeypatch, 12.0, 10.0)
    calls = []
    monkeypatch.setattr(sw, "_fetch_book", lambda tok: calls.append(1) or None)   # book fails the gate
    held = {"pressure": set(), "control": set()}
    assert sw.entry_candidates({1: _Sig()}, [], state, held) == []
    assert sw.entry_candidates({1: _Sig()}, [], state, held) == []
    assert len(calls) == 1                               # the second poll is inside CONTROL_RECHECK_S


class _Conn:
    def __init__(self):
        self.ticks, self.closed, self.saved = [], [], []


def test_manage_ticks_every_poll_and_sells_on_the_same_book(monkeypatch):
    now = datetime.now(timezone.utc)
    pos = {**_pos(), "id": 7, "arm": "control", "fixture_id": 1, "home": "FC Porto", "away": "Tondela",
           "fav_team": "FC Porto", "token_id": "h", "entered_at": now - timedelta(minutes=30),
           "shares": 1.4, "fee_rate": 0.05, "entry_ask": 0.71, "paper_trade_id": None,
           "status": "goal_pending", "goal_seen_at": now - timedelta(minutes=6),
           "goal_home_goals": 1, "goal_away_goals": 0}
    conn = _Conn()
    monkeypatch.setattr(sw, "open_positions", lambda c: [dict(pos)])
    monkeypatch.setattr(sw, "read_book", lambda tok: {"best_bid": 0.86, "best_ask": 0.87,
                                                      "bid_depth_usd": 900.0, "ask_depth_usd": 800.0})
    monkeypatch.setattr(sw, "record_tick", lambda c, p, sig, book: c.ticks.append((p["status"], sig.minute)))
    monkeypatch.setattr(sw, "close", lambda c, p, **kw: c.closed.append(kw))
    monkeypatch.setattr(sw, "save", lambda c, p: c.saved.append(p))
    counts = sw.manage(conn, {1: _Sig(minute=40, hg=1, ag=0)})
    assert counts["sold"] == 1 and counts["ticks"] == 1
    assert conn.ticks == [("sold", 40)] and conn.closed[0]["bid"] == 0.86

    # the fixture missing from the feed still gets a tick, and no step
    conn = _Conn()
    monkeypatch.setattr(sw, "open_positions", lambda c: [dict(pos, status="open", goal_seen_at=None)])
    monkeypatch.setattr(sw, "record_tick", lambda c, p, sig, book: c.ticks.append((p["status"], sig)))
    sw.manage(conn, {})
    assert conn.ticks == [("open", None)] and conn.saved == []
