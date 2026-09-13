"""lab_strategy_runner — the rules that keep a live trade the rule that was tested.

Every test here pins a way the live agent could quietly trade something other
than the theory its owner backtested: the wrong competition, the wrong side,
the wrong outcome of a two-way market, or a broken book.
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lab_strategy_runner as lr  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tag(label, slug):
    return {"label": label, "slug": slug}


def _mkt(kind, q, outcomes, tokens, prices, cid):
    return {"sportsMarketType": kind, "question": q, "outcomes": json.dumps(outcomes),
            "clobTokenIds": json.dumps(tokens), "outcomePrices": json.dumps(prices),
            "conditionId": cid, "active": True}


def _event():
    return {"title": "Arsenal vs. Chelsea", "markets": [
        _mkt("moneyline", "Will Arsenal win on 2026-09-12?", ["Yes", "No"], ["a1", "a2"], ["0.55", "0.45"], "0xa"),
        _mkt("moneyline", "Will Chelsea win on 2026-09-12?", ["Yes", "No"], ["c1", "c2"], ["0.20", "0.80"], "0xc"),
        _mkt("moneyline", "Will Arsenal vs. Chelsea end in a draw?", ["Yes", "No"], ["d1", "d2"], ["0.25", "0.75"], "0xd"),
        # Under listed FIRST: the side has to come from the label, not the position.
        _mkt("totals", "Arsenal vs. Chelsea: O/U 2.5", ["Under", "Over"], ["u", "o"], ["0.48", "0.52"], "0xt"),
        # Matches an "O/U 2.5" pattern perfectly and is not a goals market at all.
        _mkt("total_corners", "Arsenal vs. Chelsea: O/U 2.5 Corners", ["Over", "Under"], ["x", "y"], ["0.5", "0.5"], "0xz"),
    ]}


def _view(home=None, away=None, code="ENG-PR"):
    return lr.EventView(code=code, title="Arsenal vs. Chelsea",
                        kickoff=datetime(2026, 9, 12, 14, tzinfo=timezone.utc),
                        markets=lr.parse_markets(_event()), home=home, away=away)


# ── competition ──────────────────────────────────────────────────────────────

def test_competition_is_read_from_tags_and_ambiguity_fails_closed():
    assert lr.competition_code({"tags": [_tag("Soccer", "soccer"), _tag("EPL", "epl")]}) == "ENG-PR"
    assert lr.competition_code({"tags": [_tag("sea", "sea")]}) == "ITA-SA"
    # Real labels that share words with a mapped league, and are not it.
    assert lr.competition_code({"tags": [_tag("Premier League (Kazakhstan)", "kaz1")]}) is None
    assert lr.competition_code({"tags": [_tag("Brazil Serie A", "brazil-serie-a")]}) is None
    # Two leagues on one event is ambiguity, not a choice.
    assert lr.competition_code({"tags": [_tag("EPL", "epl"), _tag("La Liga", "laliga")]}) is None


def test_a_spec_never_trades_outside_the_backtest_universe():
    assert lr.pick({"market": "ou25", "side": "over", "leagues": ["ESP-LL"]}, _view())[0] is None
    # "All leagues" means the 22 in the dataset, not every board Polymarket lists.
    assert lr.pick({"market": "ou25", "side": "over", "leagues": None}, _view(code="URU-1"))[0] is None


# ── markets ──────────────────────────────────────────────────────────────────

def test_markets_come_from_the_family_and_the_outcome_label():
    m = lr.parse_markets(_event())
    assert set(m["win"]) == {"Arsenal", "Chelsea"}
    assert m["draw"]["token_id"] == "d1"
    assert m["ou25"]["over"]["token_id"] == "o"
    assert m["ou25"]["under"]["token_id"] == "u"


def test_an_over_needs_no_sides():
    leg, label, _ = lr.pick({"market": "ou25", "side": "over", "leagues": None}, _view())
    assert leg["token_id"] == "o" and label.startswith("Over 2.5")


# ── sides ────────────────────────────────────────────────────────────────────

def test_a_home_bet_without_confirmed_sides_is_skipped():
    leg, _, why = lr.pick({"market": "1x2", "side": "home", "leagues": None}, _view())
    assert leg is None and "ESPN" in why


def test_a_swapped_title_still_buys_the_real_home_team():
    # Polymarket titles it "Arsenal vs. Chelsea"; ESPN says Chelsea are at home.
    leg, label, _ = lr.pick({"market": "1x2", "side": "home", "leagues": None},
                            _view(home="Chelsea", away="Arsenal"))
    assert leg["token_id"] == "c1" and label.startswith("Chelsea")


def test_the_favourite_filter_reads_the_backed_side():
    spec = {"market": "1x2", "side": "home", "leagues": None, "fav_status": "favorite"}
    assert lr.pick(spec, _view(home="Arsenal", away="Chelsea"))[0]["token_id"] == "a1"
    assert lr.pick(spec, _view(home="Chelsea", away="Arsenal"))[0] is None


def test_orientation_needs_espn_to_agree_one_way_only(monkeypatch):
    class Fx:
        def __init__(self, home, away):
            self.home, self.away = home, away

    fixtures = [Fx("Chelsea", "Arsenal")]
    monkeypatch.setattr(lr.espn_stats, "match_fixture", lambda h, a, f: f[0])
    assert lr.orientation("Arsenal", "Chelsea", fixtures)[0] == "swapped"
    assert lr.orientation("Chelsea", "Arsenal", fixtures)[0] == "same"
    monkeypatch.setattr(lr.espn_stats, "match_fixture", lambda h, a, f: None)
    assert lr.orientation("Arsenal", "Chelsea", fixtures)[0] is None


# ── the book ─────────────────────────────────────────────────────────────────

def test_book_gates_and_the_odds_filter():
    good = {"best_bid": 0.49, "best_ask": 0.51, "ask_depth_usd": 400.0}
    assert lr.book_verdict(good, {}) is None
    assert "spread" in lr.book_verdict({**good, "best_bid": 0.30}, {})
    assert "depth" in lr.book_verdict({**good, "ask_depth_usd": 10.0}, {})
    assert "odds" in lr.book_verdict(good, {"odds_min": 2.5})
    assert lr.book_verdict(None, {}) == "no two-sided book"


# ── the spec ─────────────────────────────────────────────────────────────────

def test_blocker_and_the_site_carry_the_same_list():
    assert lr.run_blocker({"market": "1x2", "side": "home"}) is None
    assert "NBA" in lr.run_blocker({"market": "nba_ml", "side": "home"})
    assert "form" in lr.run_blocker({"market": "1x2", "side": "home", "home_form_pts5_min": 11})
    assert "season" in lr.run_blocker({"market": "ou25", "side": "over", "season_end": 2019})
    src = open(os.path.join(REPO, "site", "app", "lib", "agents.ts")).read()
    for f in lr.LIVE_UNSUPPORTED:
        assert f"'{f}'" in src, f"{f} is blocked here but not on the site"


def test_only_the_full_match_events_of_a_fixture_are_read():
    assert lr.is_full_match_event("VfL Bochum vs. SpVgg Greuther Fürth")
    assert lr.is_full_match_event("VfL Bochum vs. SpVgg Greuther Fürth - More Markets")
    # Siblings whose questions a full-match rule must never read.
    assert not lr.is_full_match_event("VfL Bochum vs. SpVgg Greuther Fürth - Halftime Result")
    assert not lr.is_full_match_event("VfL Bochum vs. SpVgg Greuther Fürth - Exact Score")


def test_titles_and_kickoff():
    assert lr.teams_from_title("FC Porto vs. FC Alverca - More Markets") == ("FC Porto", "FC Alverca")
    assert lr.kickoff_of({"startTime": "2026-09-12T14:00:00Z"}) == datetime(2026, 9, 12, 14, tzinfo=timezone.utc)
