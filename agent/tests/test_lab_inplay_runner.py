"""Lab live rules: every way a live trade could stop being the rule that was saved."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import lab_inplay_runner as lr  # noqa: E402

T0 = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)


def _ko(home_p, away_p):
    return {"legs": {"FC Porto": {"price": home_p, "token_id": "h"},
                     "CD Tondela": {"price": away_p, "token_id": "a"}}}


def _spec(**kw):
    base = {"kind": "inplay", "market": "win", "team": "favourite", "ko_odds_min": None, "ko_odds_max": None,
            "minute_min": 5, "minute_max": 85, "score": "any", "pressure": "none",
            "pressure_level": "pressing", "pressure_window": "match", "odds_min": None, "odds_max": None,
            "exit": "hold", "exit_goal": "any", "exit_wait_min": 5, "exit_minute": None, "leagues": None}
    base.update(kw)
    return base


def test_the_team_follows_the_kickoff_price_and_api_football_names():
    assert lr.team_side(_spec(team="favourite"), _ko(0.72, 0.10), "FC Porto", "Tondela") == ("home", 0.72)
    # api-football lists them the other way round: the side follows the NAME
    assert lr.team_side(_spec(team="favourite"), _ko(0.72, 0.10), "Tondela", "FC Porto") == ("away", 0.72)
    assert lr.team_side(_spec(team="underdog"), _ko(0.72, 0.10), "FC Porto", "Tondela") == ("away", 0.10)
    assert lr.team_side(_spec(team="home"), None, "FC Porto", "Tondela") == ("home", None)
    # favourite with no kick-off price is unknown, never guessed
    assert lr.team_side(_spec(team="favourite"), None, "FC Porto", "Tondela") == (None, None)
    assert lr.team_side(_spec(team="favourite"), _ko(0.72, 0.10), "FC Porto", "Benfica") == (None, None)


def test_score_states_are_relative_to_the_team():
    assert lr.score_ok("level", None, 1, 1) and not lr.score_ok("level", None, 1, 0)
    assert lr.score_ok("goalless", None, 0, 0) and not lr.score_ok("goalless", None, 1, 1)
    assert lr.score_ok("team_ahead", "away", 0, 1) and not lr.score_ok("team_ahead", "home", 0, 1)
    assert lr.score_ok("team_behind", "home", 0, 1)
    assert not lr.score_ok("team_ahead", None, 0, 1)


def test_pressure_gates():
    press = _spec(pressure="team")
    assert lr.pressure_ok(press, "home", (25.0, 4.0))
    assert not lr.pressure_ok(press, "home", (25.0, 10.0))       # pressing, not out-pressing
    assert not lr.pressure_ok(press, "away", (25.0, 4.0))
    assert lr.pressure_ok(_spec(pressure="either"), None, (4.0, 25.0))
    assert lr.pressure_ok(_spec(pressure="match"), None, (20.0, 20.0))
    assert not lr.pressure_ok(_spec(pressure="match", pressure_level="dominating"), None, (20.0, 20.0))
    assert not lr.pressure_ok(press, "home", None)                 # no reading, no entry
    assert lr.pressure_ok(_spec(), None, None)


def test_odds_bands_are_decimal_odds_on_a_probability():
    assert lr.odds_in(1.30, 1.50, 0.72)          # 1.39
    assert not lr.odds_in(1.30, 1.50, 0.80)      # 1.25
    assert lr.odds_in(None, None, None)
    assert not lr.odds_in(1.3, None, None)


def _pos(spec, h=0, a=0, side="home"):
    return {"spec": spec, "status": "open", "entry_home_goals": h, "entry_away_goals": a,
            "team_side": side, "goals_reversed": 0, "goal_seen_at": None, "goal_minute": None,
            "goal_home_goals": None, "goal_away_goals": None}


def test_after_any_goal_waits_and_a_disallowed_goal_cancels():
    p = _pos(_spec(exit="after_goal", exit_wait_min=3))
    assert lr.step(p, 1, 0, 30, T0) == "goal"
    assert lr.step(p, 0, 0, 31, T0 + timedelta(minutes=1)) == "reversed"
    assert lr.step(p, 0, 1, 40, T0 + timedelta(minutes=10)) == "goal"
    assert lr.step(p, 0, 1, 43, T0 + timedelta(minutes=13)) == "sell_due"


def test_after_the_teams_goal_ignores_the_opponents():
    p = _pos(_spec(exit="after_goal", exit_goal="team"), side="away")
    assert lr.step(p, 1, 0, 30, T0) == "hold"                       # home scored: not ours
    assert lr.step(p, 1, 1, 35, T0 + timedelta(minutes=5)) == "goal"  # the away team did
    assert lr.step(p, 1, 1, 40, T0 + timedelta(minutes=10)) == "sell_due"
    q = _pos(_spec(exit="after_goal", exit_goal="opponent"), side="home")
    assert lr.step(q, 1, 0, 30, T0) == "hold"
    assert lr.step(q, 1, 1, 31, T0) == "goal"


def test_at_minute_and_hold():
    p = _pos(_spec(exit="at_minute", exit_minute=80))
    assert lr.step(p, 0, 0, 79, T0) == "hold"
    assert lr.step(p, 0, 0, 80, T0) == "sell_due" and p["goal_seen_at"] == T0
    h = _pos(_spec(exit="hold"))
    assert lr.step(h, 3, 3, 89, T0) == "hold"


def _mkt(q, outcomes, toks, prices, cid):
    return {"question": q, "outcomes": json.dumps(outcomes), "clobTokenIds": json.dumps(toks),
            "outcomePrices": json.dumps(prices), "conditionId": cid}


FX = {"title": "FC Porto vs. CD Tondela", "markets": [
    _mkt("Will FC Porto win on 2026-09-29?", ["Yes", "No"], ["py", "pn"], ["0.7", "0.3"], "0xp"),
    _mkt("Will CD Tondela win on 2026-09-29?", ["Yes", "No"], ["ty", "tn"], ["0.1", "0.9"], "0xt"),
    _mkt("Will FC Porto vs. CD Tondela end in a draw?", ["Yes", "No"], ["dy", "dn"], ["0.2", "0.8"], "0xd"),
    _mkt("Will the first half of FC Porto vs. CD Tondela end in a draw?", ["Yes", "No"], ["hy", "hn"], ["0.4", "0.6"], "0xh"),
    _mkt("FC Porto vs. CD Tondela: O/U 1.5", ["Over", "Under"], ["o15", "u15"], ["0.6", "0.4"], "0x15"),
]}


def test_the_market_bought_is_the_one_named():
    assert lr.market_token(_spec(market="win"), FX, "away", "FC Porto", "Tondela", 0)["token_id"] == "ty"
    assert lr.market_token(_spec(market="draw"), FX, None, "FC Porto", "Tondela", 0)["token_id"] == "dy"
    ng = lr.market_token(_spec(market="next_goal"), FX, None, "FC Porto", "Tondela", 1)
    assert ng["token_id"] == "o15" and ng["target_line"] == 1.5
    assert lr.market_token(_spec(market="next_goal"), FX, None, "FC Porto", "Tondela", 3) is None


def _sig(**kw):
    base = dict(minute=30, home="FC Porto", away="Tondela", home_goals=0, away_goals=0, has_stats=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_pre_book_checks_minute_league_ko_band():
    ok, side, ko, _ = lr.pre_book_checks(_spec(ko_odds_min=1.3, ko_odds_max=1.5), _sig(), FX, _ko(0.72, 0.1), "POR-PL")
    assert ok and side == "home" and ko == 0.72
    assert not lr.pre_book_checks(_spec(minute_min=40), _sig(), FX, _ko(0.72, 0.1), "POR-PL")[0]
    assert not lr.pre_book_checks(_spec(leagues=["ITA-SA"]), _sig(), FX, _ko(0.72, 0.1), "POR-PL")[0]
    assert not lr.pre_book_checks(_spec(ko_odds_min=1.5, ko_odds_max=1.8), _sig(), FX, _ko(0.72, 0.1), "POR-PL")[0]
    # a pressure rule on a match with no stats never fires
    assert not lr.pre_book_checks(_spec(pressure="team"), _sig(), FX, _ko(0.72, 0.1), "POR-PL")[0]
