"""The parts of unl_agent that invert a bet when they are wrong: which team a
token pays on, which line, which sibling event counts, the model the
alternates are priced off, and the grading."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import soccer_line_model as sm  # noqa: E402
import unl_agent as ua  # noqa: E402

NOW = datetime.now(timezone.utc)
KO = NOW + timedelta(hours=2)
TAGS = [{"slug": "soccer", "label": "Soccer"}, {"slug": "uefa-nations-league", "label": "UEFA Nations League"}]


def _mkt(fam, q, outs, bid, ask, line=None, liq=50_000):
    return {"sportsMarketType": fam, "question": q, "outcomes": json.dumps(outs),
            "clobTokenIds": json.dumps([f"{q}|0", f"{q}|1"]), "bestBid": bid, "bestAsk": ask,
            "liquidityNum": liq, "line": line, "conditionId": f"c-{q}", "active": True,
            "closed": False, "acceptingOrders": True}


def _events():
    main = {"slug": "unl-esp-sui-2026-09-27", "title": "Spain vs. Switzerland",
            "startTime": KO.isoformat(), "tags": TAGS, "markets": [
                _mkt("moneyline", "Will Spain win on 2026-09-27?", ["Yes", "No"], 0.60, 0.61),
                _mkt("moneyline", "Will Spain vs. Switzerland end in a draw?", ["Yes", "No"], 0.23, 0.24),
                _mkt("moneyline", "Will Switzerland win on 2026-09-27?", ["Yes", "No"], 0.15, 0.16),
            ]}
    more = {"slug": "unl-esp-sui-2026-09-27-more-markets", "title": "Spain vs. Switzerland - More Markets",
            "startTime": KO.isoformat(), "tags": TAGS, "markets": [
                _mkt("totals", "Spain vs. Switzerland: O/U 2.5", ["Over", "Under"], 0.51, 0.52, 2.5),
                _mkt("totals", "Spain vs. Switzerland: O/U 3.5", ["Over", "Under"], 0.29, 0.30, 3.5),
                _mkt("spreads", "Spread: Spain (-1.5)", ["Spain", "Switzerland"], 0.36, 0.37, -1.5),
                _mkt("both_teams_to_score", "Spain vs. Switzerland: Both Teams to Score", ["Yes", "No"], 0.48, 0.49),
                _mkt("total_corners", "Spain vs. Switzerland: O/U 9.5 Corners", ["Over", "Under"], 0.4, 0.41, 9.5),
            ]}
    ht = {"slug": "unl-esp-sui-2026-09-27-halftime-result", "title": "Spain vs. Switzerland - Halftime Result",
          "startTime": KO.isoformat(), "tags": TAGS, "markets": [
              _mkt("moneyline", "Will Spain vs. Switzerland end in a draw?", ["Yes", "No"], 0.40, 0.41)]}
    club = {"slug": "epl-ars-che-2026-09-27", "title": "Arsenal vs. Chelsea",
            "startTime": KO.isoformat(), "tags": [{"slug": "epl"}], "markets": []}
    return [main, more, ht, club]


def _game():
    games = ua.games_from_events(_events(), NOW)
    assert len(games) == 1
    return games[0]


def _odds_event(home="Switzerland", away="Spain"):
    """The feed with the teams the OTHER way round from PM's title — pricing must not care."""
    def bk(key, h, d, a, tot=2.5, o=1.95, u=1.95):
        return {"key": key, "markets": [
            {"key": "h2h", "outcomes": [{"name": home, "price": h}, {"name": "Draw", "price": d},
                                        {"name": away, "price": a}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": o, "point": tot},
                                           {"name": "Under", "price": u, "point": tot}]},
            {"key": "spreads", "outcomes": [{"name": home, "price": 1.9, "point": 1.5},
                                            {"name": away, "price": 2.0, "point": -1.5}]}]}
    return {"home_team": home, "away_team": away, "commence_time": KO.isoformat(),
            "bookmakers": [bk("pinnacle", 6.4, 4.2, 1.60)]}


def _cache(ev):
    return {"data": [ev], "fetched_at_dt": NOW, "remaining": 400}


# ── grouping and filters ─────────────────────────────────────────────────────

def test_only_main_and_more_markets_are_read():
    g = _game()
    qs = [m["question"] for m in g.markets]
    assert g.slug == "unl-esp-sui-2026-09-27"
    assert qs.count("Will Spain vs. Switzerland end in a draw?") == 1    # not the HT one
    assert any("O/U 3.5" in q for q in qs)


def test_concacaf_is_not_the_nations_league():
    assert not ua.is_unl({"tags": [{"slug": "concacaf-nations-league"}]})
    assert ua.is_unl({"tags": [{"label": "UEFA Nations League"}]})
    assert not ua.is_unl({"tags": [{"slug": "epl"}], "slug": "epl-x-y"})


