#!/usr/bin/env python3
"""
Regression test for closing_collector handicap parsing + outcome mapping.

Guards the sign-convention bug fixed 2026-05-31: `_parse_handicap` used to
abs()-collapse the line sign and pair non-complementary sides, so
`home_wins_by_Nplus` resolved to the *favorable* (home +N.5) line instead of
the give-the-goals (home -N.5) line — producing fake sharp-validated handicap
edges (e.g. a home underdog "winning by 2+" priced at 77%).

Uses REAL Pinnacle Asian-Handicap blobs captured from api-football on
2026-05-31 (fixtures 1392216 home-underdog, 1392217 home-favorite). Run with:

    cd agent && source ../ingest/.venv/bin/activate
    python tests/test_handicap_mapping.py        # or: pytest tests/
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import closing_collector as cc


# --- Real raw Pinnacle Asian Handicap values (api-football bet id=4) ----------

# Fixture 1392217 — HOME FAVORITE (DNB home ~0.77). Has an explicit home -1.5 line.
AH_HOME_FAVORITE = [
    {'value': 'Home -1.25', 'odd': '2.37'}, {'value': 'Away -1.25', 'odd': '1.62'},
    {'value': 'Home -1',    'odd': '2.08'}, {'value': 'Away -1',    'odd': '1.82'},
    {'value': 'Home -0.75', 'odd': '1.79'}, {'value': 'Away -0.75', 'odd': '2.08'},
    {'value': 'Home -0.5',  'odd': '1.63'}, {'value': 'Away -0.5',  'odd': '2.36'},
    {'value': 'Home -0.25', 'odd': '1.43'}, {'value': 'Away -0.25', 'odd': '2.91'},
    {'value': 'Home +0',    'odd': '1.24'}, {'value': 'Away +0',    'odd': '4.25'},
    {'value': 'Home +0.25', 'odd': '1.21'}, {'value': 'Away +0.25', 'odd': '4.68'},
    {'value': 'Home -2',    'odd': '4.32'}, {'value': 'Away -2',    'odd': '1.23'},
    {'value': 'Home -1.75', 'odd': '3.23'}, {'value': 'Away -1.75', 'odd': '1.36'},
    {'value': 'Home -1.5',  'odd': '2.68'}, {'value': 'Away -1.5',  'odd': '1.50'},
]

# Fixture 1392216 — HOME UNDERDOG (away heavy fav). Has an explicit home +1.5 line.
AH_HOME_UNDERDOG = [
    {'value': 'Home -1',    'odd': '4.41'}, {'value': 'Away -1',    'odd': '1.21'},
    {'value': 'Home -0.75', 'odd': '3.19'}, {'value': 'Away -0.75', 'odd': '1.35'},
    {'value': 'Home -0.5',  'odd': '4.54'}, {'value': 'Away -0.5',  'odd': '1.22'},
    {'value': 'Home +0',    'odd': '3.92'}, {'value': 'Away +0',    'odd': '1.27'},
    {'value': 'Home +0.5',  'odd': '2.25'}, {'value': 'Away +0.5',  'odd': '1.68'},
    {'value': 'Home +1',    'odd': '1.68'}, {'value': 'Away +1',    'odd': '2.25'},
    {'value': 'Home +1.5',  'odd': '1.41'}, {'value': 'Away +1.5',  'odd': '2.98'},
]


def _parse(values):
    result = {}
    cc._parse_handicap(values, result, prefix='')
    return result


def test_complementary_sides_sum_to_one():
    """Each market's two stored sides must vig-remove to ~1.0."""
    blob = _parse(AH_HOME_FAVORITE)
    # home -1.5 line: home side (m1_5) + away side (+1.5) of the SAME market.
    assert abs(blob['spread_home_m1_5'] + blob['spread_away_1_5'] - 1.0) < 1e-6
    # home +0.25 line: home side + away side (-0.25 = m0_25).
    assert abs(blob['spread_home_0_25'] + blob['spread_away_m0_25'] - 1.0) < 1e-6


def test_home_favorite_wins_by_2plus_below_h2h():
    """SANITY: P(home wins by 2+) must sit well below the home-win prob."""
    blob = _parse(AH_HOME_FAVORITE)
    # DNB home from the +0 line as a proxy upper bound on the home-win prob.
    dnb_home = blob['spread_home_0_0']        # ~0.77
    win_by_2 = blob['spread_home_m1_5']       # P(home -1.5) ~0.36
    assert win_by_2 < dnb_home - 0.10, (win_by_2, dnb_home)
    # And it must equal what the outcome mapping returns.
    mapped = cc._outcome_to_closing_prob('home_wins_by_2plus', {'h2h': {}, **blob})
    assert mapped == win_by_2, (mapped, win_by_2)
    # The OLD bug returned the favorable +1.5 line (~0.64) — guard against it.
    assert mapped < 0.5, mapped


def test_home_underdog_away_wins_by_2plus():
    """Away-favorite blob: away wins-by-2+ maps to away -1.5 (the 'm' key)."""
    blob = _parse(AH_HOME_UNDERDOG)
    away_win_by_2 = blob['spread_away_m1_5']  # P(away -1.5)
    mapped = cc._outcome_to_closing_prob('away_wins_by_2plus', {'h2h': {}, **blob})
    assert mapped == away_win_by_2, (mapped, away_win_by_2)
    # Home underdog: P(home wins by 2+) line isn't quoted -> mapping returns None,
    # never the favorable home +1.5 value (~0.68).
    home_mapped = cc._outcome_to_closing_prob('home_wins_by_2plus', {'h2h': {}, **blob})
    assert home_mapped is None, home_mapped


def test_resolver_and_collector_agree_on_key():
    """closing_collector and resolver must build the SAME spread key."""
    import resolver
    blob = _parse(AH_HOME_FAVORITE)
    snap = {'h2h': {}, 'home': 'Switzerland', 'away': 'Jordan', **blob}
    a = cc._outcome_to_closing_prob('home_wins_by_2plus', snap)
    b = resolver._get_closing_from_sharp_snapshot(snap, 'home_wins_by_2plus')
    assert a is not None and a == b, (a, b)


if __name__ == '__main__':
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'  PASS  {name}')
            except AssertionError as e:
                failures += 1
                print(f'  FAIL  {name}: {e}')
    print('OK' if not failures else f'{failures} FAILURE(S)')
    sys.exit(1 if failures else 0)
