"""close_model + close_maker_agent: the pure parts — de-vig, no lookahead in the
team walk, the bid price, the side choice and the fill rule."""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import close_maker_agent as ag  # noqa: E402
import close_model as cm        # noqa: E402


def test_devig_rows_sum_to_one_and_keep_order():
    p = cm.devig(np.array([[2.0, 3.4, 4.0], [1.3, 5.5, 11.0]]))
    assert np.allclose(p.sum(1), 1.0)
    assert p[0, 0] > p[0, 2] > 0 and p[1, 0] > 0.7


def _matches(rows):
    m = pd.DataFrame(rows, columns=["match_id", "league", "season", "kickoff_utc", "h", "a", "hs", "as_",
                                    "hst", "ast", "hsh", "ash"])
    m["kickoff_utc"] = pd.to_datetime(m.kickoff_utc, utc=True)
    for c in ("cl_oh", "cl_od", "cl_oa", "pre_oh", "pre_od", "pre_oa", "cl_bk"):
        m[c] = np.nan
    return m


def test_walk_never_lets_a_match_see_itself():
    m = _matches([(1, "ENG-PR", 2025, "2025-08-10", 10, 20, 3, 0, 8, 1, 15, 5),
                  (2, "ENG-PR", 2025, "2025-08-17", 20, 10, 0, 0, 2, 2, 9, 9)])
    f, st = cm.walk(m)
    # the first match is read from empty states
    assert f.loc[0, "h_gd_f"] == 0 and f.loc[0, "a_gd_f"] == 0 and f.loc[0, "h_n"] == 0
    # the second sees only the first: team 10 (now away) won 3-0
    assert f.loc[1, "a_gd_f"] > 0 and f.loc[1, "h_gd_f"] < 0 and f.loc[1, "a_n"] == 1
    assert f.loc[1, "a_rest"] == 7.0 or f.loc[1, "a_rest"] == 7
    assert st[10]["n"] == 2


def test_missing_shots_do_not_count_as_zero():
    m = _matches([(1, "ENG-PR", 2025, "2025-08-10", 10, 20, 1, 0, np.nan, np.nan, np.nan, np.nan)])
    _, st = cm.walk(m)
    assert st[10]["sot_f"] == 0.0 and st[10]["gd_f"] > 0     # untouched, not pulled toward a fake 0-0


def test_predict_is_the_price_when_the_model_is_neutral():
    spec = {"features": ["l0"] + cm.FEATURES, "coef": [1.0] + [0.0] * len(cm.FEATURES), "intercept": 0.0}
    x = {k: 0.0 for k in cm.FEATURES}
    assert abs(cm.predict(spec, float(cm.logit(0.4)), x) - 0.4) < 1e-9


def test_bid_improves_by_a_tick_only_when_there_is_room():
    assert ag.bid_price({"bid": 0.40, "ask": 0.43}, q=0.45) == 0.41
    assert ag.bid_price({"bid": 0.40, "ask": 0.41}, q=0.45) == 0.40
    assert ag.bid_price({"bid": 0.40, "ask": 0.43}, q=0.425) is None     # within 2pp of the model


def _bk(bid, ask, depth=500.0):
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": ask - bid,
            "bid_depth": depth, "ask_depth": depth}


def test_normalised_refuses_a_broken_book():
    books = {"home": _bk(0.40, 0.42), "draw": _bk(0.27, 0.28), "away": _bk(0.31, 0.32)}
    p = ag.normalised(books)
    assert p is not None and abs(sum(p.values()) - 1) < 1e-9
    assert ag.normalised({**books, "draw": None}) is None
    assert ag.normalised({**books, "draw": _bk(0.10, 0.90)}) is None                  # placeholder ladder
    assert ag.normalised({**books, "home": _bk(0.60, 0.62)}) is None                  # sums to 1.2


def test_choose_takes_the_bigger_gap_and_respects_the_book():
    books = {"home": _bk(0.40, 0.42), "draw": _bk(0.27, 0.28), "away": _bk(0.31, 0.32)}
    p = ag.normalised(books)
    side, price, _ = ag.choose({"home": p["home"] + 0.03, "away": p["away"] + 0.025}, p, books)
    assert side == "home" and price == 0.41          # two ticks wide: improve by one
    assert ag.choose({"home": p["home"] + 0.01, "away": p["away"]}, p, books) is None
    thin = {**books, "home": _bk(0.40, 0.42, depth=10.0)}
    side, _, _ = ag.choose({"home": p["home"] + 0.05, "away": p["away"] + 0.03}, p, thin)
    assert side == "away"


def test_fill_needs_a_print_below_the_bid_or_the_ask_at_it():
    yes, no = "Y", "N"
    t0 = 1_000.0
    at_bid = [{"asset": yes, "price": 0.40, "timestamp": t0 + 10}]
    below = [{"asset": yes, "price": 0.39, "timestamp": t0 + 20}]
    no_side = [{"asset": no, "price": 0.62, "timestamp": t0 + 30}]                 # Yes-equivalent 0.38
    before = [{"asset": yes, "price": 0.30, "timestamp": t0 - 5}]                  # before we posted
    book_above = _bk(0.39, 0.41)
    assert ag.fill_from(book_above, at_bid, yes, 0.40, t0)[0] is None
    assert ag.fill_from(book_above, at_bid, yes, 0.40, t0)[2] == t0 + 10           # touched only
    assert ag.fill_from(book_above, below, yes, 0.40, t0)[:2] == ("traded_through", t0 + 20)
    assert ag.fill_from(book_above, no_side, yes, 0.40, t0)[0] == "traded_through"
    assert ag.fill_from(book_above, before, yes, 0.40, t0)[0] is None
    assert ag.fill_from(_bk(0.38, 0.40), [], yes, 0.40, t0)[0] == "ask_at_bid"


def test_history_gap_judges_a_team_against_its_own_league():
    ko = datetime(2026, 10, 10, 16, 30, tzinfo=timezone.utc)
    last = (ko - timedelta(days=20)).isoformat()            # a three-week break: fine
    states = {"teams": {"1": {"league": "ENG-PR", "last": last}, "2": {"league": "ENG-PR", "last": last},
                        "3": {"league": "ENG-PR", "last": (ko - timedelta(days=45)).isoformat()}}}
    f = ag.Fixture(ev={}, league="ENG-PR", kickoff=ko, title="", home="", away="", home_id=1, away_id=2)
    assert ag.history_gap(states, f) is None
    f.away_id = 3                                            # 25 days behind its league
    assert ag.history_gap(states, f).startswith("team history behind")
    old = {"teams": {"1": {"league": "ENG-PR", "last": (ko - timedelta(days=60)).isoformat()},
                     "2": {"league": "ENG-PR", "last": (ko - timedelta(days=60)).isoformat()}}}
    f.away_id = 2
    assert ag.history_gap(old, f).startswith("league data stale")
