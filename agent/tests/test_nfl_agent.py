"""The parts of nfl_agent that invert a bet when they are wrong: which team a
token pays on, which line it is, the de-vig, and the EV after the fee."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import nfl_agent as na  # noqa: E402
import nfl_model as nm  # noqa: E402

KO = datetime.now(timezone.utc) + timedelta(hours=2)


def _mkt(fam, q, outs, bid, ask, line=None, liq=50_000):
    return {"sportsMarketType": fam, "question": q, "outcomes": json.dumps(outs),
            "clobTokenIds": json.dumps([f"{q}|0", f"{q}|1"]), "bestBid": bid, "bestAsk": ask,
            "liquidityNum": liq, "line": line, "conditionId": f"c-{q}", "active": True,
            "closed": False, "acceptingOrders": True}


def _event(markets, home_first=False):
    teams = [{"name": "Washington Commanders", "alias": "Commanders", "abbreviation": "was",
              "ordering": "away"},
             {"name": "Philadelphia Eagles", "alias": "Eagles", "abbreviation": "phi",
              "ordering": "home"}]
    if home_first:
        teams.reverse()
    return {"slug": "nfl-was-phi-2026-09-13", "title": "Commanders vs. Eagles",
            "startTime": KO.isoformat(), "teams": teams, "markets": markets}


def _sharp(ml_home=0.70, spread_pt=-6.0, total=(44.5, 0.49)):
    home, away = "Philadelphia Eagles", "Washington Commanders"
    a = nm.anchor(give_a=-spread_pt, p_cover_a=0.5, p_win_a=ml_home,
                  total_line=total[0], p_over=total[1])
    return na.Sharp("pinnacle", a, {home: (ml_home, ml_home), away: (1 - ml_home, 1 - ml_home)},
                    {home: (spread_pt, 0.5, 0.5), away: (-spread_pt, 0.5, 0.5)},
                    (total[0], (total[1], total[1]), (1 - total[1], 1 - total[1])), True,
                    datetime.now(timezone.utc))


def test_game_uses_pm_home_designation_not_title_order():
    g = na.game_from_event(_event([], home_first=True))
    assert g.home == "Philadelphia Eagles" and g.away == "Washington Commanders"
    assert g.team("Eagles") == "Philadelphia Eagles" and g.team("was") == "Washington Commanders"


def test_non_game_slugs_are_ignored():
    ev = _event([])
    ev["slug"] = "nfl-was-phi-2026-09-13-player-props"
    assert na.game_from_event(ev) is None


def test_spread_tokens_map_to_the_named_team_and_its_opponent():
    g = na.game_from_event(_event([_mkt("spreads", "Spread: Eagles (-4.5)",
                                        ["Eagles", "Commanders"], 0.55, 0.56, line=-4.5)]))
    cs = {c.side: c for c in na.candidates(g, _sharp())}
    eagles, wash = cs["Philadelphia Eagles"], cs["Washington Commanders"]
    assert eagles.line == -4.5 and wash.line == 4.5
    assert eagles.ask == pytest.approx(0.56) and wash.ask == pytest.approx(0.45)
    # Eagles expected to win by ~6-7: laying 4.5 is better than even
    assert eagles.fair_raw > 0.5 > wash.fair_raw
    assert eagles.fair_raw + wash.fair_raw == pytest.approx(1.0)


def test_spread_with_line_field_disagreeing_with_question_is_dropped():
    g = na.game_from_event(_event([_mkt("spreads", "Spread: Eagles (-4.5)",
                                        ["Eagles", "Commanders"], 0.55, 0.56, line=-3.5)]))
    assert na.candidates(g, _sharp()) == []


def test_spread_whose_first_outcome_is_not_the_named_team_is_dropped():
    g = na.game_from_event(_event([_mkt("spreads", "Spread: Eagles (-4.5)",
                                        ["Commanders", "Eagles"], 0.55, 0.56, line=-4.5)]))
    assert na.candidates(g, _sharp()) == []


def test_exact_half_point_line_uses_the_book_price_not_the_model():
    g = na.game_from_event(_event([_mkt("totals", "Commanders vs. Eagles: O/U 44.5",
                                        ["Over", "Under"], 0.47, 0.48, line=44.5)]))
    cs = {c.side: c for c in na.candidates(g, _sharp(total=(44.5, 0.49)))}
    assert cs["Over"].source == "sharp_exact" and cs["Over"].fair == pytest.approx(0.49)
    assert cs["Under"].fair == pytest.approx(0.51)


def test_model_lines_carry_a_haircut_growing_with_distance():
    g = na.game_from_event(_event([
        _mkt("totals", "Commanders vs. Eagles: O/U 45.5", ["Over", "Under"], 0.44, 0.45, line=45.5),
        _mkt("totals", "Commanders vs. Eagles: O/U 51.5", ["Over", "Under"], 0.29, 0.30, line=51.5)]))
    cs = {(c.side, c.line): c for c in na.candidates(g, _sharp())}
    near, far = cs[("Over", 45.5)], cs[("Over", 51.5)]
    assert near.source == far.source == "sharp_model"
    assert (far.fair_raw - far.fair) > (near.fair_raw - near.fair) > 0


def test_quarter_and_team_totals_are_never_priced():
    g = na.game_from_event(_event([
        _mkt("totals", "Commanders vs. Eagles: 1H O/U 22.5", ["Over", "Under"], 0.49, 0.50, line=22.5),
        _mkt("team_totals", "Eagles Team Total: O/U 24.5", ["Over", "Under"], 0.50, 0.51, line=24.5),
        _mkt("q1_spreads", "1Q Spread: Eagles (-0.5)", ["Eagles", "Commanders"], 0.49, 0.50, line=-0.5)]))
    assert na.candidates(g, _sharp()) == []


def test_wide_or_thin_books_are_gated():
    g = na.game_from_event(_event([
        _mkt("moneyline", "Commanders vs. Eagles", ["Commanders", "Eagles"], 0.25, 0.31),
        _mkt("totals", "Commanders vs. Eagles: O/U 44.5", ["Over", "Under"], 0.47, 0.48,
             line=44.5, liq=500)]))
    assert na.candidates(g, _sharp()) == []


def test_moneyline_needs_both_teams_resolved():
    g = na.game_from_event(_event([_mkt("moneyline", "Commanders vs. Eagles",
                                        ["Commanders", "Giants"], 0.30, 0.31)]))
    assert na.candidates(g, _sharp()) == []


def test_devig_both_methods_sum_to_one_and_agree_on_even_prices():
    (p1, p2), (w1, w2) = na.devig(1.95, 1.95)
    assert p1 == pytest.approx(0.5) and w1 == pytest.approx(0.5)
    (p1, p2), (w1, w2) = na.devig(1.38, 3.27)
    assert p1 + p2 == pytest.approx(1) and w1 + w2 == pytest.approx(1, abs=1e-6)
    assert w1 > p1          # power gives the favourite more than proportional


def test_ev_includes_the_taker_fee():
    # fair == ask loses exactly the fee
    assert na.ev_pct(0.5, 0.5) == pytest.approx(100 * (1 / 1.025 - 1))
    assert na.ev_pct(0.52, 0.50) > 0 > na.ev_pct(0.505, 0.50)


def test_model_anchor_reproduces_the_book():
    a = nm.anchor(give_a=6.0, p_cover_a=0.505, p_win_a=0.703, total_line=44.5, p_over=0.49)
    assert abs(a.ml_resid_pp) < 0.5
    assert nm.win_prob(a.mu, a.sigma) == pytest.approx(0.703, abs=0.005)
    assert nm.over_prob(a.T, 44.5) == pytest.approx(0.49, abs=0.002)


def test_key_numbers_make_3_worth_more_than_4():
    # at a pick'em, moving from +2.5 to +3.5 buys far more than +3.5 to +4.5
    v = lambda give: nm.cover_prob(0.0, give)              # noqa: E731
    assert (v(-3.5) - v(-2.5)) > 2 * (v(-4.5) - v(-3.5))
