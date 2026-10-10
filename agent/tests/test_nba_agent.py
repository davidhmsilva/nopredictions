"""The parts of nba_agent that invert a bet when they are wrong: which club a
token pays on, which line it is, whether the game is in scope at all, the
de-vig, the EV after the fee, and the grade from the final score."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import nba_agent as na  # noqa: E402
import nba_model as nm  # noqa: E402

KO = datetime.now(timezone.utc) + timedelta(hours=2)
NETS, PISTONS = "Brooklyn Nets", "Detroit Pistons"


def _mkt(fam, q, outs, bid, ask, line=None, liq=50_000):
    return {"sportsMarketType": fam, "question": q, "outcomes": json.dumps(outs),
            "clobTokenIds": json.dumps([f"{q}|0", f"{q}|1"]), "bestBid": bid, "bestAsk": ask,
            "liquidityNum": liq, "line": line, "conditionId": f"c-{q}", "active": True,
            "closed": False, "acceptingOrders": True}


def _event(markets, home_first=False):
    # Polymarket's real shape (nba-det-bkn-2026-03-10): nicknames, lower-case
    # abbreviations, title "Away vs. Home"
    teams = [{"name": "Pistons", "alias": "Pistons", "abbreviation": "det", "ordering": "away"},
             {"name": "Nets", "alias": "Nets", "abbreviation": "bkn", "ordering": "home"}]
    if home_first:
        teams.reverse()
    return {"slug": "nba-det-bkn-2026-10-21", "title": "Pistons vs. Nets",
            "startTime": KO.isoformat(), "teams": teams, "markets": markets}


def _sharp(ml_home=0.30, spread_pt=6.5, total=(221.5, 0.49)):
    """Nets (home) +6.5 underdogs. spread_pt is the HOME team's handicap."""
    a = nm.anchor(give_a=-spread_pt, p_cover_a=0.5, p_win_a=ml_home,
                  total_line=total[0], p_over=total[1])
    return na.Sharp("pinnacle", a, {NETS: (ml_home, ml_home), PISTONS: (1 - ml_home, 1 - ml_home)},
                    {NETS: (spread_pt, 0.5, 0.5), PISTONS: (-spread_pt, 0.5, 0.5)},
                    (total[0], (total[1], total[1]), (1 - total[1], 1 - total[1])), True,
                    datetime.now(timezone.utc))


# ── club names ───────────────────────────────────────────────────────────────

def test_every_feed_spelling_resolves_to_one_club():
    assert len(na.TEAMS) == 30
    for s, want in [("Pistons", PISTONS), ("det", PISTONS), ("LA Clippers", "Los Angeles Clippers"),
                    ("Los Angeles Clippers", "Los Angeles Clippers"), ("lac", "Los Angeles Clippers"),
                    ("Lakers", "Los Angeles Lakers"), ("GS", "Golden State Warriors"),
                    ("gsw", "Golden State Warriors"), ("NO", "New Orleans Pelicans"),
                    ("UTAH", "Utah Jazz"), ("WSH", "Washington Wizards"), ("was", "Washington Wizards"),
                    ("Trail Blazers", "Portland Trail Blazers"), ("76ers", "Philadelphia 76ers")]:
        assert na.club(s) == want, s


def test_no_substring_matching():
    # "Los Angeles" names two clubs; "New York" only one but is still a place
    for s in ("Los Angeles", "LA", "Angeles Lakers", "Boston", "Nets Brooklyn", ""):
        assert na.club(s) is None, s


def test_a_spelling_naming_two_clubs_raises(monkeypatch):
    monkeypatch.setitem(na.TEAMS, "Seattle SuperSonics", ("Sonics", "lal"))
    with pytest.raises(ValueError):
        na._build_canon()


# ── the event ────────────────────────────────────────────────────────────────

def test_game_uses_pm_home_designation_not_title_order():
    g = na.game_from_event(_event([], home_first=True))
    assert g.home == NETS and g.away == PISTONS
    assert g.team("Pistons") == PISTONS and g.team("bkn") == NETS and g.team("Brooklyn Nets") == NETS