def test_names_fold_across_sources():
    assert ua.canon("Türkiye") == ua.canon("Turkey")
    assert ua.canon("Czech Republic") == ua.canon("Czechia")
    assert ua.canon("Bosnia & Herzegovina") == ua.canon("Bosnia and Herzegovina")
    assert ua.canon("Northern Ireland") != ua.canon("Republic of Ireland")
    assert ua.canon("Iceland") == "iceland"            # the word 'and' only, never the letters


# ── the model ────────────────────────────────────────────────────────────────

def test_model_reproduces_the_book():
    a = sm.fit(0.62, 0.15, 2.5, 0.52)
    g = a.grid()
    h, d, aw = sm.outcome_probs(g)
    assert abs(h - 0.62) < 1e-4 and abs(aw - 0.15) < 1e-4 and abs(sm.over_prob(g, 2.5) - 0.52) < 1e-4
    assert a.lh > a.la
    assert abs(sm.cover_prob(g, -1.5, True) + sm.cover_prob(g, 1.5, False) - 1) < 1e-9
    assert sm.over_prob(g, 3.5) < sm.over_prob(g, 2.5) < sm.over_prob(g, 1.5)


def test_model_without_total_holds_rho():
    a = sm.fit(0.45, 0.25)
    assert a.rho == sm.RHO_DEFAULT and a.resid_pp < 0.01


# ── the sharp line and candidates ────────────────────────────────────────────

def test_sharp_orients_by_feed_not_title():
    g = _game()
    sh = ua.sharp_for(g, _cache(_odds_event()))
    assert sh.home == "Switzerland" and sh.away == "Spain"
    assert sh.x12["Spain"][0] > 0.55                    # Spain is the favourite whichever way round
    assert sh.anchor.la > sh.anchor.lh                  # away = Spain scores more


def test_unmatched_team_returns_no_sharp():
    assert ua.sharp_for(_game(), _cache(_odds_event(home="Portugal"))) is None


def _set_total(ev, point, over, under):
    for m in ev["bookmakers"][0]["markets"]:
        if m["key"] == "totals":
            m["outcomes"] = [{"name": "Over", "price": over, "point": point},
                             {"name": "Under", "price": under, "point": point}]
    return ev


def test_asian_total_anchors_but_never_prices_exactly():
    sh = ua.sharp_for(_game(), _cache(_set_total(_odds_event(), 2.75, 2.0, 1.90)))
    assert sh.total is None and sh.total_line == 2.75 and sh.goals_ok
    assert sh.anchor.rho_fitted and sh.anchor.resid_pp < 0.05
    by = {c.label: c for c in ua.candidates(_game(), sh)}
    assert by["Over 2.5"].source == "sharp_model"


def test_asian_total_moves_the_goal_count():
    """The 2026-09-28 bug: a 2.75/3.0 total was dropped and the goals came from
    the 1X2 alone. The same 1X2 with a higher Asian total must price Over 2.5 higher."""
    lo = ua.sharp_for(_game(), _cache(_set_total(_odds_event(), 2.25, 1.95, 1.95)))
    hi = ua.sharp_for(_game(), _cache(_set_total(_odds_event(), 3.0, 1.95, 1.95)))
    o_lo = sm.over_prob(lo.anchor.grid(), 2.5)
    o_hi = sm.over_prob(hi.anchor.grid(), 2.5)
    assert o_hi > o_lo + 0.08
    assert 0.40 < o_lo < 0.52 and 0.55 < o_hi < 0.68


def test_no_total_prices_only_the_1x2():
    ev = _odds_event()
    ev["bookmakers"][0]["markets"] = [m for m in ev["bookmakers"][0]["markets"] if m["key"] != "totals"]
    sh = ua.sharp_for(_game(), _cache(ev))
    assert not sh.goals_ok
    cs = ua.candidates(_game(), sh)
    assert all(c.source != "sharp_model" for c in cs)
    assert any(c.source == "sharp_exact" and c.family == "moneyline" for c in cs)


def test_asian_ev():
    g = sm.fit(0.45, 0.28, 2.5, 0.5).grid()
    # a whole line pushes: at fair odds the EV is zero by construction of those odds
    win = sum(p for i, r in enumerate(g) for j, p in enumerate(r) if i + j > 3)
    lose = sum(p for i, r in enumerate(g) for j, p in enumerate(r) if i + j < 3)
    assert sm.asian_over_ev(g, 3.0, 1 + lose / win) == pytest.approx(0, abs=1e-12)
    # a quarter line is the average of its two halves
    assert sm.asian_over_ev(g, 2.75, 2.0) == pytest.approx(
        0.5 * sm.asian_over_ev(g, 2.5, 2.0) + 0.5 * sm.asian_over_ev(g, 3.0, 2.0))


