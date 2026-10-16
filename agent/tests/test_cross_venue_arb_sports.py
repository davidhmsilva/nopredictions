"""cross_venue_arb_sports: which baskets count, and what they cost."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cross_venue_arb_sports as A  # noqa: E402

US2 = ('home', 'away')
NFL = ('home', 'away', 'tie')
X12 = ('home', 'draw', 'away')


def pm_team(side, scenarios, rate=0.05, asks=None, bid=None, market='pm1'):
    pay = {s: (1.0 if s == side else 0.0) for s in scenarios}
    if 'tie' in scenarios:
        pay['tie'] = 0.5
    return A.Leg(venue='polymarket', ref=f'tok-{side}', market=market, side='YES',
                 label=f'PM:{side}', pay=pay, taker=rate, maker=0.0,
                 ladder=asks or [], bid=bid)


def kalshi(side, yes, scenarios, mult=1.0, asks=None, bid=None):
    legs = A.kalshi_legs('KXTEST', mult, {side: {'ticker': f'K-{side}', 'yes_sub_title': side}},
                         scenarios, tie_pays_half='tie' in scenarios)
    leg = legs[0] if yes else legs[1]
    leg.ladder, leg.bid = asks or [], bid
    return leg


def baskets(legs, scenarios):
    return {tuple(legs[i].label for i in combo) for combo, _ in A.enumerate_baskets(legs, scenarios)}


def test_nfl_tie_keeps_the_two_team_complement_whole():
    # Both venues pay 0.5 per team on a tie, so home here + away there pays $1
    # in all three scenarios -- and so does home here + NOT home there.
    legs = [pm_team('home', NFL), kalshi('away', True, NFL), kalshi('home', False, NFL)]
    got = baskets(legs, NFL)
    assert ('PM:home', 'K:YES away') in got
    assert ('PM:home', 'K:NO home') in got


def test_a_team_and_itself_is_not_a_basket():
    legs = [pm_team('home', US2), kalshi('home', True, US2)]
    assert baskets(legs, US2) == set()


def test_soccer_needs_all_three_outcomes_or_a_yes_no_pair():
    legs = [pm_team('home', X12, market='h'), kalshi('draw', True, X12), kalshi('away', True, X12),
            kalshi('home', False, X12)]
    got = baskets(legs, X12)
    assert ('PM:home', 'K:YES draw', 'K:YES away') in got        # dutch
    assert ('PM:home', 'K:NO home') in got                       # pair
    assert ('PM:home', 'K:YES draw') not in got                  # misses the away win
    # A redundant leg is not a basket of its own.
    assert ('PM:home', 'K:YES draw', 'K:NO home') not in got


def test_both_sides_of_one_book_are_its_spread_not_an_arb():
    legs = A.kalshi_legs('KXTEST', 1.0, {'home': {'ticker': 'K-h', 'yes_sub_title': 'h'}}, US2, False)
    assert A.enumerate_baskets(legs, US2) == []


def test_kalshi_fee_rounds_up_per_order_and_respects_the_multiplier():
    assert A.fee_dollars('kalshi', 0.07, 100, 0.5) == 1.75          # not 1.76
    assert A.fee_dollars('kalshi', 0.07, 1, 0.5) == 0.02            # $0.0175 -> 2 cents on 1 share
    assert A.fee_dollars('kalshi', 0.035, 100, 0.5) == 0.88         # MLB's 0.5 multiplier
    assert abs(A.fee_dollars('polymarket', 0.03, 100, 0.5) - 0.75) < 1e-12


def test_pm_rate_is_read_off_the_market():
    assert A.pm_rate({'feesEnabled': True, 'feeSchedule': {'rate': 0.03, 'exponent': 1}}) == 0.03
    assert A.pm_rate({'feeType': 'zero_fees', 'feeSchedule': {'rate': 0, 'exponent': 1}}) == 0.0
    assert A.pm_rate({'feesEnabled': False, 'feeSchedule': {'rate': 0.05}}) == 0.0
    assert A.pm_rate({}) == 0.05


def test_an_arb_needs_the_gap_to_beat_both_fees():
    # 0.48 + 0.49 = 0.97: 3pp gross. Fees at 0.05 / 0.07 near even money are
    # ~1.25 + ~1.75pp, so it clears by a hair at size.
    pm = pm_team('home', US2, asks=[(0.48, 1000)])
    k = kalshi('away', True, US2, asks=[(0.49, 1000)])
    g = A.Game(sport='nhl', title='t', start=None, scenarios=US2, legs=[pm, k])
    g.baskets = A.enumerate_baskets(g.legs, US2)
    r = A.evaluate(g)
    assert r['best_cross']['gross_pp'] == 3.0
    assert r['arbs'] and r['arbs'][0]['profit'] > 0

    # One tick of gross is what July found everywhere: fees eat it.
    k.ladder = [(0.51, 1000)]
    r = A.evaluate(g)
    assert r['best_cross']['gross_pp'] == 1.0
    assert r['best_cross']['net_pp'] < 0
    assert r['arbs'] == []


def test_a_one_share_arb_is_eaten_by_kalshis_cent():
    pm = pm_team('home', US2, rate=0.0, asks=[(0.50, 1)])
    k = kalshi('away', True, US2, asks=[(0.495, 1)])
    g = A.Game(sport='nhl', title='t', start=None, scenarios=US2, legs=[pm, k])
    g.baskets = A.enumerate_baskets(g.legs, US2)
    # 0.5pp gross on one share = $0.005; Kalshi charges $0.01.
    assert A.evaluate(g)['arbs'] == []


def test_maker_window_rests_one_leg_and_takes_the_other():
    pm = pm_team('home', US2, asks=[(0.52, 100)], bid=0.50)
    k = kalshi('away', True, US2, asks=[(0.49, 100)], bid=None)
    g = A.Game(sport='nhl', title='t', start=None, scenarios=US2, legs=[pm, k])
    g.baskets = A.enumerate_baskets(g.legs, US2)
    r = A.evaluate(g)
    # Kalshi has no bid to rest on, so the only window is: rest PM home at
    # 0.50 (no maker fee) and take Kalshi away at 0.49 (+1.75pp fee).
    assert abs(r['maker_pp'] - (100 * (1 - 0.50 - 0.49 - 0.07 * 0.49 * 0.51))) < 1e-3
    assert r['maker_how'].startswith('rest PM:home')


# ---------------------------------------------------------------------------
# maker_window_sim: when a resting bid is filled
# ---------------------------------------------------------------------------

import maker_window_sim as M  # noqa: E402


def _leg(venue, side, ref='T'):
    return M.L(['x', venue, ref, 'mkt', side, [1.0, 0.0], 0.05, 0.0, 0.50, 200.0, 0.52, 50.0])


def test_a_print_through_our_price_fills_us_whatever_the_queue():
    prints = [(110.0, 0.49, 3.0)]
    assert M.fill(prints, 0.50, queue=10_000, size=100, t0=100, t1=160) == (110.0, 100)


def test_a_print_at_our_price_fills_only_past_the_queue_ahead():
    prints = [(110.0, 0.50, 150.0), (120.0, 0.50, 100.0)]
    # 200 queued ahead: the first print does not reach us, the second fills 50.
    assert M.fill(prints, 0.50, queue=200, size=100, t0=100, t1=160) == (120.0, 50.0)


def test_prints_before_the_order_is_live_do_not_count():
    prints = [(100.5, 0.40, 999.0)]
    assert M.fill(prints, 0.50, queue=0, size=100, t0=100, t1=160) is None


def test_polymarket_complement_buy_hits_our_bid_at_one_minus_price():
    leg = _leg('polymarket', 'YES', ref='HOME')
    tr = [{'t': 1.0, 'asset': 'AWAY', 'side': 'BUY', 'price': 0.45, 'size': 10.0},
          {'t': 2.0, 'asset': 'HOME', 'side': 'BUY', 'price': 0.56, 'size': 10.0},   # lifts asks
          {'t': 3.0, 'asset': 'HOME', 'side': 'SELL', 'price': 0.54, 'size': 5.0}]
    got = M.bid_prints(leg, tr, complement='AWAY')
    assert [(t, round(p, 4), s) for t, p, s in got] == [(1.0, 0.55, 10.0), (3.0, 0.54, 5.0)]


def test_kalshi_no_bid_is_hit_by_yes_takers():
    leg = _leg('kalshi', 'NO')
    tr = [{'t': 1.0, 'taker_side': 'yes', 'yes': 0.40, 'size': 7.0},
          {'t': 2.0, 'taker_side': 'no', 'yes': 0.39, 'size': 7.0}]
    assert [(t, round(p, 4), s) for t, p, s in M.bid_prints(leg, tr, None)] == [(1.0, 0.60, 7.0)]
