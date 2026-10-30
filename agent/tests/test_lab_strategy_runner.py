"""lab_strategy_runner — the rules that keep a live trade the rule that was tested.

Every test here pins a way the live agent could quietly trade something other
than the theory its owner backtested: the wrong competition, the wrong side,
the wrong outcome of a two-way market, or a broken book.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import espn_stats  # noqa: E402
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

def test_blocker_and_the_site_agree():
    assert lr.run_blocker({"market": "1x2", "side": "home"}) is None
    assert "NBA" in lr.run_blocker({"market": "nba_ml", "side": "home"})
    assert "season" in lr.run_blocker({"market": "ou25", "side": "over", "season_end": 2019})
    # The site offers Deploy from its own copy of this rule. If it still blocked
    # a filter the runner can read, the idea would test and never deploy; the
    # other way round, the runner would pause what the site just switched on.
    src = open(os.path.join(REPO, "site", "app", "lib", "agents.ts")).read()
    assert "LIVE_UNSUPPORTED" not in src
    assert "NBA ideas are backtest-only" in src


# ── a team's recent games ────────────────────────────────────────────────────

KO = datetime(2026, 10, 31, 15, tzinfo=timezone.utc)


def _games(*results, last=datetime(2026, 10, 24, 15, tzinfo=timezone.utc), gap=7):
    """(goals for, goals against), oldest first, the last one on `last`."""
    n = len(results)
    return [espn_stats.TeamGame(kickoff=last - timedelta(days=gap * (n - 1 - i)), goals_for=f, goals_against=a)
            for i, (f, a) in enumerate(results)]


def test_a_form_filter_needs_the_real_sides():
    # "The home team's last 5" read off the wrong team is a different rule.
    spec = {"market": "ou25", "side": "over", "leagues": None, "home_form_pts5_max": 4}
    assert lr.needs_form(spec) and lr.needs_sides(spec)
    assert lr.run_blocker(spec) is None
    assert "ESPN" in lr.pick(spec, _view())[2]
    assert not lr.needs_form({"market": "ou25", "side": "over"})
    assert not lr.needs_sides({"market": "ou25", "side": "over"})


def test_form_uses_the_backtests_definitions():
    f = lr.form_of(_games((2, 0), (1, 1), (0, 3), (2, 2), (1, 0), (4, 1)), KO)
    # The previous FIVE, oldest dropped: D L D W W = 1 + 0 + 1 + 3 + 3 — and the
    # total goals of those five, averaged.
    assert f.form_pts5 == 8
    assert f.avg_tg5 == round((2 + 3 + 4 + 1 + 5) / 5, 3)
    assert f.rest_days == 7


def test_rest_counts_calendar_days_not_hours():
    late = datetime(2026, 10, 28, 20, 45, tzinfo=timezone.utc)
    early = datetime(2026, 10, 31, 11, 30, tzinfo=timezone.utc)
    assert lr.form_of(_games((1, 0), last=late), early).rest_days == 3


def test_a_game_never_counts_in_its_own_history():
    # ESPN adds a game as soon as it finishes; a rule read late must not see the
    # fixture itself, or anything played after it, whatever order they arrive in.
    earlier = _games((1, 0), (1, 0), (1, 0), (1, 0), (1, 0))
    later = [espn_stats.TeamGame(kickoff=KO, goals_for=0, goals_against=5),
             espn_stats.TeamGame(kickoff=KO + timedelta(days=7), goals_for=0, goals_against=5)]
    f = lr.form_of(later + earlier, KO)
    assert f.form_pts5 == 15 and f.avg_tg5 == 1.0 and f.rest_days == 7


def test_under_five_games_there_is_no_form_but_there_is_rest():
    f = lr.form_of(_games((1, 0), (0, 0), (2, 2), (0, 1)), KO)
    assert f.form_pts5 is None and f.avg_tg5 is None and f.rest_days == 7
    assert lr.form_of([], KO) == lr.TeamForm()


def test_an_unknown_value_fails_its_bound_like_a_null_in_the_backtest():
    spec = {"home_form_pts5_max": 4}
    assert lr.form_verdict(spec, None, None) == "home form_pts5 unknown"
    assert lr.form_verdict(spec, lr.TeamForm(rest_days=3), lr.TeamForm()) == "home form_pts5 unknown"
    # A bound on one side says nothing about the other.
    assert lr.form_verdict(spec, lr.TeamForm(form_pts5=4), None) is None


def test_every_bound_on_both_sides_is_read():
    home = lr.TeamForm(rest_days=3, form_pts5=4, avg_tg5=2.2)
    away = lr.TeamForm(rest_days=9, form_pts5=12, avg_tg5=3.4)
    assert lr.form_verdict({"home_rest_days_max": 4, "away_rest_days_min": 7}, home, away) is None
    assert "home rest_days" in lr.form_verdict({"home_rest_days_min": 7}, home, away)
    assert "away form_pts5" in lr.form_verdict({"away_form_pts5_max": 10}, home, away)
    assert "away avg_tg5" in lr.form_verdict({"away_avg_tg5_max": 3.0}, home, away)
    assert lr.form_verdict({"home_avg_tg5_min": 2.0, "home_avg_tg5_max": 2.5}, home, away) is None


def test_history_reaches_into_last_season_only_when_it_has_to():
    calls = []

    def get(code, team_id, season=None):
        calls.append(season)
        return _games((1, 0), (1, 0), (1, 0)) if season is None else _games((2, 2), (2, 2), last=datetime(2026, 5, 20, tzinfo=timezone.utc))

    games = lr.team_history("eng.1", "363", KO, get=get)
    assert calls == [None, 2025]    # the 2025-26 season, for a kick-off in 2026-27
    assert lr.form_of(games, KO).form_pts5 == 11

    calls.clear()
    enough = _games((1, 0), (1, 0), (1, 0), (1, 0), (1, 0))
    assert lr.team_history("eng.1", "363", KO, get=lambda c, t, s=None: calls.append(s) or enough) == enough
    assert calls == [None]


def test_a_failed_request_is_not_a_short_history():
    # Reading the failure as "no games yet" would still answer rest days from
    # whatever arrived, and a wrong number looks exactly like a right one.
    assert lr.team_history("eng.1", "363", KO, get=lambda c, t, s=None: None) is None
    calls = {"n": 0}

    def second_fails(code, team_id, season=None):
        calls["n"] += 1
        return _games((1, 0)) if season is None else None

    assert lr.team_history("eng.1", "363", KO, get=second_fails) is None
    assert calls["n"] == 2


def test_a_promoted_club_has_no_form_yet_rather_than_half_of_one():
    games = lr.team_history("eng.1", "306", KO, get=lambda c, t, s=None: _games((1, 0), (0, 1)) if s is None else [])
    f = lr.form_of(games, KO)
    assert f.form_pts5 is None and f.rest_days == 7


def test_forms_are_read_once_per_cycle_and_fail_closed_without_espn(monkeypatch):
    calls = []

    def history(code, team_id, kickoff):
        calls.append(team_id)
        return _games((1, 0), (1, 0), (1, 0), (1, 0), (1, 0))

    monkeypatch.setattr(lr, "team_history", history)
    cache = {}
    ev = _view(home="Arsenal", away="Chelsea")
    ev.kickoff = KO
    assert lr.forms_for(ev, cache) == (None, None)      # no ESPN fixture, no form
    assert calls == []

    ev.espn = espn_stats.EspnFixture(event_id="1", league_code="eng.1", league="EPL", home="Arsenal",
                                     away="Chelsea", minute=None, state="pre", detail="",
                                     home_id="359", away_id="363")
    home, away = lr.forms_for(ev, cache)
    assert home.form_pts5 == 15 and away.form_pts5 == 15
    lr.forms_for(ev, cache)
    assert calls == ["359", "363"]

    # A team ESPN gave no id for has no form; it does not borrow the other's.
    ev.espn.away_id = ""
    assert lr.forms_for(ev, {})[1] is None


def test_only_the_full_match_events_of_a_fixture_are_read():
    assert lr.is_full_match_event("VfL Bochum vs. SpVgg Greuther Fürth")
    assert lr.is_full_match_event("VfL Bochum vs. SpVgg Greuther Fürth - More Markets")
    # Siblings whose questions a full-match rule must never read.
    assert not lr.is_full_match_event("VfL Bochum vs. SpVgg Greuther Fürth - Halftime Result")
    assert not lr.is_full_match_event("VfL Bochum vs. SpVgg Greuther Fürth - Exact Score")


def test_titles_and_kickoff():
    assert lr.teams_from_title("FC Porto vs. FC Alverca - More Markets") == ("FC Porto", "FC Alverca")
    assert lr.kickoff_of({"startTime": "2026-09-12T14:00:00Z"}) == datetime(2026, 9, 12, 14, tzinfo=timezone.utc)


def test_team_lists_keep_a_two_club_name_out():
    big = ["Benfica", "FC Porto", "Sporting CP"]
    spec = {"market": "1x2", "side": "home", "leagues": ["POR-PL"],
            "home_team_in": big, "home_team_not_in": ["SC Braga"],
            "away_team_not_in": big + ["SC Braga"]}
    assert lr.needs_sides(spec)
    assert lr.team_lists_fail(spec, "Sporting CP", "CD Tondela") is None
    assert lr.team_lists_fail(spec, "Sport Lisboa e Benfica", "FC Arouca") is None
    # "Sporting Braga" scores 1.0 against "Sporting CP": only the exclusion stops it
    assert lr.team_lists_fail(spec, "Sporting Braga", "CD Tondela") == "home team excluded"
    assert lr.team_lists_fail(spec, "FC Porto", "SC Braga") == "away team excluded"
    assert lr.team_lists_fail(spec, "FC Porto", "Sporting CP") == "away team excluded"
    assert lr.team_lists_fail(spec, "Casa Pia AC", "FC Porto") == "home team not in list"
    assert lr.team_lists_fail(spec, None, None) == "home team not in list"