def test_unknown_club_fails_closed():
    ev = _event([])
    ev["teams"][0] = {"name": "Team LeBron", "alias": "Team LeBron", "abbreviation": "lbj",
                      "ordering": "away"}
    assert na.game_from_event(ev) is None


def test_club_whose_name_and_abbreviation_disagree_fails_closed():
    ev = _event([])
    ev["teams"][0]["abbreviation"] = "bos"
    assert na.game_from_event(ev) is None


def test_non_game_slugs_are_ignored():
    ev = _event([])
    ev["slug"] = "nba-will-anthony-davis-be-traded-by-next-season"
    assert na.game_from_event(ev) is None


# ── candidates ───────────────────────────────────────────────────────────────

def test_spread_tokens_map_to_the_named_team_and_its_opponent():
    g = na.game_from_event(_event([_mkt("spreads", "Spread: Pistons (-4.5)",
                                        ["Pistons", "Nets"], 0.55, 0.56, line=-4.5)]))
    cs = {c.side: c for c in na.candidates(g, _sharp())}
    det, bkn = cs[PISTONS], cs[NETS]
    assert det.line == -4.5 and bkn.line == 4.5
    assert det.ask == pytest.approx(0.56) and bkn.ask == pytest.approx(0.45)
    # Pistons expected to win by ~6.5: laying 4.5 is better than even
    assert det.fair_raw > 0.5 > bkn.fair_raw
    assert det.fair_raw + bkn.fair_raw == pytest.approx(1.0)


def test_spread_named_for_the_underdog_is_read_the_right_way_round():
    # Polymarket lists "Spread: 76ers (-1.5)" AND "Spread: Knicks (-1.5)" on one game
    g = na.game_from_event(_event([_mkt("spreads", "Spread: Nets (-1.5)",
                                        ["Nets", "Pistons"], 0.20, 0.21, line=-1.5)]))
    cs = {c.side: c for c in na.candidates(g, _sharp())}
    assert cs[NETS].line == -1.5 and cs[PISTONS].line == 1.5
    assert cs[NETS].fair_raw < 0.35          # a 6.5-point dog winning by 2+


def test_spread_with_line_field_disagreeing_with_question_is_dropped():
    g = na.game_from_event(_event([_mkt("spreads", "Spread: Pistons (-4.5)",
                                        ["Pistons", "Nets"], 0.55, 0.56, line=-3.5)]))
    assert na.candidates(g, _sharp()) == []


def test_spread_whose_first_outcome_is_not_the_named_team_is_dropped():
    g = na.game_from_event(_event([_mkt("spreads", "Spread: Pistons (-4.5)",
                                        ["Nets", "Pistons"], 0.55, 0.56, line=-4.5)]))
    assert na.candidates(g, _sharp()) == []


def test_exact_half_point_line_uses_the_book_price_not_the_model():
    g = na.game_from_event(_event([_mkt("totals", "Pistons vs. Nets: O/U 221.5",
                                        ["Over", "Under"], 0.47, 0.48, line=221.5)]))
    cs = {c.side: c for c in na.candidates(g, _sharp(total=(221.5, 0.49)))}
    assert cs["Over"].source == "sharp_exact" and cs["Over"].fair == pytest.approx(0.49)
    assert cs["Under"].fair == pytest.approx(0.51)


def test_model_lines_carry_a_haircut_growing_with_distance():
    g = na.game_from_event(_event([
        _mkt("totals", "Pistons vs. Nets: O/U 223.5", ["Over", "Under"], 0.44, 0.45, line=223.5),
        _mkt("totals", "Pistons vs. Nets: O/U 231.5", ["Over", "Under"], 0.29, 0.30, line=231.5)]))
    cs = {(c.side, c.line): c for c in na.candidates(g, _sharp())}
    near, far = cs[("Over", 223.5)], cs[("Over", 231.5)]
    assert near.source == far.source == "sharp_model"
    assert (far.fair_raw - far.fair) > (near.fair_raw - near.fair) > 0


