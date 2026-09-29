"""
Stage J's pure parts: reading a market, its resolution, its price at a moment,
and the join to our own results. No network, no database.

    cd ingest && .venv/bin/python -m pytest tests/test_stage_j_venue_history.py -q
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stage_j_venue_history as j  # noqa: E402

UTC = timezone.utc


def _pm(question, git='', smt=None, outcomes=('Yes', 'No'), line=None, prices=('1', '0'),
        status='resolved'):
    return {
        'question': question, 'groupItemTitle': git, 'sportsMarketType': smt,
        'outcomes': json.dumps(list(outcomes)), 'clobTokenIds': json.dumps(['111', '222']),
        'line': line, 'outcomePrices': json.dumps(list(prices)),
        'umaResolutionStatus': status, 'closed': True, 'conditionId': '0xabc',
    }


# ---------------------------------------------------------------------------
# titles and families
# ---------------------------------------------------------------------------

def test_fixture_teams_strips_competition_prefix_and_sibling_suffix():
    assert j.fixture_teams('EPL: Tottenham vs. Everton ') == ('Tottenham', 'Everton')
    assert j.fixture_teams('UEFA Nations League: Spain vs. Serbia') == ('Spain', 'Serbia')
    assert j.fixture_teams('Deportivo Saprissa vs. CS Herediano - More Markets') == \
        ('Deportivo Saprissa', 'CS Herediano')
    assert j.fixture_teams('Will Cristiano Ronaldo play in FIFA Club World Cup?') is None


def test_moneyline_2026_with_market_type():
    row = j.classify_pm_market(_pm('Will AD Pasto win on 2026-09-19?', 'AD Pasto', 'moneyline'),
                               'AD Pasto', 'Once Caldas')
    assert row['family'] == 'moneyline'
    assert row['subject'] == 'AD Pasto' and row['subject_team'] == 'a'


def test_moneyline_2024_has_no_market_type_and_is_read_off_the_question():
    row = j.classify_pm_market(_pm('Will Everton beat Tottenham?', 'Everton'),
                               'Tottenham', 'Everton')
    assert row['family'] == 'moneyline' and row['subject_team'] == 'b'


def test_draw_in_both_vintages():
    old = j.classify_pm_market(_pm('Will Tottenham vs Everton end in a draw?', 'Draw'),
                               'Tottenham', 'Everton')
    new = j.classify_pm_market(_pm('Will Sevilla vs. Mallorca end in a draw?',
                                   'Draw (Sevilla vs. Mallorca)'), 'Sevilla', 'Mallorca')
    assert old['subject_team'] == new['subject_team'] == 'draw'


def test_moneyline_subject_that_is_neither_team_is_refused():
    assert j.classify_pm_market(_pm('Will Arsenal win on 2026-09-19?', 'Arsenal', 'moneyline'),
                                'Chelsea', 'Everton') is None


def test_totals_line_from_the_field_or_the_question():
    a = j.classify_pm_market(_pm('A vs. B: O/U 2.5', 'O/U 2.5', 'totals', ('Over', 'Under'), 2.5),
                             'A FC', 'B FC')
    b = j.classify_pm_market(_pm('Arsenal vs. Chelsea: O/U 3.5', 'O/U 3.5', None, ('Over', 'Under')),
                             'Arsenal', 'Chelsea')
    assert a['family'] == 'totals' and a['line'] == 2.5
    assert b['family'] == 'totals' and b['line'] == 3.5


def test_totals_with_outcomes_in_the_other_order_are_refused():
    assert j.classify_pm_market(_pm('A vs. B: O/U 2.5', 'O/U 2.5', 'totals', ('Under', 'Over'), 2.5),
                                'Arsenal', 'Chelsea') is None


def test_first_half_and_corner_families_are_not_match_totals():
    for smt in ('first_half_totals', 'total_corners', 'soccer_team_totals'):
        assert j.classify_pm_market(_pm('x: O/U 1.5', 'O/U 1.5', smt, ('Over', 'Under'), 1.5),
                                    'Arsenal', 'Chelsea') is None


def test_spread_needs_its_outcomes_to_name_the_subject_first():
    ok = j.classify_pm_market(
        _pm('Spread: Deportivo Saprissa (-1.5)', 'Deportivo Saprissa (-1.5)', 'spreads',
            ('Deportivo Saprissa', 'CS Herediano'), -1.5),
        'Deportivo Saprissa', 'CS Herediano')
    assert ok['family'] == 'spreads' and ok['subject_team'] == 'a' and ok['line'] == -1.5
    flipped = j.classify_pm_market(
        _pm('Spread: Deportivo Saprissa (-1.5)', 'Deportivo Saprissa (-1.5)', 'spreads',
            ('CS Herediano', 'Deportivo Saprissa'), -1.5),
        'Deportivo Saprissa', 'CS Herediano')
    assert flipped is None


def test_btts():
    row = j.classify_pm_market(_pm('A vs. B: Both Teams to Score', 'Both Teams to Score',
                                   'both_teams_to_score'), 'Arsenal', 'Chelsea')
    assert row['family'] == 'btts' and row['outcome0'] == 'Yes'


# ---------------------------------------------------------------------------
# resolution and fees
# ---------------------------------------------------------------------------

def test_resolution_reads_only_clean_payouts():
    assert j.pm_resolution(_pm('q', prices=('1', '0'))) == (0, 1.0)
    assert j.pm_resolution(_pm('q', prices=('0', '1'))) == (1, 0.0)
    assert j.pm_resolution(_pm('q', prices=('0.5', '0.5'))) == (None, 0.5)
    assert j.pm_resolution(_pm('q', prices=('0.93', '0.07'))) == (None, None)
    assert j.pm_resolution(_pm('q', prices=('1', '0'), status='disputed')) == (None, None)


def test_fee_rate_is_the_markets_own_schedule():
    assert j.pm_fee_rate({'feesEnabled': False}) == 0.0
    assert j.pm_fee_rate({'feesEnabled': True, 'feeSchedule': {'rate': 0.03}}) == 0.03


# ---------------------------------------------------------------------------
# prices at a moment
# ---------------------------------------------------------------------------

KO = int(datetime(2026, 9, 20, 15, 0, tzinfo=UTC).timestamp())


def test_horizon_never_reads_a_point_after_its_moment():
    pts = [(KO - 24 * 3600 - 60, 0.40), (KO - 3600 - 30, 0.45), (KO - 3600 + 30, 0.99),
           (KO - 120, 0.50), (KO + 60, 0.90)]
    out = j.horizon_prices(pts, KO)
    assert out['mid_24h'] == 0.40
    assert out['mid_1h'] == 0.45          # not the 0.99 thirty seconds after the mark
    assert out['mid_close'] == 0.50       # not the in-play 0.90
    assert out['close_at'] == datetime.fromtimestamp(KO - 120, tz=UTC)


def test_a_close_hours_old_is_not_a_close_and_a_missing_window_stays_empty():
    out = j.horizon_prices([(KO - int(6.5 * 3600), 0.40)], KO)
    assert out['mid_close'] is None        # 6.5 hours old
    assert out['mid_1h'] is None           # 5.5 hours stale at the 1h mark
    assert out['mid_24h'] is None          # the market did not exist a day out
    assert out['mid_6h'] == 0.40
    # A point AFTER the 6h mark is that moment's future, never its price.
    assert j.horizon_prices([(KO - 5 * 3600, 0.40)], KO)['mid_6h'] is None


def test_last_buys_take_the_latest_taker_buy_of_each_outcome_before_kickoff():
    trades = [
        {'timestamp': KO + 30, 'side': 'BUY', 'outcomeIndex': 0, 'price': 0.9},    # in play
        {'timestamp': KO - 4, 'side': 'BUY', 'outcomeIndex': 1, 'price': 0.68},
        {'timestamp': KO - 7, 'side': 'BUY', 'outcomeIndex': 0, 'price': 0.33},
        {'timestamp': KO - 10, 'side': 'SELL', 'outcomeIndex': 0, 'price': 0.31},
        {'timestamp': KO - 900, 'side': 'BUY', 'outcomeIndex': 0, 'price': 0.30},
        {'timestamp': KO - 7200, 'side': 'BUY', 'outcomeIndex': 1, 'price': 0.70},
    ]
    out = j.last_buys(trades, KO)
    assert out['buy0_price'] == 0.33 and out['buy1_price'] == 0.68
    assert out['buy0_at'] == datetime.fromtimestamp(KO - 7, tz=UTC)
    assert out['trades_1h'] == 4


def test_kalshi_candles_live_and_historical_field_names():
    live = {'yes_bid': {'close_dollars': '0.81'}, 'yes_ask': {'close_dollars': '0.82'}}
    hist = {'yes_bid': {'close': '0.51'}, 'yes_ask': {'close': '0.52'}}
    assert j.kalshi_mid(live) == 0.815
    assert j.kalshi_mid(hist) == 0.515


def test_kalshi_placeholder_book_has_no_mid_but_keeps_its_quote():
    c = {'end_period_ts': KO - 60, 'yes_bid': {'close_dollars': '0.02'},
         'yes_ask': {'close_dollars': '0.81'}}
    out = j.kalshi_prices([], [c], KO)
    assert out['mid_close'] is None
    assert out['bid_close'] == 0.02 and out['ask_close'] == 0.81


def test_kalshi_close_is_the_last_minute_candle_ending_by_kickoff():
    mk = lambda ts, b, a: {'end_period_ts': ts, 'yes_bid': {'close_dollars': str(b)},  # noqa: E731
                           'yes_ask': {'close_dollars': str(a)}}
    minute = [mk(KO - 120, 0.40, 0.41), mk(KO, 0.42, 0.43), mk(KO + 60, 0.60, 0.61)]
    hourly = [mk(KO - 24 * 3600, 0.30, 0.31), mk(KO - 3600, 0.38, 0.39)]
    out = j.kalshi_prices(hourly, minute, KO)
    assert out['ask_close'] == 0.43 and out['bid_close'] == 0.42
    assert out['mid_24h'] == 0.305 and out['mid_1h'] == 0.385


# ---------------------------------------------------------------------------
# kick-off times
# ---------------------------------------------------------------------------

def test_football_data_time_is_london_local():
    stored = datetime(2025, 8, 15, 20, 0, tzinfo=UTC)      # Liverpool v Bournemouth
    assert j.db_real_kickoff(stored, 'ENG-PR') == datetime(2025, 8, 15, 19, 0, tzinfo=UTC)
    winter = datetime(2026, 1, 17, 15, 0, tzinfo=UTC)
    assert j.db_real_kickoff(winter, 'ENG-PR') == winter
    assert j.db_real_kickoff(datetime(2025, 8, 15, tzinfo=UTC), 'ENG-PR') is None
    assert j.db_real_kickoff(stored, 'BRA-SA') is None


def test_a_listed_start_after_the_match_ended_is_never_the_close():
    # Celta v Real Madrid, 2026-03: listed for the 7th, finished on the 6th.
    listed = datetime(2026, 3, 7, 20, 0, tzinfo=UTC)
    finished = datetime(2026, 3, 6, 22, 1, tzinfo=UTC)
    # Polymarket's finish is a loose end: it rules the listing out and places nothing.
    assert j.settle_kickoff(listed, None, None, finished) == (None, None)
    real = datetime(2026, 3, 6, 20, 0, tzinfo=UTC)
    assert j.settle_kickoff(listed, real, None, finished) == (real, 'match_london')
    # Kalshi's winner declaration is a tight end: it places one, well early.
    ko, src = j.settle_kickoff(listed, None, finished)
    assert src == 'end_estimate' and ko == finished - j.END_ESTIMATE
    assert ko < real


def test_a_stale_early_listing_is_kept_when_only_a_loose_end_knows():
    # Osasuna v Mallorca: listed the 6th 20:00, played the 7th 13:00. A close
    # 17 hours stale is a worse price, never one from inside the match.
    listed = datetime(2026, 3, 6, 20, 0, tzinfo=UTC)
    finished = datetime(2026, 3, 7, 15, 4, tzinfo=UTC)
    assert j.settle_kickoff(listed, None, None, finished) == (listed, 'listed')
    assert j.settle_kickoff(listed, datetime(2026, 3, 7, 13, 0, tzinfo=UTC), None, finished)[1] == \
        'match_london'
    # A tight end that far away calls the listing stale.
    assert j.settle_kickoff(listed, None, finished)[1] == 'end_estimate'


def test_a_consistent_listing_stands_and_a_closed_market_only_rules_out():
    listed = datetime(2026, 9, 20, 13, 0, tzinfo=UTC)
    end = listed + timedelta(minutes=115)
    assert j.settle_kickoff(listed, None, end) == (listed, 'listed')
    assert j.settle_kickoff(listed, None, None, end) == (listed, 'listed')
    assert j.settle_kickoff(listed, None, None) == (listed, 'listed_unchecked')
    # closedTime before the listed start + 100 min: the listing cannot be real,
    # and a closedTime alone cannot place the start.
    assert j.settle_kickoff(listed, None, None, listed + timedelta(minutes=30)) == (None, None)


def test_a_finish_stamped_at_the_listed_start_is_a_placeholder():
    listed = datetime(2026, 3, 21, 5, 0, tzinfo=UTC)          # J2 League, finish == start
    assert j.pm_finish(listed, listed) is None
    later = listed + timedelta(minutes=118)
    assert j.pm_finish(listed, later) == later
    assert j.pm_end_bound(later, later + timedelta(hours=3)) == later
    assert j.pm_end_bound(None, None) is None


def test_link_recovers_a_rescheduled_fixture_from_the_end_of_the_match():
    idx = _index()
    stored = datetime(2026, 3, 6, 20, 0, tzinfo=UTC)          # winter: London == UTC
    by = _by_date([(30, 3, 5, stored, 'ENG-PR')])
    listed = stored + timedelta(days=1)                        # the stale listing
    assert j.link_fixture('Man United', 'Everton', listed, None, idx, by)[0] == 'no_candidate'
    st, lk = j.link_fixture('Man United', 'Everton', listed, None, idx, by,
                            end_hint=stored + timedelta(minutes=121))
    assert st == 'linked' and lk['match_id'] == 30


def test_alias_proposal_needs_one_certain_side_and_the_clock():
    canonical = {1: 'Barcelona', 2: 'Espanol', 3: 'Getafe'}
    idx = j.TeamIndex({k: [v] for k, v in canonical.items()}, canonical)
    idx.pm_aliases = {}
    ko = datetime(2026, 1, 3, 20, 0, tzinfo=UTC)               # winter: London == UTC
    by = _by_date([(40, 3, 2, ko, 'ESP-LL')])
    got = j.alias_proposals('polymarket', 'Getafe CF', 'RCD Espanyol de Barcelona',
                            ko.date(), ko, None, None, idx, by)
    assert got == {('RCD Espanyol de Barcelona', 2)}
    # Twenty minutes off the clock is not the same match.
    late = ko + timedelta(minutes=20)
    assert j.alias_proposals('polymarket', 'Getafe CF', 'RCD Espanyol de Barcelona',
                             late.date(), late, None, None, idx, by) == set()


def test_the_derby_reads_both_ways_and_so_proposes_nothing_usable():
    # "Espanyol de BARCELONA" contains Barcelona, so the derby anchors on both
    # sides and proposes two different spellings for one club. The learner
    # keeps only fixtures with exactly one proposal.
    canonical = {1: 'Barcelona', 2: 'Espanol'}
    idx = j.TeamIndex({k: [v] for k, v in canonical.items()}, canonical)
    idx.pm_aliases = {}
    ko = datetime(2026, 1, 3, 20, 0, tzinfo=UTC)
    by = _by_date([(40, 1, 2, ko, 'ESP-LL')])
    got = j.alias_proposals('polymarket', 'FC Barcelona', 'RCD Espanyol de Barcelona',
                            ko.date(), ko, None, None, idx, by)
    assert len(got) == 2


def test_womens_fixtures_never_reach_our_mens_results():
    assert j.is_womens_fixture('womens-champions-league', None, 'Arsenal WFC', 'Chelsea FC')
    assert j.is_womens_fixture('fa-cup', None, 'Birmingham City WFC', 'Coventry United')
    assert j.is_womens_fixture('KXNWSLGAME', 'NWSL', 'Orlando Pride', 'Gotham FC')
    assert not j.is_womens_fixture('premier-league-2025', 'Premier League', 'Wolves', 'West Ham')


def test_parse_ts_reads_both_gamma_formats():
    want = datetime(2026, 9, 20, 1, 15, tzinfo=UTC)
    assert j.parse_ts('2026-09-20 01:15:00+00') == want
    assert j.parse_ts('2026-09-20T01:15:00Z') == want


# ---------------------------------------------------------------------------
# linking
# ---------------------------------------------------------------------------

def _index():
    canonical = {1: 'Liverpool', 2: 'Bournemouth', 3: 'Man United', 4: 'Man City',
                 5: 'Everton'}
    names = {k: [v] for k, v in canonical.items()}
    names[3].append('Manchester United')
    idx = j.TeamIndex(names, canonical)
    idx.pm_aliases = {}
    return idx


def _by_date(rows):
    out = {}
    for r in rows:
        out.setdefault(r[3].date(), []).append(r)
    return out


def test_link_reads_the_london_time_and_both_orientations():
    idx = _index()
    stored = datetime(2025, 8, 15, 20, 0, tzinfo=UTC)
    by = _by_date([(10, 1, 2, stored, 'ENG-PR')])
    pm_ko = datetime(2025, 8, 15, 19, 0, tzinfo=UTC)
    st, lk = j.link_fixture('Liverpool FC', 'AFC Bournemouth', pm_ko, None, idx, by)
    assert st == 'linked' and lk['match_id'] == 10 and lk['swapped'] is False
    assert lk['db_ko'] == pm_ko
    st, lk = j.link_fixture('Bournemouth', 'Liverpool', pm_ko, None, idx, by)
    assert st == 'linked' and lk['swapped'] is True


def test_link_refuses_a_kickoff_three_hours_away():
    idx = _index()
    by = _by_date([(10, 1, 2, datetime(2025, 8, 15, 20, 0, tzinfo=UTC), 'ENG-PR')])
    st, _ = j.link_fixture('Liverpool', 'Bournemouth',
                           datetime(2025, 8, 15, 23, 30, tzinfo=UTC), None, idx, by)
    assert st == 'no_candidate'


def test_link_fails_closed_on_two_equally_good_matches():
    idx = _index()
    d = datetime(2026, 1, 17, 15, 0, tzinfo=UTC)
    by = _by_date([(20, 3, 5, d, 'ENG-PR'), (21, 3, 5, d + timedelta(hours=1), 'ENG-PR')])
    st, _ = j.link_fixture('Man United', 'Everton', d, None, idx, by)
    assert st == 'ambiguous'


def test_link_honours_not_in_model():
    idx = _index()
    idx.pm_aliases = {'inter miami': j.NOT_IN_MODEL}
    by = _by_date([(10, 1, 2, datetime(2025, 8, 15, 20, 0, tzinfo=UTC), 'ENG-PR')])
    st, _ = j.link_fixture('Inter Miami', 'Liverpool', None, date(2025, 8, 15), idx, by)
    assert st == 'not_in_model'


def test_subject_side_follows_the_match_not_the_venue():
    assert j._subject_side('a', False) == 'home'
    assert j._subject_side('a', True) == 'away'
    assert j._subject_side('b', True) == 'home'
    assert j._subject_side('draw', True) == 'draw'
    assert j._subject_side(None, False) is None


# ---------------------------------------------------------------------------
# Kalshi rows
# ---------------------------------------------------------------------------

def _kx(ticker, event, title, label, result, strike=None):
    return {'ticker': ticker, 'event_ticker': event, 'title': title, 'yes_sub_title': label,
            'result': result, 'settlement_value_dollars': '1.0000' if result == 'yes' else '0.0000',
            'volume_fp': '1000.00', 'floor_strike': strike}


def test_kalshi_fixture_rows_join_totals_and_btts_on_the_ticker_suffix():
    ev = 'KXEPLGAME-26MAY24LFCBRE'
    game = [_kx(f'{ev}-LFC', ev, 'Liverpool vs Brentford Winner?', 'Liverpool', 'no'),
            _kx(f'{ev}-BRE', ev, 'Liverpool vs Brentford Winner?', 'Brentford', 'no'),
            _kx(f'{ev}-TIE', ev, 'Liverpool vs Brentford Winner?', 'Tie', 'yes')]
    total = [_kx('KXEPLTOTAL-26MAY24LFCBRE-2', 'KXEPLTOTAL-26MAY24LFCBRE',
                 'Will over 2.5 goals be scored?', 'Over 2.5 goals scored', 'no', 2.5)]
    btts = [_kx('KXEPLBTTS-26MAY24LFCBRE-BTTS', 'KXEPLBTTS-26MAY24LFCBRE',
                'Will both teams score?', 'Both Teams To Score', 'yes')]
    rows = j.kalshi_fixture_rows('EPL', {'GAME': 'KXEPLGAME', 'title': 'EPL'},
                                 {'GAME': game, 'TOTAL': total, 'BTTS': btts})
    by = {r['market_id']: r for r in rows}
    assert by[f'{ev}-TIE']['subject_team'] == 'draw' and by[f'{ev}-TIE']['winner'] == 0
    assert by[f'{ev}-BRE']['subject_team'] == 'b' and by[f'{ev}-BRE']['winner'] == 1
    tot = by['KXEPLTOTAL-26MAY24LFCBRE-2']
    assert tot['family'] == 'totals' and tot['line'] == 2.5 and tot['team_a'] == 'Liverpool'
    assert by['KXEPLBTTS-26MAY24LFCBRE-BTTS']['family'] == 'btts'
    assert all(r['event_date'] == date(2026, 5, 24) for r in rows)


def test_kalshi_recent_markets_take_the_fixture_from_the_event_title():
    # From ~2026-08 the market title is only "Fulham wins".
    ev = 'KXEPLGAME-26SEP20FULMUN'
    game = [_kx(f'{ev}-FUL', ev, 'Fulham wins', 'Fulham', 'no'),
            _kx(f'{ev}-MUN', ev, 'Manchester United wins', 'Manchester United', 'no'),
            _kx(f'{ev}-TIE', ev, 'Tie is the result', 'Tie', 'yes')]
    assert j.kalshi_fixture_rows('EPL', {'GAME': 'KXEPLGAME'}, {'GAME': game}) == []
    rows = j.kalshi_fixture_rows('EPL', {'GAME': 'KXEPLGAME'}, {'GAME': game},
                                 {ev: 'Fulham vs Manchester United'})
    assert {r['subject_team'] for r in rows} == {'a', 'b', 'draw'}
    assert rows[0]['team_a'] == 'Fulham'


def test_kalshi_fixture_whose_legs_do_not_orient_is_dropped_whole():
    ev = 'KXEPLGAME-26MAY24LFCBRE'
    game = [_kx(f'{ev}-LFC', ev, 'Liverpool vs Brentford Winner?', 'Liverpool', 'no'),
            _kx(f'{ev}-X', ev, 'Liverpool vs Brentford Winner?', 'Somebody Else', 'no'),
            _kx(f'{ev}-TIE', ev, 'Liverpool vs Brentford Winner?', 'Tie', 'yes')]
    assert j.kalshi_fixture_rows('EPL', {'GAME': 'KXEPLGAME'}, {'GAME': game}) == []
