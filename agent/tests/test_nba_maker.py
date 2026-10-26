"""The parts of nba_maker that fabricate a fill or a side when they are wrong:
which markets are bid, on which club, at what price, when a resting bid counts
as filled, and what the close and the payout are."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import nba_agent as na  # noqa: E402
import nba_maker as mk  # noqa: E402

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
KO = NOW + timedelta(hours=8)


def _mkt(fam, q, outs, bid, ask, line=None, liq=50_000, tick=0.01):
    return {"sportsMarketType": fam, "question": q, "outcomes": json.dumps(outs),
            "clobTokenIds": json.dumps([f"{q}|0", f"{q}|1"]), "bestBid": bid, "bestAsk": ask,
            "liquidityNum": liq, "line": line, "conditionId": f"c-{q}", "active": True,
            "closed": False, "acceptingOrders": True, "orderPriceMinTickSize": tick}


def _game(markets, ko=KO):
    ev = {"slug": "nba-tor-min-2026-10-25", "title": "Raptors vs. Timberwolves",
          "startTime": ko.isoformat(), "markets": markets,
          "teams": [{"name": "Raptors", "alias": "Raptors", "abbreviation": "tor", "ordering": "away"},
                    {"name": "Timberwolves", "alias": "Timberwolves", "abbreviation": "min",
                     "ordering": "home"}]}
    g = na.game_from_event(ev)
    assert g is not None
    return g


TOR, MIN = "Toronto Raptors", "Minnesota Timberwolves"
ML = _mkt("moneyline", "Raptors vs. Timberwolves", ["Raptors", "Timberwolves"], 0.50, 0.51, liq=461_000)


# ── which markets ────────────────────────────────────────────────────────────

def test_main_spread_is_the_one_nearest_even_and_sides_follow_the_labels():
    g = _game([ML,
               _mkt("spreads", "Spread: Raptors (-5.5)", ["Raptors", "Timberwolves"], 0.34, 0.36, -5.5),
               _mkt("spreads", "Spread: Timberwolves (-2.5)", ["Timberwolves", "Raptors"], 0.43, 0.44, -2.5),
               _mkt("spreads", "Spread: Raptors (-1.5)", ["Raptors", "Timberwolves"], 0.47, 0.48, -1.5)])
    ms = mk.pick_markets(g)
    assert [m["market_type"] for m in ms] == ["moneyline", "spreads"]
    sp = ms[1]
    assert sp["question"] == "Spread: Raptors (-1.5)"
    assert [lg["side"] for lg in sp["legs"]] == [TOR, MIN]
    assert sp["lines"] == [-1.5, 1.5]
    # outcome 1 is outcome 0's mirror
    assert abs(sp["legs"][1]["bid"] - 0.52) < 1e-9 and abs(sp["legs"][1]["ask"] - 0.53) < 1e-9


def test_a_tie_on_distance_goes_to_the_deeper_market():
    g = _game([_mkt("spreads", "Spread: Raptors (-1.5)", ["Raptors", "Timberwolves"], 0.47, 0.48, -1.5, liq=249_000),
               _mkt("spreads", "Spread: Timberwolves (-1.5)", ["Timberwolves", "Raptors"], 0.47, 0.48, -1.5, liq=292_000)])
    (sp,) = mk.pick_markets(g)
    assert sp["question"] == "Spread: Timberwolves (-1.5)"
    assert sp["legs"][0]["side"] == MIN and sp["lines"][0] == -1.5


def test_no_spread_near_even_means_no_spread_bid():
    g = _game([ML, _mkt("spreads", "Spread: Raptors (-10.5)", ["Raptors", "Timberwolves"], 0.21, 0.24, -10.5)])
    assert [m["market_type"] for m in mk.pick_markets(g)] == ["moneyline"]


def test_a_spread_whose_question_team_is_not_outcome_0_is_refused():
    # the question says Raptors -1.5 but outcome 0 is Timberwolves: the line would sit on the wrong club
    g = _game([_mkt("spreads", "Spread: Raptors (-1.5)", ["Timberwolves", "Raptors"], 0.47, 0.48, -1.5)])
    assert mk.pick_markets(g) == []


def test_two_moneylines_on_one_game_bid_nothing():
    g = _game([ML, _mkt("moneyline", "Raptors vs. Timberwolves (2)", ["Raptors", "Timberwolves"], 0.5, 0.51)])
    assert mk.pick_markets(g) == []


def test_bids_are_due_inside_24h_and_once_per_market():
    g = _game([ML])
    assert len(mk.plan_bids([g], set(), NOW)) == 1
    assert mk.plan_bids([g], {(g.slug, "moneyline")}, NOW) == []
    far = _game([ML], ko=NOW + timedelta(hours=30))
    late = _game([ML], ko=NOW + timedelta(minutes=10))
    assert mk.plan_bids([far, late], set(), NOW) == []


def test_a_thin_market_is_not_bid():
    g = _game([_mkt("moneyline", "Raptors vs. Timberwolves", ["Raptors", "Timberwolves"], 0.5, 0.51, liq=500)])
    assert mk.plan_bids([g], set(), NOW) == []


# ── the price ────────────────────────────────────────────────────────────────

def test_bid_joins_at_one_or_two_ticks_and_improves_at_three():
    assert mk.bid_price({"bid": 0.50, "ask": 0.51}, 0.01) == 0.50
    assert mk.bid_price({"bid": 0.50, "ask": 0.52}, 0.01) == 0.50
    assert mk.bid_price({"bid": 0.50, "ask": 0.53}, 0.01) == 0.51


def test_both_sides_never_meet():
    # at every spread the two bids' Yes-equivalent prices stay apart
    for spread_ticks in (1, 2, 3):
        yes = {"bid": 0.50, "ask": round(0.50 + 0.01 * spread_ticks, 2)}
        no = {"bid": round(1 - yes["ask"], 2), "ask": round(1 - yes["bid"], 2)}
        a, b = mk.bid_price(yes, 0.01), mk.bid_price(no, 0.01)
        assert a + b < 1 - 1e-9


def test_wide_or_extreme_books_get_no_bid():
    assert mk.bid_price({"bid": 0.50, "ask": 0.54}, 0.01) is None      # wider than 3c
    assert mk.bid_price({"bid": 0.96, "ask": 0.97}, 0.01) is None      # past MAX_BID
    assert mk.bid_price({"bid": 0.02, "ask": 0.03}, 0.01) is None      # under MIN_BID


# ── the fill ─────────────────────────────────────────────────────────────────

T0 = 1_000_000.0


def _tr(ts, price, asset="yes"):
    return {"timestamp": ts, "price": price, "asset": asset}


def test_a_print_strictly_below_the_bid_fills():
    t, px, touch = mk.fill_from([_tr(T0 + 50, 0.50), _tr(T0 + 90, 0.49)], "yes", 0.50, T0, T0 + 1000)
    assert t == T0 + 90 and px == 0.49 and touch == T0 + 50


def test_a_print_at_the_bid_is_only_a_touch():
    t, px, touch = mk.fill_from([_tr(T0 + 50, 0.50), _tr(T0 + 60, 0.51)], "yes", 0.50, T0, T0 + 1000)
    assert t is None and px is None and touch == T0 + 50


def test_the_other_token_is_read_as_its_mirror():
    # No traded at 0.52 → Yes at 0.48, below a 0.50 bid: a fill
    t, px, _ = mk.fill_from([_tr(T0 + 10, 0.52, asset="no")], "yes", 0.50, T0, T0 + 1000)
    assert t == T0 + 10 and abs(px - 0.48) < 1e-9
    # No at 0.49 → Yes at 0.51, above the bid: nothing
    assert mk.fill_from([_tr(T0 + 10, 0.49, asset="no")], "yes", 0.50, T0, T0 + 1000)[0] is None


def test_trades_before_the_bid_or_after_tip_off_never_count():
    trades = [_tr(T0 - 1, 0.40), _tr(T0, 0.40), _tr(T0 + 2000, 0.40)]
    assert mk.fill_from(trades, "yes", 0.50, T0, T0 + 1000) == (None, None, None)


def test_the_first_trade_through_is_the_fill_whatever_the_order_given():
    t, px, _ = mk.fill_from([_tr(T0 + 90, 0.45), _tr(T0 + 30, 0.49)], "yes", 0.50, T0, T0 + 1000)
    assert t == T0 + 30 and px == 0.49


# ── the close and the payout ─────────────────────────────────────────────────

def test_close_is_the_last_point_at_or_before_tip_off():
    hist = [{"t": 100, "p": 0.40}, {"t": 200, "p": 0.42}, {"t": 260, "p": 0.90}]
    assert mk.close_from(hist, 200) == (0.42, 200)
    assert mk.close_from(hist, 50) == (None, None)


def test_maker_yield_has_no_fee():
    assert abs(mk.maker_yield(1.0, 0.80) - 0.25) < 1e-12
    assert mk.maker_yield(0.0, 0.80) == -1.0
    assert abs(mk.maker_yield(0.5, 0.50)) < 1e-12