def test_far_alternate_spreads_are_not_priced():
    g = na.game_from_event(_event([_mkt("spreads", "Spread: Pistons (-20.5)",
                                        ["Pistons", "Nets"], 0.10, 0.11, line=-20.5)]))
    assert na.candidates(g, _sharp()) == []


def test_half_and_team_markets_are_never_priced():
    g = na.game_from_event(_event([
        _mkt("first_half_totals", "Pistons vs. Nets: 1H O/U 110.5", ["Over", "Under"], 0.49, 0.50, line=110.5),
        _mkt("totals", "Pistons vs. Nets: 1H O/U 110.5", ["Over", "Under"], 0.49, 0.50, line=110.5),
        _mkt("first_half_spreads", "1H Spread: Pistons (-3.5)", ["Pistons", "Nets"], 0.49, 0.50, line=-3.5),
        _mkt("points", "Cade Cunningham: Points O/U 26.5", ["Over", "Under"], 0.49, 0.50, line=26.5)]))
    assert na.candidates(g, _sharp()) == []


def test_wide_or_thin_books_are_gated():
    g = na.game_from_event(_event([
        _mkt("moneyline", "Pistons vs. Nets", ["Pistons", "Nets"], 0.65, 0.71),
        _mkt("totals", "Pistons vs. Nets: O/U 221.5", ["Over", "Under"], 0.47, 0.48,
             line=221.5, liq=500)]))
    assert na.candidates(g, _sharp()) == []


def test_moneyline_needs_both_teams_resolved():
    g = na.game_from_event(_event([_mkt("moneyline", "Pistons vs. Nets",
                                        ["Pistons", "Knicks"], 0.69, 0.70)]))
    assert na.candidates(g, _sharp()) == []


def test_moneyline_prices_each_club_from_its_own_sharp_price():
    g = na.game_from_event(_event([_mkt("moneyline", "Pistons vs. Nets",
                                        ["Pistons", "Nets"], 0.69, 0.70)]))
    cs = {c.side: c for c in na.candidates(g, _sharp(ml_home=0.30))}
    assert cs[PISTONS].fair == pytest.approx(0.70) and cs[NETS].fair == pytest.approx(0.30)
    assert cs[NETS].ask == pytest.approx(0.31)


# ── the sharp feed ───────────────────────────────────────────────────────────

def test_sharp_for_matches_full_names_against_pm_nicknames():
    g = na.game_from_event(_event([]))
    cache = {"fetched_at_dt": datetime.now(timezone.utc), "data": [{
        "home_team": "Brooklyn Nets", "away_team": "Detroit Pistons",
        "commence_time": KO.isoformat(),
        "bookmakers": [{"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [{"name": "Brooklyn Nets", "price": 3.30},
                                        {"name": "Detroit Pistons", "price": 1.36}]},
            {"key": "spreads", "outcomes": [{"name": "Brooklyn Nets", "price": 1.93, "point": 7.5},
                                            {"name": "Detroit Pistons", "price": 1.93, "point": -7.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 1.92, "point": 221.5},
                                           {"name": "Under", "price": 1.94, "point": 221.5}]}]}]}]}
    sh = na.sharp_for(g, cache)
    assert sh is not None and sh.book == "pinnacle" and sh.model_ok
    assert sh.anchor.mu == pytest.approx(-7.5, abs=0.6)        # Nets, the home side, are dogs
    assert sh.spread[PISTONS][0] == -7.5 and sh.ml[NETS][0] < 0.3


def test_sharp_for_refuses_a_different_game_of_the_same_clubs():
    g = na.game_from_event(_event([]))
    far = (KO + timedelta(days=2)).isoformat()
    cache = {"fetched_at_dt": datetime.now(timezone.utc), "data": [{
        "home_team": "Brooklyn Nets", "away_team": "Detroit Pistons", "commence_time": far,
        "bookmakers": []}]}
    assert na.sharp_for(g, cache) is None


# ── scope: preseason is never bet ────────────────────────────────────────────

