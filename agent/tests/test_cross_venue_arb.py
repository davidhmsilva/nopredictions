#!/usr/bin/env python3
"""
Tests for the cross-venue (Polymarket vs Kalshi) arbitrage detector.

The detector's headline output is a negative — "no arbitrage found". A negative
is only worth anything if the thing producing it can produce a positive, so the
core of this file is a synthetic book with a KNOWN arb that the detector must
find, at the right size and the right profit. Without that, "no arb" and
"detector is broken" look identical.

Also pins the two conventions that were wrong on the first pass and would
silently zero the scan:
  - Kalshi orderbooks are resting BIDS on both sides (yes_ask = 1 - best_no_bid)
  - the join's time gate needs match time, not the venues' other date fields

Run with:
    cd agent && source ../ingest/.venv/bin/activate
    python tests/test_cross_venue_arb.py         # or: pytest tests/
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cross_venue_arb as A

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = ''):
    if cond:
        print(f'  ✓ {name}')
    else:
        FAILURES.append(f'{name}: {detail}')
        print(f'  ✗ {name}  {detail}')


def leg(venue, outcome, side, ladder):
    return A.Leg(venue=venue, outcome=outcome, side=side,
                 ladder=A._clean_ladder(ladder), ref=f'{venue}-{outcome}-{side}')


# ---------------------------------------------------------------------------

def test_finds_known_arb():
    """A PAIR arb worth exactly 6 cents a share, 100 shares deep.

    YES on Kalshi at 0.40 + NO on PM at 0.54 = 0.94 cost, $1 payout.
    """
    print('\ntest_finds_known_arb')
    fx = A.Fixture(home='Alpha', away='Beta', kickoff=None)
    fx.legs[('kalshi', 'home', 'YES')] = leg('kalshi', 'home', 'YES', [(0.40, 100)])
    fx.legs[('polymarket', 'home', 'NO')] = leg('polymarket', 'home', 'NO', [(0.54, 100)])

    gross = A.find_arbs(fx, use_fees=False, min_profit_pp=0.0)
    check('finds the arb', len(gross) == 1, f'got {len(gross)}')
    if not gross:
        return
    a = gross[0]
    check('kind is PAIR', a.kind == 'PAIR', a.kind)
    check('sizes to full depth', abs(a.shares - 100) < 1e-6, str(a.shares))
    check('gross profit 6pp', abs(a.profit_pp - 6.0) < 1e-6, f'{a.profit_pp}')
    check('gross profit $6', abs(a.profit - 6.0) < 1e-6, f'{a.profit}')

    # Fees must shrink it but not erase it: PM 100*0.05*0.54*0.46 = $1.242,
    # Kalshi ceil(0.07*100*0.40*0.60) = ceil(1.68) = $1.68 → $2.922 total.
    net = A.find_arbs(fx, use_fees=True, min_profit_pp=0.0)
    check('survives fees', len(net) == 1, f'got {len(net)}')
    if net:
        # Hand-computed, not re-derived from the module — a test that recomputes
        # the formula would reproduce the formula's bugs. Worked by hand:
        #   PM     100 * 0.05 * 0.54 * 0.46 = $1.242
        #   Kalshi 0.07 * 100 * 0.40 * 0.60 = $1.68 (already whole cents)
        expected_fee = 1.242 + 1.68
        check('fee matches hand calc', abs(net[0].fees - expected_fee) < 1e-6,
              f'{net[0].fees} vs {expected_fee}')
        check('net profit = gross - fees',
              abs(net[0].profit - 3.078) < 1e-6, f'{net[0].profit}')


def test_rejects_non_arb():
    """The same shape one tick the other way must produce nothing."""
    print('\ntest_rejects_non_arb')
    fx = A.Fixture(home='Alpha', away='Beta', kickoff=None)
    fx.legs[('kalshi', 'home', 'YES')] = leg('kalshi', 'home', 'YES', [(0.46, 100)])
    fx.legs[('polymarket', 'home', 'NO')] = leg('polymarket', 'home', 'NO', [(0.55, 100)])
    check('no arb at 1.01', A.find_arbs(fx, use_fees=False, min_profit_pp=0.0) == [])


def test_dutch_picks_cheapest_venue():
    """Neither venue is dutchable alone; taking the best of each is."""
    print('\ntest_dutch_picks_cheapest_venue')
    fx = A.Fixture(home='Alpha', away='Beta', kickoff=None)
    # PM: 0.50 / 0.30 / 0.25 = 1.05     Kalshi: 0.45 / 0.32 / 0.28 = 1.05
    # best of each: 0.45 + 0.30 + 0.25 = 1.00 → still not an arb
    for oc, p in (('home', 0.50), ('draw', 0.30), ('away', 0.25)):
        fx.legs[('polymarket', oc, 'YES')] = leg('polymarket', oc, 'YES', [(p, 50)])
    for oc, p in (('home', 0.45), ('draw', 0.32), ('away', 0.28)):
        fx.legs[('kalshi', oc, 'YES')] = leg('kalshi', oc, 'YES', [(p, 50)])
    check('1.00 basket is not an arb', A.find_arbs(fx, use_fees=False, min_profit_pp=0.0) == [])

    # Drop the PM draw a tick: best-of-each = 0.45 + 0.28 + 0.25 = 0.98
    fx.legs[('polymarket', 'draw', 'YES')] = leg('polymarket', 'draw', 'YES', [(0.28, 50)])
    got = [a for a in A.find_arbs(fx, use_fees=False, min_profit_pp=0.0) if a.kind == 'DUTCH']
    check('0.98 basket is an arb', len(got) == 1, f'got {len(got)}')
    if got:
        check('dutch profit 2pp', abs(got[0].profit_pp - 2.0) < 1e-6, f'{got[0].profit_pp}')
        venues = {l.venue for l in got[0].legs}
        check('mixes both venues', venues == {'polymarket', 'kalshi'}, str(venues))


def test_sizing_respects_thinnest_leg():
    """Size is capped by the shallowest leg, not the deepest."""
    print('\ntest_sizing_respects_thinnest_leg')
    fx = A.Fixture(home='Alpha', away='Beta', kickoff=None)
    fx.legs[('kalshi', 'home', 'YES')] = leg('kalshi', 'home', 'YES', [(0.40, 5000)])
    fx.legs[('polymarket', 'home', 'NO')] = leg('polymarket', 'home', 'NO', [(0.54, 20)])
    got = A.find_arbs(fx, use_fees=False, min_profit_pp=0.0)
    check('found', len(got) == 1, f'got {len(got)}')
    if got:
        check('capped at 20 shares', abs(got[0].shares - 20) < 1e-6, str(got[0].shares))


def test_vwap_walks_the_ladder():
    """A deeper size must pay the worse levels, not the top of book."""
    print('\ntest_vwap_walks_the_ladder')
    ladder = A._clean_ladder([(0.40, 10), (0.45, 10), (0.60, 10)])
    check('10 shares at top', abs(A.walk(ladder, 10)[1] - 0.40) < 1e-9)
    check('20 shares blends', abs(A.walk(ladder, 20)[1] - 0.425) < 1e-9,
          str(A.walk(ladder, 20)[1]))
    check('too deep returns None', A.walk(ladder, 100) is None)


def test_kalshi_fee_ceiling():
    """Kalshi rounds the fee UP to the cent, per order."""
    print('\ntest_kalshi_fee_ceiling')
    # 0.07 * 1 * 0.5 * 0.5 = $0.0175 → charged $0.02
    check('1 share at 0.50 costs 2c', abs(A.kalshi_fee(1, 0.50) - 0.02) < 1e-9,
          str(A.kalshi_fee(1, 0.50)))
    # 100 shares: 0.07 * 100 * 0.25 = $1.75 exactly, no rounding up needed
    check('100 shares at 0.50 costs $1.75', abs(A.kalshi_fee(100, 0.50) - 1.75) < 1e-9,
          str(A.kalshi_fee(100, 0.50)))
    check('kalshi dearer than pm', A.kalshi_fee(100, 0.50) > A.pm_fee(100, 0.50))


def test_kalshi_book_is_bids_both_sides():
    """yes_ask = 1 - best_no_bid. Inverting this silently prices every Kalshi
    leg at its complement and manufactures arbs that do not exist."""
    print('\ntest_kalshi_book_is_bids_both_sides')
    import unittest.mock as mock
    payload = {'orderbook_fp': {
        'yes_dollars': [['0.4000', '50'], ['0.4100', '30']],   # bids to buy YES
        'no_dollars':  [['0.5700', '40'], ['0.5800', '25']],   # bids to buy NO
    }}
    with mock.patch.object(A, '_get', return_value=payload):
        buy_yes, buy_no = A.load_kalshi_book('FAKE')
    check('best yes ask = 1 - best no bid (0.42)',
          abs(buy_yes[0][0] - 0.42) < 1e-9, str(buy_yes[:2]))
    check('best no ask = 1 - best yes bid (0.59)',
          abs(buy_no[0][0] - 0.59) < 1e-9, str(buy_no[:2]))
    check('yes ladder ascends', buy_yes[0][0] < buy_yes[1][0])


def test_name_normalisation():
    """Accent folding is load-bearing for the join."""
    print('\ntest_name_normalisation')
    check('accents fold', A.norm_team('KF Egnatia Rrogozhinë') == A.norm_team('Egnatia Rrogozhine'),
          f"{A.norm_team('KF Egnatia Rrogozhinë')} vs {A.norm_team('Egnatia Rrogozhine')}")
    check('distinct clubs stay distinct', A.norm_team('Botafogo') != A.norm_team('Botafogo SP'))

    # The stopword list deliberately does NOT chase every club suffix (fbc,
    # fbpa, sjdr, ...). Growing it without bound risks collapsing genuinely
    # different clubs into one key, which is the expensive direction of the
    # error. The fuzzy matcher is the designed safety net for the tail.
    import difflib
    pair = difflib.SequenceMatcher(
        None, A.fixture_key('Coritiba FBC', 'SE Palmeiras'),
        A.fixture_key('Coritiba', 'Palmeiras')).ratio()
    check('suffix variance survives via fuzzy', pair >= A.FUZZY_CUTOFF, f'ratio {pair:.3f}')


def test_pm_outcome_mapping():
    print('\ntest_pm_outcome_mapping')
    h, a = 'KF Egnatia Rrogozhinë', 'NK Celje'
    check('home', A._pm_outcome('Will KF Egnatia Rrogozhinë win on 2026-07-22?', h, a) == 'home')
    check('away', A._pm_outcome('Will NK Celje win on 2026-07-22?', h, a) == 'away')
    check('draw', A._pm_outcome('Will KF Egnatia Rrogozhinë vs. NK Celje end in a draw?', h, a) == 'draw')
    check('rejects halftime prop',
          A._pm_outcome('KF Egnatia Rrogozhinë leading at halftime?', h, a) is None)


def test_kalshi_kickoff_is_not_close_time():
    """close_time is the settlement window, ~2 weeks out. Using it collapses
    the join to zero — this is the bug that did exactly that."""
    print('\ntest_kalshi_kickoff_is_not_close_time')
    market = {'occurrence_datetime': '2026-07-22T22:00:00Z',
              'close_time': '2026-08-05T19:00:00Z'}
    event = {'event_ticker': 'KXUCLGAME-26JUL22EGRCEL'}
    ko = A._kalshi_kickoff(market, event)
    check('uses occurrence_datetime', ko.strftime('%Y-%m-%d') == '2026-07-22', str(ko))

    ko2 = A._kalshi_kickoff({'close_time': '2026-08-05T19:00:00Z'}, event)
    check('falls back to ticker date', ko2.strftime('%Y-%m-%d') == '2026-07-22', str(ko2))


if __name__ == '__main__':
    for fn in [v for k, v in sorted(globals().items()) if k.startswith('test_')]:
        fn()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED:')
        for f in FAILURES:
            print(f'  - {f}')
        sys.exit(1)
    print('all passed')
