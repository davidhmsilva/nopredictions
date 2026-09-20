"""Where to buy, across two exchanges.

Everything here is offline: the properties being pinned are about the DECISION
-- which venue, after which fee, gated on which book -- and every one of them
has a live counterpart in site/app/lib/venues.ts that has to keep agreeing.

The regressions these pin were all found on 2026-09-20 while the site's board
was being built, and every one of them makes a strategy look BETTER than it is:

  * quoting a saving gross of fees overstates it by about half, because
    Kalshi's taker fee is 40% higher
  * letting a fixture's 1X2 spread vouch for its Over 2.5 produced a 27.3pp
    "saving" against a book quoted bid 0.35 / ask 0.69
  * reading Kalshi's `occurrence_datetime` as a kick-off joined fixtures on a
    +3h offset that happened to sit exactly on a +-3h window's boundary
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import venues as V  # noqa: E402


# ── fees ─────────────────────────────────────────────────────────────────────

def test_kalshi_fee_is_higher_than_polymarkets_at_every_price():
    for p in (0.05, 0.2, 0.5, 0.8, 0.95):
        assert V.taker_fee(p, V.KALSHI) > V.taker_fee(p, V.POLYMARKET)


def test_fee_peaks_at_even_money_and_vanishes_at_the_extremes():
    assert V.taker_fee(0.5, V.POLYMARKET) == pytest.approx(0.0125)
    assert V.taker_fee(0.5, V.KALSHI) == pytest.approx(0.0175)
    # The cheap end is why a settled-market sweep works at all: 0.05 * p(1-p)
    # is 1.25pp at p=0.50 and 0.05pp at p=0.01.
    assert V.taker_fee(0.01, V.POLYMARKET) == pytest.approx(0.000495)
    assert V.taker_fee(0.0, V.KALSHI) == 0.0
    assert V.taker_fee(1.0, V.KALSHI) == 0.0


def test_kalshi_order_fee_rounds_up_to_the_cent():
    # Kalshi rounds the whole order UP, so a small order pays a brutal
    # effective rate. Anything that SIZES an order must use this, not the rate.
    # 0.07 * 1 * 0.25 = $0.0175, which rounds UP to two cents.
    assert V.kalshi_order_fee(1, 0.5) == 0.02
    assert V.kalshi_order_fee(1000, 0.5) == pytest.approx(17.5)


# ── the book gate ────────────────────────────────────────────────────────────

def test_a_one_sided_book_is_not_a_book():
    assert V.grade([V.Quote(bid=None, ask=0.99)]) == 'none'
    assert V.grade([V.Quote(bid=0.55, ask=None)]) == 'none'


def test_the_spread_is_the_tell_not_the_depth():
    # CA Mineiro v EC Vitoria: bid 0.55 / ask 0.99 behind $30,117, traded at
    # 0.56 two minutes later. Every depth floor passes that quote.
    assert V.grade([V.Quote(bid=0.55, ask=0.99, ask_depth_usd=30_117)]) == 'none'
    # Depth only ever downgrades clean to thin.
    assert V.grade([V.Quote(bid=0.50, ask=0.51, ask_depth_usd=30_117)]) == 'clean'
    assert V.grade([V.Quote(bid=0.50, ask=0.51, ask_depth_usd=10)]) == 'thin'
    # Unknown depth is not a thin book.
    assert V.grade([V.Quote(bid=0.50, ask=0.51, ask_depth_usd=None)]) == 'clean'


def test_grade_reads_the_worst_quote_given():
    tight = V.Quote(bid=0.50, ask=0.51)
    wide = V.Quote(bid=0.35, ask=0.69)
    assert V.grade([tight]) == 'clean'
    assert V.grade([tight, wide]) == 'none'


# ── the pick ─────────────────────────────────────────────────────────────────

def test_the_fee_never_flips_a_clear_winner():
    """The claim this file used to make, kept as the test that refuted it.

    "Netting the fee stops Kalshi winning prices it should not" sounds right
    and is false: the fee difference is 0.02*p*(1-p), which maxes at 0.005 =
    MIN_GAP, so a gap big enough to call cannot be eaten by it. Searched
    exhaustively rather than argued, because the wording shipped once.
    """
    worst = -1.0
    for i in range(2, 99):
        p = i / 100
        for d in (x / 1000 for x in range(5, 200)):
            k = round(p - d, 4)
            if not (V.TRADEABLE[0] < k < V.TRADEABLE[1]):
                continue
            worst = max(worst, V.net_cost(k, V.KALSHI) - V.net_cost(p, V.POLYMARKET))
    assert worst < 1e-5


def test_the_fee_decides_when_the_two_print_the_same_price():
    # Same price, lower fee: Polymarket is genuinely cheaper, and a gross
    # comparison calls it a draw. Only near even money does the difference
    # reach half a cent on its own.
    even = V.Quote(bid=0.49, ask=0.50)
    pick = V.best_of({V.POLYMARKET: even, V.KALSHI: even})
    assert pick.venue == V.POLYMARKET
    assert pick.saving_pp == pytest.approx(0.5)

    # Away from even money the fee difference is under half a cent, which is
    # below one Kalshi tick, so it is reported as level rather than as a win.
    wide = V.Quote(bid=0.29, ask=0.30)
    assert V.best_of({V.POLYMARKET: wide, V.KALSHI: wide}).venue is None


def test_a_venue_with_no_real_book_cannot_win_a_price():
    pm = V.Quote(bid=0.39, ask=0.40, ask_depth_usd=5_000)
    # The Santa Cruz v Floresta Over 2.5: cheapest by arithmetic, not a market.
    kal = V.Quote(bid=0.35, ask=0.69)
    assert V.best_of({V.POLYMARKET: pm, V.KALSHI: kal}).venue is None


def test_two_prices_inside_half_a_cent_are_one_price():
    pm = V.Quote(bid=0.49, ask=0.500)
    kal = V.Quote(bid=0.49, ask=0.498)
    pick = V.best_of({V.POLYMARKET: pm, V.KALSHI: kal})
    assert pick.venue is None
    assert pick.quoted == 2          # both quoted; neither is better


def test_one_quote_is_not_a_comparison():
    pick = V.best_of({V.POLYMARKET: V.Quote(bid=0.49, ask=0.50), V.KALSHI: None})
    assert pick.venue is None and pick.quoted == 1 and pick.saving_pp is None
    assert not pick.contested


def test_a_decided_market_has_no_winner():
    # Outside the tradeable band the odds stop describing a bet anyone places.
    pm = V.Quote(bid=0.990, ask=0.995)
    kal = V.Quote(bid=0.985, ask=0.990)
    assert V.best_of({V.POLYMARKET: pm, V.KALSHI: kal}) == V.NO_PICK


def test_saving_is_measured_net_and_in_points():
    pm = V.Quote(bid=0.59, ask=0.60)
    kal = V.Quote(bid=0.54, ask=0.55)
    pick = V.best_of({V.POLYMARKET: pm, V.KALSHI: kal})
    assert pick.venue == V.KALSHI
    expected = (V.net_cost(0.60, V.POLYMARKET) - V.net_cost(0.55, V.KALSHI)) * 100
    assert pick.saving_pp == pytest.approx(round(expected, 2))


# ── what an agent calls ──────────────────────────────────────────────────────

def test_choose_books_the_cheaper_venue_and_remembers_the_other():
    pm = V.Quote(bid=0.59, ask=0.60, ask_depth_usd=1_000)
    kal = V.Quote(bid=0.54, ask=0.55, ask_depth_usd=1_000)
    ex = V.choose(pm, kal)
    assert ex.venue == V.KALSHI and ex.ask == 0.55
    assert ex.alt_venue == V.POLYMARKET and ex.alt_ask == 0.60
    assert ex.contested and ex.saving_pp > 0


def test_choose_falls_back_to_polymarket_when_the_two_are_level():
    # Every one of these strategies has its history on Polymarket, so "either"
    # has to resolve there rather than drift between venues tick by tick.
    # At 0.30 the fee difference is under half a cent, so this really is level.
    pm = V.Quote(bid=0.29, ask=0.30)
    kal = V.Quote(bid=0.29, ask=0.30)
    ex = V.choose(pm, kal)
    assert ex.venue == V.POLYMARKET
    assert ex.alt_ask == 0.30 and ex.saving_pp is None


def test_choose_uses_kalshi_when_polymarket_has_no_real_book():
    # A one-sided Polymarket quote is not an alternative we chose against, and
    # recording it as one would make the row look contested when it was not.
    pm = V.Quote(bid=None, ask=0.60)
    kal = V.Quote(bid=0.54, ask=0.55)
    ex = V.choose(pm, kal)
    assert ex.venue == V.KALSHI
    # Polymarket quoted nothing tradeable, so there was no alternative.
    assert ex.alt_ask is None and not ex.contested


def test_choose_refuses_when_neither_venue_has_a_book():
    assert V.choose(V.Quote(bid=None, ask=0.99), None) is None
    assert V.choose(None, None) is None


# ── Kalshi's own shapes ──────────────────────────────────────────────────────

def test_et_date_comes_off_the_event_ticker():
    assert V.kalshi_et_date('KXBRASILEIROCGAME-26SEP20VITCRU') == '20260920'
    assert V.kalshi_et_date('KXMLBGAME-26SEP151840MILPIT') == '20260915'
    assert V.kalshi_et_date('KXNOTAGAME') is None


def test_a_late_kickoff_files_under_the_eastern_day_it_belongs_to():
    # 02:00Z Sunday is 22:00 ET Saturday, which is where every US schedule
    # puts it. A UTC date would move it a day and lose the join.
    assert V.et_date('2026-09-21T02:00:00Z') == '20260920'
    assert V.et_date('2026-09-20T22:00:00Z') == '20260920'
    assert V.et_date(None) is None


def test_occurrence_datetime_is_never_read_as_a_kickoff():
    # It is the expected SETTLEMENT: kick-off + 3h on 55 of 67 measured pairs,
    # and +2h to +4.5h on the rest. The fixture carries no `kickoff` field at
    # all, so nothing can quietly start using it as one.
    assert not hasattr(V.KalshiFixture('t', 's', 'c', 'h', 'a', None, 'u'), 'kickoff')


def _event(title, subs, **extra):
    return {
        'event_ticker': 'KXTESTGAME-26SEP20ABCDEF',
        'title': title,
        'markets': [
            dict({'ticker': f'KXTESTGAME-26SEP20ABCDEF-{i}',
                  'yes_sub_title': s, 'yes_bid_dollars': '0.40',
                  'yes_ask_dollars': '0.42', 'volume_fp': '10'}, **extra)
            for i, s in enumerate(subs)
        ],
    }


def test_a_game_event_resolves_its_three_sides():
    f = V.parse_game_event(_event('Milan vs Lecce', ['Milan', 'Lecce', 'Tie']), 'KXSERIEAGAME', 'Serie A')
    assert f is not None
    assert f.legs['home'][1] == 'Milan'
    assert f.legs['away'][1] == 'Lecce'
    assert f.legs['draw'][1] == 'Tie'
    assert f.et_date == '20260920'


def test_an_ambiguous_side_fails_closed():
    # A label that reads as both teams cannot be placed, and placing it wrong
    # INVERTS the reading rather than blunting it.
    assert V.parse_game_event(_event('Milan vs Milan', ['Milan', 'Milan', 'Tie']), 's', 'c') is None
    # A label that reads as neither.
    assert V.parse_game_event(_event('Milan vs Lecce', ['Roma', 'Lecce', 'Tie']), 's', 'c') is None
    # No draw leg at all is fine; no away leg is not.
    assert V.parse_game_event(_event('Milan vs Lecce', ['Milan', 'Tie']), 's', 'c') is None


def test_a_totals_ladder_is_keyed_on_the_strike_not_the_wording():
    ev = {
        'event_ticker': 'KXTESTTOTAL-26SEP20ABCDEF',
        'title': 'Milan vs Lecce: Total Goals',
        'markets': [
            {'ticker': 'a', 'yes_sub_title': 'Over 2.5 goals scored', 'floor_strike': 2.5,
             'yes_bid_dollars': '0.50', 'yes_ask_dollars': '0.51', 'volume_fp': '5'},
            {'ticker': 'b', 'yes_sub_title': 'Over 3.5 goals scored', 'floor_strike': 3.5,
             'yes_bid_dollars': '0.24', 'yes_ask_dollars': '0.25', 'volume_fp': '5'},
        ],
    }
    teams, legs, volume = V.parse_total_event(ev)
    assert teams == ('Milan', 'Lecce')
    assert set(legs) == {'2.5', '3.5'}
    assert volume == 10


def test_the_under_is_the_no_leg_of_the_over_ticker():
    f = V.KalshiFixture('t', 's', 'c', 'Milan', 'Lecce', '20260920', 'u',
                        totals={'2.5': ('tk', 'Over 2.5', V.Quote(bid=0.50, ask=0.52))})
    under = f.under(2.5)
    # Buying NO at 1 - yes_bid IS selling YES at the bid.
    assert under.ask == pytest.approx(0.50)
    assert under.bid == pytest.approx(0.48)
    # Its depth is the size resting on the yes bid, which the feed omits.
    assert under.ask_depth_usd is None


def test_a_missing_line_is_none_not_a_price():
    f = V.KalshiFixture('t', 's', 'c', 'Milan', 'Lecce', '20260920', 'u')
    assert f.over(2.5) is None
    assert f.under(2.5) is None
    assert f.side('home') is None


# ── the join ─────────────────────────────────────────────────────────────────

def _index(*fixtures):
    idx = V.KalshiSoccerIndex(auto_refresh=False)
    idx._fixtures = list(fixtures)       # noqa: SLF001 - offline by construction
    return idx


def _fx(home, away, day='20260920'):
    return V.KalshiFixture('KXT-26SEP20AB', 's', 'c', home, away, day, 'u',
                           legs={'home': ('h', home, V.Quote(bid=0.4, ask=0.42))})


def test_the_join_needs_the_day_to_agree():
    idx = _index(_fx('Milan', 'Lecce', '20260921'))
    assert idx.fixture('AC Milan', 'US Lecce', '2026-09-20T18:45:00Z') is None
    idx = _index(_fx('Milan', 'Lecce', '20260920'))
    assert idx.fixture('AC Milan', 'US Lecce', '2026-09-20T18:45:00Z') is not None


def test_the_join_refuses_a_crossed_pairing():
    # A pairing that works both ways is not a pairing. On a derby, where both
    # sides share a city token, that is exactly the case that would invert the
    # board.
    idx = _index(_fx('Milan', 'Milan'))
    assert idx.fixture('Milan', 'Milan', '2026-09-20T18:45:00Z') is None


def test_the_join_refuses_two_candidates():
    idx = _index(_fx('Milan', 'Lecce'), _fx('AC Milan', 'Lecce'))
    assert idx.fixture('AC Milan', 'US Lecce', '2026-09-20T18:45:00Z') is None


def test_the_join_never_runs_on_names_alone():
    idx = _index(_fx('Milan', 'Lecce'))
    assert idx.fixture('AC Milan', 'US Lecce', None) is None


def test_the_join_is_not_a_substring_match():
    # "plate" is inside "platense"; this pairing cost the project two
    # double-digit phantom edges on 2026-08-14.
    idx = _index(_fx('Platense', 'Boca Juniors'))
    assert idx.fixture('CA River Plate', 'AA Argentinos Juniors',
                       '2026-09-20T18:45:00Z') is None