def _sched(st, start=KO, teams=(NETS, PISTONS)):
    return {"20261021": [{"teams": frozenset(teams), "start": start, "season_type": st,
                          "status": "STATUS_SCHEDULED", "scores": {}}]}


@pytest.mark.parametrize("st", [2, 3, 5])
def test_regular_season_playin_and_playoffs_are_in_scope(st):
    g = na.game_from_event(_event([]))
    assert na.season_type(g, _sched(st)) in na.BET_SEASON_TYPES


def test_preseason_and_unplaceable_games_are_out_of_scope():
    g = na.game_from_event(_event([]))
    assert na.season_type(g, _sched(1)) == 1
    assert na.season_type(g, None) is None                               # ESPN down
    assert na.season_type(g, _sched(2, teams=(NETS, "Boston Celtics"))) is None
    assert na.season_type(g, _sched(2, start=KO + timedelta(days=3))) is None


def test_espn_day_maps_display_names_and_reads_season_type(monkeypatch):
    payload = {"events": [{"date": "2026-10-21T23:30Z", "season": {"type": 2},
                           "competitions": [{"status": {"type": {"name": "STATUS_SCHEDULED"}},
                                             "competitors": [
        {"homeAway": "home", "team": {"displayName": "LA Clippers"}, "score": "0"},
        {"homeAway": "away", "team": {"displayName": "Utah Jazz"}, "score": "0"}]}]}]}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: R())
    rows = na._espn_day("20261021")
    assert rows[0]["teams"] == frozenset(("Los Angeles Clippers", "Utah Jazz"))
    assert rows[0]["season_type"] == 2


