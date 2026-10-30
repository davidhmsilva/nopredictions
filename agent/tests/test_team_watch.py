"""The team watch list: what has to hold for its CLV to mean anything.

Offline. Each case pins a way the list could report a lag that is not there:
a detector that sees the game it is predicting, a side resolved to the wrong
club, a club watched in the division it just left, a book that is not a price.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import team_watch as tw                                           # noqa: E402


def _match(day, home, away, ftr, code='I1', season=2026, pre=(2.0, 3.4, 4.0), close=(2.0, 3.4, 4.0)):
    r = {'Date': day, 'HomeTeam': home, 'AwayTeam': away, 'FTR': ftr, 'code': code, 'season': season,
         'date': pd.Timestamp(day)}
    for pfx, o in (('BFE', pre), ('Avg', pre), ('Max', pre), ('BFEC', close), ('AvgC', close), ('MaxC', close)):
        r[pfx + 'H'], r[pfx + 'D'], r[pfx + 'A'] = o
    return r


def _season(results, team='Lazio', code='I1', season=2026, start='2026-08-20'):
    days = pd.date_range(start, periods=len(results), freq='7D')
    return pd.DataFrame([_match(str(d.date()), team, f'Opp{i}', ftr, code, season)
                         for i, (d, ftr) in enumerate(zip(days, results))])


def test_power_devig_sums_to_one_and_refuses_a_non_price():
    p = tw.power_devig([2.0, 1.01], [3.4, 1.01], [4.0, 1.01])
    assert p[0].sum() == pytest.approx(1.0, abs=1e-9)
    assert p[0][0] > p[0][2]                       # the favourite stays the favourite
    assert np.isnan(p[1]).all()                    # overround ~2.97: not a book


def test_the_detector_never_sees_the_game_it_scores():
    t = tw.team_games(_season(['H'] * 7 + ['A']))
    lz = t[t.team == 'Lazio'].reset_index(drop=True)
    for k in range(1, len(lz)):
        assert lz.resid_before[k] == pytest.approx(lz.resid[:k].mean())
    assert np.isnan(lz.resid_before[0])
    # the 8th game was lost; that loss must not be in the reading that decided to back it
    assert lz.resid_before[7] == pytest.approx(lz.resid[:7].mean())


def test_dynamic_rule_waits_for_min_games():
    t = tw.team_games(_season(['H'] * 9))
    lz = t[t.team == 'Lazio'].reset_index(drop=True)
    assert not lz.dynamic_on[:tw.MIN_GAMES].any()
    assert lz.dynamic_on[tw.MIN_GAMES:].all()      # winning every game at 2.00 is well above +0.30


def test_move_and_clv_on_a_hand_example():
    d = pd.DataFrame([_match('2026-09-01', 'Lazio', 'Roma', 'H', pre=(2.2, 3.4, 3.4), close=(2.0, 3.5, 3.9))])
    lz = tw.team_games(d).query("team == 'Lazio'").iloc[0]
    p_pre = tw.power_devig([2.2], [3.4], [3.4])[0][0]
    p_close = tw.power_devig([2.0], [3.5], [3.9])[0][0]
    assert lz.move_pp == pytest.approx((p_close - p_pre) * 100)
    assert lz.move_pp > 0                          # the price shortened toward Lazio
    assert lz.clv_best == pytest.approx(2.2 * p_close - 1)


def test_frozen_list_follows_a_club_that_changed_division():
    prev = _season(['H'] * 30, team='Hull', code='E1', season=2025, start='2025-08-01')
    now = _season(['H'] * 6, team='Hull', code='E0', season=2026, start='2026-08-20')
    others = pd.concat([_season(['A'] * 30, team=f'X{i}', code='E1', season=2025, start='2025-08-01')
                        for i in range(3)])
    t = tw.team_games(pd.concat([prev, now, others], ignore_index=True))
    w = tw.build_frozen(t, pd.Timestamp('2026-10-11').date())
    hull = [e for e in w['teams'] if e['team'] == 'Hull']
    assert len(hull) == 1 and hull[0]['code'] == 'E0'


@pytest.fixture(scope='module')
def resolver():
    clubs = {'SC0': ['Dundee', 'Dundee United', 'Celtic'], 'F1': ['Paris FC', 'Paris SG', 'Lens'],
             'D1': ['RB Leipzig', 'Ein Frankfurt', 'Bayern Munich'], 'SP1': ['Ath Bilbao', 'Ath Madrid']}
    rows = [{'season': 2026, 'code': c, 'team': n} for c, ns in clubs.items() for n in ns]
    return tw.Resolver(pd.DataFrame(rows), 2026)


@pytest.mark.parametrize('pm, code, fd', [
    ('Dundee FC', 'SC0', 'Dundee'),                    # ties with Dundee United under the scorer
    ('Dundee United FC', 'SC0', 'Dundee United'),
    ('Paris FC', 'F1', 'Paris FC'),                    # ties with Paris SG
    ('Paris Saint-Germain FC', 'F1', 'Paris SG'),
    ('RB Leipzig', 'D1', 'RB Leipzig'),                # alias target "Leipzig" is not FD's spelling
    ('Eintracht Frankfurt', 'D1', 'Ein Frankfurt'),
    ('Athletic Club', 'SP1', 'Ath Bilbao'),            # never Ath Madrid
])
def test_resolver_names_the_right_club(resolver, pm, code, fd):
    assert resolver.club(pm, code) == fd


def test_resolver_fails_closed(resolver):
    assert resolver.fixture('Dundee FC', 'Some Unknown Club', 'SC0') == (None, None)
    assert resolver.fixture('Dundee FC', 'Dundee', 'SC0') == (None, None)        # one club twice
    assert resolver.club('Dundee FC', 'F1') is None                               # wrong league


def test_cluster_ci_resamples_clubs_not_games():
    # one club with 100 identical games must not look like 100 independent observations
    v = [1.0] * 100 + [0.0, 0.0]
    c = ['a'] * 100 + ['b', 'c']
    m, lo, hi, n = tw.cluster_ci(v, c, B=500)
    assert n == 102 and m == pytest.approx(100 / 102)
    assert lo < 0.5                                # dropping club "a" is a possible resample
