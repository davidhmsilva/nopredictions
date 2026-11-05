"""lab_strategy_runner — the rules that keep a live trade the rule that was tested.

Every test here pins a way the live agent could quietly trade something other
than the theory its owner backtested: the wrong competition, the wrong side,
the wrong outcome of a two-way market, or a broken book.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import espn_stats  # noqa: E402
import lab_strategy_runner as lr  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tag(label, slug):
    return {"label": label, "slug": slug}


def _mkt(kind, q, outcomes, tokens, prices, cid, line=None):
    return {"sportsMarketType": kind, "question": q, "outcomes": json.dumps(outcomes),
            "clobTokenIds": json.dumps(tokens), "outcomePrices": json.dumps(prices),
            "conditionId": cid, "active": True, "line": line}


def _event():
    return {"title": "Arsenal vs. Chelsea", "markets": [
        _mkt("moneyline", "Will Arsenal win on 2026-09-12?", ["Yes", "No"], ["a1", "a2"], ["0.55", "0.45"], "0xa"),
        _mkt("moneyline", "Will Chelsea win on 2026-09-12?", ["Yes", "No"], ["c1", "c2"], ["0.20", "0.80"], "0xc"),
        _mkt("moneyline", "Will Arsenal vs. Chelsea end in a draw?", ["Yes", "No"], ["d1", "d2"], ["0.25", "0.75"], "0xd"),
        # Under listed FIRST: the side has to come from the label, not the position.
        _mkt("totals", "Arsenal vs. Chelsea: O/U 2.5", ["Under", "Over"], ["u", "o"], ["0.48", "0.52"], "0xt", 2.5),
        # Matches an "O/U 2.5" pattern perfectly and is not a goals market at all.
        _mkt("total_corners", "Arsenal vs. Chelsea: O/U 2.5 Corners", ["Over", "Under"], ["x", "y"], ["0.5", "0.5"], "0xz", 2.5),
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
    assert lr.competition_code({"tags": [_tag("Brazil Sèrie B", "brazil-serie-b")]}) is None
    assert lr.competition_code({"tags": [_tag("Japan J2 League", "jpn2")]}) is None
    assert lr.competition_code({"tags": [_tag("Liga de Expansión MX", "mex2")]}) is None
    assert lr.competition_code({"tags": [_tag("Primera Nacional", "argpn")]}) is None
    assert lr.competition_code({"tags": [_tag("Superettan", "swe2")]}) is None
    assert lr.competition_code({"tags": [_tag("OBOS-ligaen", "nor2")]}) is None
    assert lr.competition_code({"tags": [_tag("China FA Cup", "china-fa-cup")]}) is None
    # The same word in two countries, and tags as Polymarket writes them today.
    assert lr.competition_code({"tags": [_tag("Austria Bundesliga", "austria-bundesliga")]}) == "AUT-BL"
    assert lr.competition_code({"tags": [_tag("Bundesliga", "bundesliga")]}) == "GER-BL1"
    assert lr.competition_code({"tags": [_tag("Russian Premier League", "rus")]}) == "RUS-PL"
    assert lr.competition_code({"tags": [_tag("Argentina Primera División", "arg")]}) == "ARG-PD"
    assert lr.competition_code({"tags": [_tag("Brazil Serie A", "brazil-serie-a")]}) == "BRA-SA"
    assert lr.competition_code({"tags": [_tag("Chinese Super League", "chinese-super-league")]}) == "CHN-CSL"
    # Two leagues on one event is ambiguity, not a choice.
    assert lr.competition_code({"tags": [_tag("EPL", "epl"), _tag("La Liga", "laliga")]}) is None


def test_a_spec_never_trades_outside_the_backtest_universe():
    assert lr.pick({"market": "ou25", "side": "over", "leagues": ["ESP-LL"]}, _view())[0] is None
    # "All leagues" means the ones in the dataset, not every board Polymarket lists.
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
    # A spec the runner cannot name a team for, or cannot read a bound of, is
    # paused with a reason. Walking past it would trade a broader rule than the
    # one tested, which is what a site deployed before this file would ask for.
    assert "which team" in lr.run_blocker({"market": "1x2", "side": "team"})
    assert "which team" in lr.run_blocker({"market": "1x2", "side": "opponent", "fav_status": "favorite"})
    assert lr.run_blocker({"market": "1x2", "side": "team", "fav_status": "favorite"}) is None
    assert "home_table_rank_max" in lr.run_blocker({"market": "1x2", "side": "home", "home_table_rank_max": 3})
    assert "away_table_rank_min" in lr.run_blocker({"market": "1x2", "side": "team", "team": "Arsenal", "opp_table_rank_min": 15})
    # team_ and opp_ mean nothing on a rule with no team in it.
    assert "team_last_gd_max" in lr.run_blocker({"market": "ou25", "side": "over", "team_last_gd_max": -1})
    assert lr.run_blocker({"market": "1x2", "side": "home", "home_last_gd_max": -1, "odds_max": 2}) is None
    # The site offers Deploy from its own copy of this rule. If it still blocked
    # a filter the runner can read, the idea would test and never deploy; the
    # other way round, the runner would pause what the site just switched on.
    src = open(os.path.join(REPO, "site", "app", "lib", "agents.ts")).read()
    assert "LIVE_UNSUPPORTED" not in src
    assert "NBA ideas are backtest-only" in src


# ── a team's recent games ────────────────────────────────────────────────────

KO = datetime(2026, 10, 31, 15, tzinfo=timezone.utc)


def _games(*results, last=datetime(2026, 10, 24, 15, tzinfo=timezone.utc), gap=7):
    """(goals for, goals against[, at home]), oldest first, the last one on `last`."""
    n = len(results)
    return [espn_stats.TeamGame(kickoff=last - timedelta(days=gap * (n - 1 - i)), goals_for=r[0], goals_against=r[1],
                                home=r[2] if len(r) > 2 else None)
            for i, r in enumerate(results)]


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


def test_its_last_game_is_a_goal_difference():
    assert lr.form_of(_games((0, 3), (2, 2)), KO).last_gd == 0
    assert lr.form_of(_games((2, 2), (0, 3)), KO).last_gd == -3
    assert lr.form_of(_games((2, 2), (4, 1)), KO).last_gd == 3
    assert lr.form_of([], KO).last_gd is None


def test_goals_scored_and_conceded_are_the_teams_own():
    f = lr.form_of(_games((9, 9), (2, 0), (1, 1), (0, 3), (2, 2), (1, 0)), KO)
    assert f.avg_gf5 == round((2 + 1 + 0 + 2 + 1) / 5, 3)
    assert f.avg_ga5 == round((0 + 1 + 3 + 2 + 0) / 5, 3)
    assert f.avg_tg5 == f.avg_gf5 + f.avg_ga5


def test_runs_count_back_from_the_last_game():
    # L W W D W W, newest last: two wins, four without defeat since the loss.
    f = lr.form_of(_games((0, 1), (1, 0), (1, 0), (0, 0), (1, 0), (1, 0)), KO)
    assert (f.win_run, f.unbeaten_run, f.loss_run, f.winless_run) == (2, 5, 0, 0)
    # W D L L, newest last.
    f = lr.form_of(_games((1, 0), (0, 0), (0, 1), (0, 1)), KO)
    assert (f.win_run, f.unbeaten_run, f.loss_run, f.winless_run) == (0, 0, 2, 3)


def test_a_run_the_history_ends_inside_of_is_not_a_number():
    # Three straight wins and nothing before them: it may be a run of three or of
    # thirty, so a bound on it fails, like a NULL. What broke it is still known.
    f = lr.form_of(_games((1, 0), (1, 0), (1, 0)), KO)
    assert f.win_run is None and f.unbeaten_run is None
    assert f.loss_run == 0 and f.winless_run == 0


def test_a_run_stops_counting_at_the_cap():
    f = lr.form_of(_games(*[(1, 0)] * 14), KO)
    assert f.win_run == lr.RUN_CAP and f.unbeaten_run == lr.RUN_CAP
    assert f.loss_run == 0
    # Exactly the cap in history is enough to say "the cap or more".
    assert lr.form_of(_games(*[(1, 0)] * lr.RUN_CAP), KO).win_run == lr.RUN_CAP


def test_form_at_this_end_counts_only_this_ends_games():
    games = _games((3, 0, True), (0, 3, False), (3, 0, True), (0, 3, False), (3, 0, True),
                   (0, 3, False), (3, 0, True), (0, 3, False), (3, 0, True), (0, 3, False))
    assert lr.form_of(games, KO, at_home=True).venue_pts5 == 15
    assert lr.form_of(games, KO, at_home=False).venue_pts5 == 0
    # The overall last 5 are the same however the team plays today.
    assert lr.form_of(games, KO, at_home=True).form_pts5 == lr.form_of(games, KO, at_home=False).form_pts5


def test_form_at_this_end_needs_five_games_there():
    games = _games((3, 0, True), (3, 0, True), (3, 0, True), (3, 0, True), (0, 3, False), (0, 3, False))
    assert lr.form_of(games, KO, at_home=True).venue_pts5 is None
    assert lr.form_of(games, KO, at_home=False).venue_pts5 is None
    # A game the feed gave no end for counts as neither, and where the team plays
    # today being unknown is no end at all.
    unknown = _games(*[(3, 0)] * 8)
    assert lr.form_of(unknown, KO, at_home=True).venue_pts5 is None
    assert lr.form_of(_games(*[(3, 0, True)] * 8), KO).venue_pts5 is None


def test_a_new_figure_never_counts_the_game_itself():
    earlier = _games((1, 0, True), (1, 0, False), (1, 0, True), (1, 0, False), (1, 0, True),
                     (1, 0, False), (1, 0, True), (1, 0, False), (1, 0, True), (1, 0, False))
    later = [espn_stats.TeamGame(kickoff=KO, goals_for=0, goals_against=5, home=True),
             espn_stats.TeamGame(kickoff=KO + timedelta(days=7), goals_for=0, goals_against=5, home=False)]
    f = lr.form_of(later + earlier, KO, at_home=True)
    assert f.last_gd == 1 and f.win_run == 10 and f.loss_run == 0
    assert f.venue_pts5 == 15 and f.avg_ga5 == 0 and f.avg_gf5 == 1


def test_a_new_bound_is_read_on_both_sides_and_an_unknown_fails_it():
    home = lr.TeamForm(last_gd=-3, win_run=0, unbeaten_run=0, loss_run=2, winless_run=4,
                       venue_pts5=4, avg_gf5=0.6, avg_ga5=2.4)
    away = lr.TeamForm(last_gd=2, win_run=3, unbeaten_run=6, loss_run=0, winless_run=0,
                       venue_pts5=11, avg_gf5=2.2, avg_ga5=0.8)
    fits = {"home_last_gd_max": -1, "home_loss_run_min": 2, "home_winless_run_min": 3, "home_venue_pts5_max": 4,
            "home_avg_ga5_min": 2, "away_last_gd_min": 1, "away_win_run_min": 3, "away_unbeaten_run_min": 5,
            "away_venue_pts5_min": 10, "away_avg_gf5_min": 2, "away_avg_ga5_max": 0.8}
    assert lr.form_verdict(fits, home, away) is None
    for k, why in (({"home_last_gd_min": 0}, "home last_gd"), ({"away_last_gd_max": 0}, "away last_gd"),
                   ({"home_win_run_min": 1}, "home win_run"), ({"away_unbeaten_run_max": 5}, "away unbeaten_run"),
                   ({"home_loss_run_max": 1}, "home loss_run"), ({"away_winless_run_min": 1}, "away winless_run"),
                   ({"home_venue_pts5_min": 5}, "home venue_pts5"), ({"away_avg_ga5_max": 0.5}, "away avg_ga5")):
        assert why in lr.form_verdict(k, home, away)
    assert lr.form_verdict({"home_win_run_min": 1}, lr.TeamForm(), away) == "home win_run unknown"


def test_history_reaches_into_last_season_only_when_it_has_to():
    calls = []

    def get(code, team_id, season=None):
        calls.append(season)
        return _games((1, 0), (1, 0), (1, 0)) if season is None else _games((2, 2), (2, 2), last=datetime(2026, 5, 20, tzinfo=timezone.utc))

    games = lr.team_history("eng.1", "363", KO, get=get)
    assert calls == [None, 2025]    # the 2025-26 season, for a kick-off in 2026-27
    assert lr.form_of(games, KO).form_pts5 == 11

    # Five games used to be enough. A run wants RUN_CAP games, and the form at one
    # end wants 5 there: ten played, but only four at home, is not yet enough.
    calls.clear()
    enough = _games(*[(1, 0, i % 2 == 0) for i in range(lr.RUN_CAP)])
    assert lr.team_history("eng.1", "363", KO, get=lambda c, t, s=None: calls.append(s) or enough) == enough
    assert calls == [None]

    calls.clear()
    lopsided = _games(*[(1, 0, i < 4) for i in range(lr.RUN_CAP)])
    lr.team_history("eng.1", "363", KO, get=lambda c, t, s=None: calls.append(s) or lopsided)
    assert calls == [None, 2025]


def test_a_calendar_year_league_asks_for_last_year_not_the_year_before():
    # In April 2027 the July boundary says "2026", so "last season" would be 2025 and
    # the whole of 2026 would be skipped: a wrong number, not a missing one.
    calls = []
    april = datetime(2027, 4, 10, 18, tzinfo=timezone.utc)
    short = _games((1, 0), (0, 1))
    lr.team_history("usa.1", "182", april, get=lambda c, t, s=None: calls.append(s) or short)
    assert calls == [None, 2026]
    calls.clear()
    lr.team_history("eng.1", "363", april, get=lambda c, t, s=None: calls.append(s) or short)
    assert calls == [None, 2025]
    # mex.1's and jpn.1's seasons run across the new year: 2026 is 2026-27 on both.
    calls.clear()
    lr.team_history("mex.1", "227", april, get=lambda c, t, s=None: calls.append(s) or short)
    assert calls == [None, 2025]
    assert "mex.1" not in lr.CALENDAR_YEAR_ESPN and "jpn.1" not in lr.CALENDAR_YEAR_ESPN


def test_a_feed_that_has_not_rolled_over_does_not_count_a_season_twice():
    games = _games((1, 0), (0, 1), (1, 1))
    assert len(lr.team_history("usa.1", "182", KO, get=lambda c, t, s=None: games)) == 3


def test_espn_is_asked_for_the_us_day_as_well_as_the_utc_day(monkeypatch):
    # 01:30 UTC on the 1st is the evening of the 31st in the US, and filed under it.
    asked = []
    monkeypatch.setattr(lr.espn_stats, "fetch_league", lambda code, dates=None: asked.append((code, dates)) or [dates])
    cache = {}
    ko = datetime(2026, 11, 1, 1, 30, tzinfo=timezone.utc)
    assert lr.espn_around("USA-MLS", ko, cache) == ["20261101", "20261031"]
    assert lr.espn_around("USA-MLS", ko, cache) == ["20261101", "20261031"]
    assert asked == [("usa.1", "20261101"), ("usa.1", "20261031")]


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

    # Each team's form at THIS end: the home side's home games, the away side's away games.
    monkeypatch.setattr(lr, "team_history", lambda code, team_id, kickoff: _games(
        (3, 0, True), (0, 3, False), (3, 0, True), (0, 3, False), (3, 0, True),
        (0, 3, False), (3, 0, True), (0, 3, False), (3, 0, True), (0, 3, False)))
    home, away = lr.forms_for(ev, {})
    assert home.venue_pts5 == 15 and away.venue_pts5 == 0

    # A team ESPN gave no id for has no form; it does not borrow the other's.
    ev.espn.away_id = ""
    assert lr.forms_for(ev, {})[1] is None


# ── rules about "the team" ───────────────────────────────────────────────────

def _spec(**kw):
    return {"market": "1x2", "leagues": None, "fav_status": None, "home_team": None, "away_team": None, **kw}


def test_a_plain_rule_is_its_own_only_leg():
    spec = _spec(side="home", home_last_gd_max=-1)
    assert lr.flat_legs(spec) == [spec]
    assert lr.flat_legs({"market": "ou25", "side": "over"}) == [{"market": "ou25", "side": "over"}]


def test_the_favourite_is_two_rules_and_their_bounds_swap_ends():
    legs = lr.flat_legs(_spec(side="team", fav_status="favorite", team_last_gd_max=-1, opp_winless_run_min=3, odds_max=1.8))
    assert [leg["side"] for leg in legs] == ["home", "away"]
    assert all(leg["fav_status"] == "favorite" and leg["odds_max"] == 1.8 for leg in legs)
    assert legs[0]["home_last_gd_max"] == -1 and legs[0]["away_winless_run_min"] == 3
    assert legs[1]["away_last_gd_max"] == -1 and legs[1]["home_winless_run_min"] == 3
    # Nothing downstream sees the words it does not read.
    assert not any(k.startswith(("team_", "opp_")) for leg in legs for k in leg)


def test_a_named_team_is_either_end_and_the_opponent_is_the_other_team():
    legs = lr.flat_legs(_spec(side="team", team="Hannover"))
    assert [(leg["side"], leg["home_team"], leg["away_team"]) for leg in legs] == [
        ("home", "Hannover", None), ("away", None, "Hannover")]
    legs = lr.flat_legs(_spec(side="opponent", team="Hannover", opp_avg_ga5_min=2))
    assert [(leg["side"], leg["home_team"], leg["away_team"]) for leg in legs] == [
        ("away", "Hannover", None), ("home", None, "Hannover")]
    assert legs[0]["away_avg_ga5_min"] == 2 and legs[1]["home_avg_ga5_min"] == 2
    # A market with no team in it stays as it was, in that team's games.
    legs = lr.flat_legs(_spec(side="draw", team="Hannover", team_rest_days_max=4))
    assert [leg["side"] for leg in legs] == ["draw", "draw"]
    assert legs[0]["home_team"] == "Hannover" and legs[0]["home_rest_days_max"] == 4
    assert legs[1]["away_team"] == "Hannover" and legs[1]["away_rest_days_max"] == 4


def _both_legs(spec, ev):
    return [lr.pick(leg, ev) for leg in lr.flat_legs(spec)]


def test_the_favourite_is_found_at_whichever_end_it_is():
    spec = _spec(side="team", fav_status="favorite")
    # Arsenal are 0.55 to Chelsea's 0.20, at home here and away there.
    assert [p[0]["token_id"] if p[0] else None for p in _both_legs(spec, _view(home="Arsenal", away="Chelsea"))] == ["a1", None]
    assert [p[0]["token_id"] if p[0] else None for p in _both_legs(spec, _view(home="Chelsea", away="Arsenal"))] == [None, "a1"]
    spec = _spec(side="team", fav_status="underdog")
    assert [p[0]["token_id"] if p[0] else None for p in _both_legs(spec, _view(home="Arsenal", away="Chelsea"))] == [None, "c1"]


def test_a_named_team_fires_once_at_either_end_and_never_without_real_sides():
    spec = _spec(side="team", team="Chelsea")
    assert [p[0]["token_id"] if p[0] else None for p in _both_legs(spec, _view(home="Arsenal", away="Chelsea"))] == [None, "c1"]
    assert [p[0]["token_id"] if p[0] else None for p in _both_legs(spec, _view(home="Chelsea", away="Arsenal"))] == ["c1", None]
    # Against a team: its opponent, wherever that is.
    spec = _spec(side="opponent", team="Chelsea")
    assert [p[0]["token_id"] if p[0] else None for p in _both_legs(spec, _view(home="Chelsea", away="Arsenal"))] == ["a1", None]
    # Without ESPN it cannot know which end is which, and skips rather than guess.
    assert all(p[0] is None and "ESPN" in p[2] for p in _both_legs(spec, _view()))
    # Nor does it fire on a game the team is not in.
    assert all(p[0] is None for p in _both_legs(_spec(side="team", team="Everton"), _view(home="Arsenal", away="Chelsea")))


def test_a_draw_in_a_teams_games_needs_that_team_and_buys_the_draw():
    spec = _spec(side="draw", team="Chelsea")
    assert all(lr.needs_sides(leg) for leg in lr.flat_legs(spec))
    picks = _both_legs(spec, _view(home="Chelsea", away="Arsenal"))
    assert [p[0]["token_id"] if p[0] else None for p in picks] == ["d1", None]


def test_a_relative_rules_bound_lands_on_the_team_it_means(monkeypatch):
    # The favourite (Arsenal) is away here, so its last game is the AWAY team's figure.
    spec = _spec(side="team", fav_status="favorite", team_last_gd_max=-1)
    leg = lr.flat_legs(spec)[1]
    home, away = lr.TeamForm(last_gd=3), lr.TeamForm(last_gd=-2)
    assert lr.needs_form(leg) and lr.form_verdict(leg, home, away) is None
    assert "away last_gd" in lr.form_verdict(leg, away, home)


def test_every_lab_league_with_a_current_espn_feed_can_have_its_sides_confirmed():
    # sco.3 and sco.4 answer, with a season that stopped on 2025-09-27.
    assert set(lr.LAB_CODES) - set(lr.ESPN_CODE) == {"SCO-L1", "SCO-L2"}
    assert not set(lr.ESPN_CODE) - set(lr.LAB_CODES)


def test_the_language_is_the_same_in_all_of_its_places():
    # A figure missing from any of these either cannot be asked for, cannot be
    # tested, reads as a broader rule than it is, or trades one.
    site = open(os.path.join(REPO, "site", "app", "lib", "backtest.ts")).read()
    words = open(os.path.join(REPO, "site", "app", "lib", "strategyText.ts")).read()
    prompt = open(os.path.join(REPO, "site", "app", "api", "backtest", "route.ts")).read()
    sql = open(os.path.join(REPO, "db", "083_language_recent_games.sql")).read()
    fields = re.search(r"TEAM_FIGURES = \[(.*?)\] as const", site, re.S).group(1)
    assert sorted(re.findall(r"'(\w+)'", fields)) == sorted(lr.TEAM_FIGURES)
    assert f"RUN_CAP = {lr.RUN_CAP}" in site
    for figure in lr.TEAM_FIGURES:
        assert f"'{figure}'" in words or f"{figure}:" in words, figure
        assert f'"home_{figure}"' in prompt, figure
    for figure in set(lr.TEAM_FIGURES) - {"rest_days", "form_pts5", "avg_tg5"}:
        for side in ("home", "away"):
            assert f"{side}_{figure} " in sql, f"{side}_{figure} column"
            assert f"'{side}_{figure}_min'" in sql and f"'{side}_{figure}_max'" in sql, f"{side}_{figure} bounds"


def test_the_backtest_holds_exactly_the_leagues_the_runner_trades():
    # bt_features is what "all leagues" means in a test. A league in it that the
    # runner does not trade is a bet the backtest counted and no agent can place.
    site = open(os.path.join(REPO, "site", "app", "lib", "backtest.ts")).read()
    sql = open(os.path.join(REPO, "db", "086_more_leagues.sql")).read()
    listed = re.search(r"AND l\.code IN \((.*?)\)", sql, re.S).group(1)
    assert set(re.findall(r"'([A-Z0-9-]+)'", listed)) == set(lr.LAB_CODES)
    assert set(re.findall(r"code: '([A-Z0-9-]+)'", site)) == set(lr.LAB_CODES)


def test_over_under_is_never_traded_where_it_was_never_tested():
    # Football-Data's extra leagues hold a 1X2 close and nothing else. Polymarket lists
    # O/U 2.5 on them, so without this "all leagues" would trade what it never tested.
    spec = {"market": "ou25", "side": "over", "leagues": None}
    assert lr.pick(spec, _view())[0] is not None
    leg, _, why = lr.pick(spec, _view(code="USA-MLS"))
    assert leg is None and "Over/Under" in why
    assert lr.pick({**spec, "leagues": ["ENG-PR", "USA-MLS"]}, _view(code="USA-MLS"))[0] is None
    # A rule that can only ever be that does not run, and says so.
    assert "no Over/Under prices" in lr.run_blocker({**spec, "leagues": ["USA-MLS", "JPN-J1"]})
    assert lr.run_blocker({**spec, "leagues": ["ENG-PR", "USA-MLS"]}) is None
    assert lr.run_blocker(spec) is None
    # The result markets there are what the backtest holds.
    assert lr.run_blocker({"market": "1x2", "side": "draw", "leagues": ["USA-MLS"]}) is None
    site = open(os.path.join(REPO, "site", "app", "lib", "backtest.ts")).read()
    listed = re.search(r"NO_OU_LEAGUES = new Set<string>\(\[(.*?)\]\)", site, re.S).group(1)
    assert set(re.findall(r"'([A-Z0-9-]+)'", listed)) == set(lr.NO_OU_HISTORY)
    assert lr.NO_OU_HISTORY < lr.LAB_CODES and lr.NO_OU_HISTORY <= set(lr.ESPN_CODE)


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


# ── both teams to score and the other goal lines ─────────────────────────────

def _goals_sibling():
    """What Gamma really sends for "- More Markets" (checked live 2026-10-31), with
    the look-alikes that would trade a different bet if they were read as goals."""
    t = "Arsenal vs. Chelsea"
    return {"title": f"{t} - More Markets", "startTime": "2026-09-12T14:00:00Z",
            "tags": [_tag("EPL", "epl")], "markets": [
        _mkt("totals", f"{t}: O/U 1.5", ["Over", "Under"], ["o15", "u15"], ["0.7", "0.3"], "0x15", 1.5),
        # The question says 9.5 and the line says 3.5: the field wins, text is never read.
        _mkt("totals", f"{t}: O/U 9.5", ["Under", "Over"], ["u35", "o35"], ["0.7", "0.3"], "0x35", 3.5),
        _mkt("totals", f"{t}: O/U 4.5", ["Over", "Under"], ["o45", "u45"], ["0.1", "0.9"], "0x45", 4.5),
        _mkt("totals", f"{t}: O/U 2.5", ["Over", "Under"], ["o25", "u25"], ["0.5", "0.5"], "0x25", 2.5),
        # A line no Lab market covers, and a totals market with none at all.
        _mkt("totals", f"{t}: O/U 5.5", ["Over", "Under"], ["o55", "u55"], ["0.1", "0.9"], "0x55", 5.5),
        _mkt("totals", f"{t}: O/U 1.5", ["Over", "Under"], ["bad", "bad2"], ["0.1", "0.9"], "0xbad"),
        # No listed first: the side comes from the label.
        _mkt("both_teams_to_score", f"{t}: Both Teams to Score", ["No", "Yes"], ["bn", "by"], ["0.45", "0.55"], "0xb"),
        # Other families that say the same words, all real sportsMarketTypes.
        _mkt("both_teams_to_score_first_half", f"{t}: Both Teams to Score 1H", ["Yes", "No"], ["fy", "fn"], ["0.1", "0.9"], "0xf"),
        _mkt("first_half_totals", f"{t}: 1H O/U 1.5", ["Over", "Under"], ["h1", "h2"], ["0.3", "0.7"], "0xh", 1.5),
        _mkt("soccer_team_totals", f"{t}: Arsenal O/U 1.5", ["Over", "Under"], ["tt1", "tt2"], ["0.4", "0.6"], "0xtt", 1.5),
        _mkt("spreads", f"{t}: Spread -1.5", ["Arsenal", "Chelsea"], ["s1", "s2"], ["0.4", "0.6"], "0xs", 1.5),
    ]}


def _main_event():
    return {**_event(), "startTime": "2026-09-12T14:00:00Z", "tags": [_tag("EPL", "epl")],
            "markets": [m for m in _event()["markets"] if m["sportsMarketType"] == "moneyline"]}


def _goal_view(code="ENG-PR"):
    merged = lr.fixtures_of([_main_event(), _goals_sibling()])
    assert len(merged) == 1
    return lr.EventView(code=code, title=merged[0]["title"],
                        kickoff=datetime(2026, 9, 12, 14, tzinfo=timezone.utc),
                        markets=lr.parse_markets(merged[0]))


def test_each_goal_market_is_read_from_its_family_and_line_never_the_question():
    m = lr.parse_markets(_goals_sibling())
    assert m["ou15"]["over"]["token_id"] == "o15" and m["ou15"]["under"]["token_id"] == "u15"
    assert m["ou25"]["over"]["token_id"] == "o25"
    # Under listed first, and a question naming another line: read off the fields.
    assert m["ou35"]["over"]["token_id"] == "o35" and m["ou35"]["under"]["token_id"] == "u35"
    assert m["ou45"]["under"]["token_id"] == "u45"
    tokens = {leg["token_id"] for mk in ("ou15", "ou25", "ou35", "ou45", "btts") for leg in m[mk].values()}
    for lookalike in ("fy", "fn", "h1", "h2", "tt1", "tt2", "s1", "s2", "o55", "u55", "bad", "bad2"):
        assert lookalike not in tokens


def test_both_teams_to_score_takes_its_side_from_the_label():
    m = lr.parse_markets(_goals_sibling())
    assert m["btts"]["yes"]["token_id"] == "by" and m["btts"]["no"]["token_id"] == "bn"


def test_a_goal_market_with_labels_it_cannot_read_is_not_a_market():
    bad = {"markets": [_mkt("both_teams_to_score", "x", ["Over", "Under"], ["a", "b"], ["0.5", "0.5"], "0xb"),
                       _mkt("totals", "x", ["Yes", "No"], ["c", "d"], ["0.5", "0.5"], "0xt", 1.5)]}
    m = lr.parse_markets(bad)
    assert m["btts"] is None and m["ou15"] is None


def test_siblings_become_one_fixture_with_every_market():
    halftime = {"title": "Arsenal vs. Chelsea - Halftime Result", "startTime": "2026-09-12T14:00:00Z",
                "tags": [_tag("EPL", "epl")],
                "markets": [_mkt("moneyline", "Will the first half end in a draw?", ["Yes", "No"],
                                 ["ht1", "ht2"], ["0.4", "0.6"], "0xht")]}
    merged = lr.fixtures_of([_main_event(), _goals_sibling(), halftime])
    assert len(merged) == 1
    assert merged[0]["title"] == "Arsenal vs. Chelsea"
    m = lr.parse_markets(merged[0])
    # Both halves of the fixture are there, and the half-time event is not.
    assert m["draw"]["token_id"] == "d1" and m["btts"]["yes"]["token_id"] == "by"


def test_the_same_clubs_on_another_day_stay_another_fixture():
    return_leg = {**_main_event(), "startTime": "2027-01-12T14:00:00Z"}
    assert len(lr.fixtures_of([_main_event(), return_leg])) == 2


def test_one_bet_per_fixture_covers_every_sibling():
    """An agent that bet on the moneyline has bet on this game, whichever sibling its
    bet is on: the ids asked about must span both events."""
    cids = lr.fixture_condition_ids(_goal_view().markets)
    for bought in ("0xa", "0xc", "0xd", "0x15", "0x25", "0x35", "0x45", "0xb"):
        assert bought in cids
    for never in ("0xf", "0xh", "0xtt", "0xs", "0x55", "0xbad"):
        assert never not in cids


def test_the_cycle_asks_already_in_about_the_whole_fixture(monkeypatch):
    """A trade on the moneyline stops a BTTS bet on the same game, though the two
    markets sit on different Polymarket events."""
    asked = []
    spec = {"market": "btts", "side": "yes", "leagues": None}
    never = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not get this far"))  # noqa: E731
    monkeypatch.setattr(lr, "_conn", lambda: object())
    monkeypatch.setattr(lr, "running_strategies", lambda c: [{"id": 7, "name": "x", "theory": None, "spec": spec}])
    monkeypatch.setattr(lr, "fetch_upcoming", lambda now: [_main_event(), _goals_sibling()])
    monkeypatch.setattr(lr, "already_in", lambda c, sid, cids: asked.append(cids) or True)
    monkeypatch.setattr(lr, "_fetch_book", never)
    monkeypatch.setattr(lr, "write_trade", never)
    assert lr.run_once() == 0
    assert len(asked) == 1 and "0xa" in asked[0] and "0xb" in asked[0]


def test_pick_buys_the_token_the_rule_names():
    v = _goal_view()
    for market, side, token, words in (
        ("btts", "yes", "by", "Both teams to score: Yes"),
        ("btts", "no", "bn", "Both teams to score: No"),
        ("ou15", "over", "o15", "Over 1.5"),
        ("ou15", "under", "u15", "Under 1.5"),
        ("ou35", "over", "o35", "Over 3.5"),
        ("ou35", "under", "u35", "Under 3.5"),
        ("ou45", "over", "o45", "Over 4.5"),
        ("ou45", "under", "u45", "Under 4.5"),
    ):
        leg, label, why = lr.pick({"market": market, "side": side, "leagues": None}, v)
        assert leg["token_id"] == token and label == f"{words} — Arsenal vs. Chelsea" and why == "ok"


def test_a_side_the_market_does_not_have_buys_nothing():
    v = _goal_view()
    assert lr.pick({"market": "btts", "side": "over", "leagues": None}, v)[0] is None
    assert lr.pick({"market": "ou15", "side": "yes", "leagues": None}, v)[0] is None
    assert lr.pick({"market": "ou15", "side": "home", "leagues": None}, v)[0] is None


def test_goal_markets_need_no_sides_unless_the_rule_names_one():
    v = _goal_view()  # ESPN confirmed nothing
    assert lr.pick({"market": "btts", "side": "yes", "leagues": None}, v)[0] is not None
    assert lr.pick({"market": "btts", "side": "yes", "leagues": None, "home_team": "Arsenal"}, v)[0] is None
    assert lr.pick({"market": "btts", "side": "yes", "leagues": None, "home_avg_gf5_min": 1.6}, v)[0] is None


def test_a_league_the_replay_never_saw_is_not_traded_on_these_markets():
    for code in ("SCO-CH", "SCO-L1", "SCO-L2"):
        v = _goal_view(code)
        assert lr.pick({"market": "btts", "side": "yes", "leagues": None}, v)[2] == "league"
        assert lr.pick({"market": "ou35", "side": "over", "leagues": [code]}, v)[2] == "league"
        # O/U 2.5 was tested at Pinnacle's close in these leagues, and stays.
        assert lr.pick({"market": "ou25", "side": "over", "leagues": None}, v)[0] is not None


def test_a_favourite_on_a_goals_market_takes_no_games_as_the_replay_does():
    v = _goal_view()
    v.home, v.away = "Arsenal", "Chelsea"
    leg, _, why = lr.pick({"market": "btts", "side": "yes", "leagues": None, "fav_status": "favorite"}, v)
    assert leg is None and why == "favourite filter"


def test_a_rule_about_a_named_team_flattens_to_the_same_goal_market():
    legs = lr.flat_legs({"market": "btts", "side": "yes", "leagues": None, "team": "Arsenal", "team_avg_gf5_min": 2})
    assert [leg.get("side") for leg in legs] == ["yes", "yes"]
    assert legs[0]["home_team"] == "Arsenal" and legs[0]["home_avg_gf5_min"] == 2
    assert legs[1]["away_team"] == "Arsenal" and legs[1]["away_avg_gf5_min"] == 2


def test_the_new_markets_run_and_nba_still_does_not():
    for market, side in (("btts", "yes"), ("btts", "no"), ("ou15", "over"), ("ou35", "under"), ("ou45", "over")):
        assert lr.run_blocker({"market": market, "side": side, "leagues": None}) is None
    assert "NBA" in lr.run_blocker({"market": "nba_ml", "side": "home", "leagues": None})
    assert "NBA" in lr.run_blocker({"market": "ou55", "side": "over", "leagues": None})


def test_settlement_reads_the_winner_flag_of_the_token_bought(monkeypatch):
    """A No on both teams to score: the token bought decides, whichever outcome the
    market lists first. A market with no winner yet stays open."""
    class R:
        def __init__(self, tokens):
            self.tokens = tokens

        def json(self):
            return {"tokens": self.tokens}
    monkeypatch.setattr(lr.requests, "get", lambda *a, **k: R(
        [{"token_id": "bn", "winner": True}, {"token_id": "by", "winner": False}]))
    assert lr._clob_winner("0xb") == {"bn": True, "by": False}
    monkeypatch.setattr(lr.requests, "get", lambda *a, **k: R(
        [{"token_id": "bn", "winner": False}, {"token_id": "by", "winner": False}]))
    assert lr._clob_winner("0xb") is None


def test_the_site_and_the_runner_list_the_same_markets_and_leagues():
    src = open(os.path.join(REPO, "site/app/lib/backtest.ts"), encoding="utf-8").read()
    listed = re.search(r"VENUE_MARKETS = \[(.*?)\]", src).group(1)
    assert set(re.findall(r"'(\w+)'", listed)) == lr.GOAL_MARKETS - {"ou25"}
    held = re.search(r"VENUE_NO_HISTORY = \[(.*?)\]", src, re.S).group(1)
    assert set(re.findall(r"'([\w-]+)'", held)) == lr.NO_VENUE_HISTORY
    sql = open(os.path.join(REPO, "db/085_venue_markets.sql"), encoding="utf-8").read()
    replayed_out = re.search(r"l\.code <> ALL \(ARRAY\[(.*?)\]", sql, re.S).group(1)
    assert set(re.findall(r"'([\w-]+)'", replayed_out)) == lr.NO_VENUE_HISTORY


def test_a_goal_market_on_a_league_with_no_replay_is_refused_and_not_traded():
    assert "never been tested" in lr.run_blocker({"market": "btts", "side": "yes", "leagues": ["JPN-J1"]})
    assert "never been tested" in lr.run_blocker({"market": "ou35", "side": "over", "leagues": ["USA-MLS", "SCO-L1"]})
    assert "never been tested" in lr.run_blocker({"market": "btts", "side": "no", "leagues": ["BRA-SA"]})
    # One league it has seen: the rule runs there, and only there.
    assert lr.run_blocker({"market": "btts", "side": "yes", "leagues": ["JPN-J1", "MEX-LMX"]}) is None
    assert lr.run_blocker({"market": "btts", "side": "yes", "leagues": None}) is None
    # O/U 2.5 has its own list, and J1 is on both.
    assert "Over/Under" in lr.run_blocker({"market": "ou25", "side": "over", "leagues": ["JPN-J1"]})


def test_the_replay_reads_each_market_at_its_own_line():
    sql = open(os.path.join(REPO, "db/085_venue_markets.sql"), encoding="utf-8").read()
    for market, line in (("ou15", "1.5"), ("ou35", "3.5"), ("ou45", "4.5")):
        assert f"WHEN v.line = {line} THEN '{market}'" in sql
        assert lr.LINE_OF[market] == float(line)