def test_candidates_price_every_family_on_the_right_side():
    g = _game()
    sh = ua.sharp_for(g, _cache(_odds_event()))
    cs = ua.candidates(g, sh)
    by = {c.label: c for c in cs}
    # 1X2 legs are the book's own price
    assert by["Spain win — Yes"].source == "sharp_exact"
    assert abs(by["Spain win — Yes"].fair - min(sh.x12["Spain"])) < 1e-9
    assert abs(by["Draw — No"].fair - (1 - max(sh.x12["Draw"]))) < 1e-9
    # main total exact, the alternate off the model with a haircut
    assert by["Over 2.5"].source == "sharp_exact"
    assert by["Over 3.5"].source == "sharp_model" and by["Over 3.5"].fair < by["Over 3.5"].fair_raw
    # Pinnacle quotes Spain -1.5 exactly
    assert by["Spain -1.5"].source == "sharp_exact"
    assert by["Switzerland +1.5"].source == "sharp_exact"
    assert "BTTS — Yes" in by
    assert not any("Corner" in c.question for c in cs)
    # the No leg of a Yes/No trades at the mirror of the book
    assert abs(by["Spain win — No"].ask - (1 - 0.60)) < 1e-9


def test_spread_side_follows_the_outcome_label():
    g = _game()
    sh = ua.sharp_for(g, _cache(_odds_event()))
    # drop Pinnacle's spread so the model prices it: Spain -1.5 must be < 0.5 and
    # Switzerland +1.5 its complement
    sh.spread = {}
    by = {c.label: c for c in ua.candidates(g, sh)}
    sp, sw = by["Spain -1.5"], by["Switzerland +1.5"]
    assert abs(sp.fair_raw + sw.fair_raw - 1) < 1e-9
    assert sp.fair_raw == pytest.approx(sm.cover_prob(sh.anchor.grid(), -1.5, False))


def test_no_sharp_means_pm_mid_only():
    cs = ua.candidates(_game(), None)
    assert cs and all(c.source == "pm_mid" for c in cs)
    assert all(c.ev < 0 for c in cs)                    # the mid never beats its own ask


def test_wide_or_thin_books_are_refused():
    g = _game()
    g.markets = [_mkt("totals", "Spain vs. Switzerland: O/U 2.5", ["Over", "Under"], 0.40, 0.52, 2.5),
                 _mkt("totals", "Spain vs. Switzerland: O/U 1.5", ["Over", "Under"], 0.70, 0.71, 1.5, liq=50)]
    assert ua.candidates(g, None) == []


# ── the rule ─────────────────────────────────────────────────────────────────

def _c(ev, src="sharp_exact", depth=500):
    c = ua.Cand("totals", None, 2.5, "Over", "q", "c", "t", 0.5, 0.51, 1e4, 0.52, 0.52, src)
    c.ev, c.depth_usd = ev, depth
    return c


def test_decide():
    assert ua.decide(_c(2.0), fresh=True, mins=300) == "edge"
    assert ua.decide(_c(2.0), fresh=False, mins=300) is None           # stale: never EDGE
    assert ua.decide(_c(2.0, "sharp_model"), True, 300) is None        # model needs 3%
    assert ua.decide(_c(-1.0), True, 30) == "forced"
    assert ua.decide(_c(5.0, "pm_mid"), True, 300) is None
    assert ua.decide(_c(2.0, depth=5), True, 300) is None               # no depth for 1u


def test_ev_includes_the_fee():
    assert ua.ev_pct(0.5, 0.5) < 0
    assert ua.ev_pct(0.5, 0.5) == pytest.approx(100 * (0.5 / (0.5 * 1.025) - 1))


# ── grading ──────────────────────────────────────────────────────────────────

GOALS = {"spain": 2, "switzerland": 1}


@pytest.mark.parametrize("fam,subj,line,side,res", [
    ("moneyline", "Spain", None, "Yes", "won"),
    ("moneyline", "Spain", None, "No", "lost"),
    ("moneyline", "Switzerland", None, "Yes", "lost"),
    ("moneyline", "Draw", None, "No", "won"),
    ("totals", None, 2.5, "Over", "won"),
    ("totals", None, 3.5, "Under", "won"),
    ("spreads", None, -1.5, "Spain", "lost"),
    ("spreads", None, 1.5, "Switzerland", "won"),
    ("both_teams_to_score", None, None, "Yes", "won"),
])
def test_grade(fam, subj, line, side, res):
    assert ua.grade(fam, subj, line, side, "Switzerland", "Spain", GOALS)[0] == res


def test_grade_fails_closed():
    assert ua.grade("moneyline", "Portugal", None, "Yes", "Spain", "Switzerland", GOALS) is None
    assert ua.grade("spreads", None, -1.5, "Portugal", "Spain", "Switzerland", GOALS) is None
    assert ua.grade("totals", None, 2.5, "Over", "Spain", "Portugal", GOALS) is None