def test_espn_down_is_none_not_an_empty_day(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(na.requests, "get", boom)
    assert na._espn_day("20261021") is None
    assert na.espn_schedule([na.game_from_event(_event([]))]) is None


# ── the rule and the credits ─────────────────────────────────────────────────

def _cand(ev, src="sharp_exact"):
    return na.Cand("totals", 221.5, "Over", "q", "c", "t", 0.47, 0.48, 50_000, 0.5, 0.5, src, ev=ev)


def test_decide_edge_needs_a_fresh_sharp_price_and_the_threshold():
    assert na.decide(_cand(1.6), fresh=True, mins=300) == "edge"
    assert na.decide(_cand(1.6), fresh=False, mins=300) is None
    assert na.decide(_cand(2.9, "sharp_model"), fresh=True, mins=300) is None
    assert na.decide(_cand(3.1, "sharp_model"), fresh=True, mins=300) == "edge"
    assert na.decide(_cand(9.0, "pm_mid"), fresh=True, mins=300) is None


def test_decide_forces_a_bet_inside_the_window_whatever_its_sign():
    assert na.decide(_cand(-3.0), fresh=False, mins=35) == "forced"
    assert na.decide(None, fresh=True, mins=10) is None


def test_credits_are_rationed_for_the_shared_key():
    assert na.may_fetch("edge", 400) and na.may_fetch("force", 100)
    assert not na.may_fetch("edge", 120)         # under the reserve: forced/close only
    assert na.may_fetch("close", 120)
    assert not na.may_fetch("force", 50)         # under the floor: nothing at all
    assert not na.may_fetch(None, 400)
    assert na.may_fetch("edge", None)            # no cache anywhere yet


def test_pool_remaining_reads_the_latest_fetch_of_any_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(na, "HERE", str(tmp_path))
    old, new = "2026-10-06T10:00:00+00:00", "2026-10-06T12:00:00+00:00"
    (tmp_path / ".nfl_odds_cache.json").write_text(json.dumps({"fetched_at": new, "remaining": 180}))
    (tmp_path / ".nba_odds_cache.json").write_text(json.dumps({"fetched_at": old, "remaining": 209}))
    assert na.pool_remaining() == 180


# ── prices ───────────────────────────────────────────────────────────────────

def test_devig_both_methods_sum_to_one_and_agree_on_even_prices():
    (p1, p2), (w1, w2) = na.devig(1.95, 1.95)
    assert p1 == pytest.approx(0.5) and w1 == pytest.approx(0.5)
    (p1, p2), (w1, w2) = na.devig(1.38, 3.27)
    assert p1 + p2 == pytest.approx(1) and w1 + w2 == pytest.approx(1, abs=1e-6)
    assert w1 > p1


def test_ev_includes_the_taker_fee():
    assert na.ev_pct(0.5, 0.5) == pytest.approx(100 * (1 / 1.025 - 1))
    assert na.ev_pct(0.52, 0.50) > 0 > na.ev_pct(0.505, 0.50)


def test_model_anchor_reproduces_the_book():
    a = nm.anchor(give_a=7.5, p_cover_a=0.5, p_win_a=0.735, total_line=221.5, p_over=0.49)
    assert abs(a.ml_resid_pp) < 0.5
    assert nm.win_prob(a.mu, a.sigma) == pytest.approx(0.735, abs=0.005)
    assert nm.over_prob(a.T, 221.5) == pytest.approx(0.49, abs=0.002)


def test_no_ties_in_the_nba():
    d = nm.margin_dist()
    assert d.factor[d.support == 0][0] == 0.0
    # a pick'em is exactly a coin flip and the two sides sum to one
    assert nm.win_prob(0.0) == pytest.approx(0.5, abs=1e-9)
    assert nm.win_prob(4.0) + nm.win_prob(-4.0) == pytest.approx(1.0)


# ── provisional grading from the final score ─────────────────────────────────

@pytest.mark.parametrize("mt,line,side,hs,as_,want", [
    ("totals", 221.5, "Over", 100, 138, "won"),          # DET 138 @ BKN 100, 2026-03-10
    ("totals", 240.5, "Over", 100, 138, "lost"),
    ("totals", 240.5, "Under", 100, 138, "won"),
    ("spreads", -14.5, PISTONS, 100, 138, "won"),        # PM resolved Pistons -14.5 YES
    ("spreads", -38.5, PISTONS, 100, 138, "lost"),       # … and -38.5 NO
    ("spreads", 14.5, NETS, 100, 138, "lost"),
    ("spreads", 38.5, NETS, 100, 138, "won"),            # the home side, graded from its own score
    ("moneyline", None, PISTONS, 100, 138, "won"),
    ("moneyline", None, NETS, 100, 138, "lost"),
])
def test_grade_scores_the_side_that_was_bought(mt, line, side, hs, as_, want):
    got = na.grade(mt, line, side, NETS, PISTONS, hs, as_)
    assert got is not None and got[0] == want


def test_grade_fails_closed_on_a_side_it_cannot_place():
    assert na.grade("spreads", 6.5, "Boston Celtics", NETS, PISTONS, 100, 138) is None
    assert na.grade("moneyline", None, "Team LeBron", NETS, PISTONS, 100, 138) is None
    assert na.grade("totals", None, "Under", NETS, PISTONS, 100, 138) is None
    assert na.grade("first_half_totals", 110.5, "Over", NETS, PISTONS, 100, 138) is None


def test_espn_finals_keeps_only_finished_games_keyed_on_the_pair(monkeypatch):
    def ev(status, home, away, hs, as_):
        return {"date": "2026-10-22T02:00Z", "season": {"type": 2}, "competitions": [{
            "status": {"type": {"name": status}}, "competitors": [
                {"homeAway": "home", "team": {"displayName": home}, "score": hs},
                {"homeAway": "away", "team": {"displayName": away}, "score": as_}]}]}
    payload = {"events": [ev("STATUS_FINAL", "LA Clippers", "Utah Jazz", "112", "108"),
                          # tied at the end of regulation, heading to overtime
                          ev("STATUS_END_PERIOD", "Brooklyn Nets", "Detroit Pistons", "101", "101")]}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: R())
    finals = na._espn_finals([datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)])
    assert finals == {frozenset(("Los Angeles Clippers", "Utah Jazz")):
                      {"Los Angeles Clippers": 112, "Utah Jazz": 108}}
