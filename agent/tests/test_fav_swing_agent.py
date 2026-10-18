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


def test_a_second_goal_does_not_restart_the_clock():
    p = _pos()
    sw.step(p, 1, 0, 40, T0)
    assert sw.step(p, 1, 1, 43, T0 + timedelta(minutes=3)) == "regoal"
    assert p["goal_minute"] == 40 and (p["goal_home_goals"], p["goal_away_goals"]) == (1, 1)
    assert sw.step(p, 1, 1, 44, T0 + timedelta(minutes=4)) == "hold"
    assert sw.step(p, 1, 1, 45, T0 + timedelta(minutes=5)) == "sell_due"   # 5 min since the FIRST goal


def test_dortmund_werder_sells_at_two_nil():
    """pt#6333, 2026-10-09: the feed saw 1-0 at 20:11:15, 2-0 at 20:16:14 (0.4s
    before the clock ran out), 2-1 at 20:18:15, 2-2 at 20:23:19. The old rule
    restarted the clock on every goal and never sold."""
    t = datetime(2026, 10, 9, 20, 11, 15, 211977, tzinfo=timezone.utc)
    p = _pos()
    assert sw.step(p, 1, 0, 81, t) == "goal"
    assert sw.step(p, 2, 0, 86, t + timedelta(seconds=299.55)) == "regoal"
    assert sw.step(p, 2, 0, 87, t + timedelta(seconds=359.19)) == "sell_due"
    assert p["goal_minute"] == 81 and (p["goal_home_goals"], p["goal_away_goals"]) == (2, 0)


def test_a_goal_after_the_clock_ran_out_is_still_a_sale():
    p = _pos()
    sw.step(p, 1, 0, 40, T0)
    assert sw.step(p, 1, 0, 45, T0 + timedelta(minutes=5)) == "sell_due"   # book not clean, no sale yet
    assert sw.step(p, 1, 1, 46, T0 + timedelta(minutes=6)) == "sell_due"


def test_a_return_to_the_entry_score_still_cancels_after_a_further_goal():
    p = _pos()
    sw.step(p, 1, 0, 40, T0)
    sw.step(p, 2, 0, 42, T0 + timedelta(minutes=2))
    sw.step(p, 1, 0, 43, T0 + timedelta(minutes=3))                      # the second did not stand
    assert p["status"] == "goal_pending" and p["goal_minute"] == 40
    assert sw.step(p, 0, 0, 44, T0 + timedelta(minutes=4)) == "reversed"
    assert p["status"] == "open" and p["goal_seen_at"] is None


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
