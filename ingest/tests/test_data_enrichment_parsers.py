"""
The parsing traps behind Stages K, L and M (2026-10-03), pinned.

Every case below is a shape the live sources actually returned, and every one
of them would have produced a plausible table rather than an error:

  * ESPN writes stoppage time as "45'+2'" / "90'+5'": a 45'+2' goal is a
    FIRST-half goal, and it is exactly what an HT market pays on.
  * ESPN credits an own goal to the team that benefits, so the timeline adds up
    to the final score; when it does not, the HT score must stay unknown.
  * Kalshi's live candle route names the close `close_dollars`, the historical
    route `close`: reading one shape stored every historical quote as None.
  * Football-Data's 1314_SC2.csv carries BbAHh = -275 for -2.75.
  * The /new/ files mix "2012/2013" and "2014" season labels in one country,
    and their times are UK local time.

Run with:
    cd ingest && python -m pytest tests/test_data_enrichment_parsers.py -q
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import stage_k_fd_extra as k        # noqa: E402
import stage_l_espn_events as l     # noqa: E402
import stage_m_price_paths as m     # noqa: E402


# --- ESPN (Stage L) ----------------------------------------------------------

def test_clock_parses_stoppage_time():
    assert l.parse_clock("60'") == (60, 0)
    assert l.parse_clock("45'+2'") == (45, 2)
    assert l.parse_clock("90'+5'") == (90, 5)
    assert l.parse_clock(None) == (None, 0)


def test_first_half_stoppage_is_first_half():
    assert l.period_of(45, False) == 1          # 45'+2' keeps base minute 45
    assert l.period_of(46, False) == 2
    assert l.period_of(90, False) == 2          # 90'+5' is still the second half
    assert l.period_of(105, False) == 3
    assert l.period_of(120, False) == 4
    assert l.period_of(120, True) == 5          # shootout


def _event(details, hs, as_, status='STATUS_FULL_TIME', completed=True):
    return {
        'id': '1', 'date': '2024-08-17T11:30Z', 'season': {'year': 2024, 'slug': '2024-25'},
        'competitions': [{
            'status': {'type': {'name': status, 'completed': completed}},
            'competitors': [
                {'homeAway': 'home', 'score': str(hs), 'team': {'id': '10', 'displayName': 'Home FC'}},
                {'homeAway': 'away', 'score': str(as_), 'team': {'id': '20', 'displayName': 'Away FC'}},
            ],
            'details': details,
        }],
    }


def _goal(team, clock, **flags):
    d = {'type': {'text': 'Goal'}, 'clock': {'displayValue': clock}, 'team': {'id': team},
         'scoringPlay': True}
    d.update(flags)
    return d


def test_own_goal_counts_for_the_beneficiary_and_ht_score():
    row, ev = l.parse_event(_event([
        _goal('20', "45'+2'", ownGoal=True),     # own goal by a home player, credited to away
        _goal('10', "90'+4'"),
    ], 1, 1), 'eng.1')
    assert row['timeline_ok'] is True
    assert (row['home_score_ht'], row['away_score_ht']) == (0, 1)
    assert ev[0]['kind'] == 'own_goal' and ev[0]['team_side'] == 'A' and ev[0]['period'] == 1


def test_timeline_that_misses_a_goal_leaves_ht_unknown():
    row, _ = l.parse_event(_event([_goal('10', "12'")], 2, 0), 'eng.1')
    assert row['timeline_ok'] is False
    assert row['home_score_ht'] is None and row['away_score_ht'] is None


def test_red_card_and_shootout_kinds():
    assert l.kind_of({'redCard': True}) == 'red'
    assert l.kind_of({'yellowCard': True}) == 'yellow'
    assert l.kind_of({'shootout': True, 'scoringPlay': True}) == 'shootout_goal'
    assert l.kind_of({'scoringPlay': True, 'penaltyKick': True}) == 'penalty_goal'


# --- Kalshi (Stage M) --------------------------------------------------------

def test_kalshi_reads_both_candle_shapes():
    assert m._dollars({'close_dollars': '0.4500'}) == 0.45      # live route
    assert m._dollars({'close': '0.2800'}) == 0.28              # historical route
    assert m._dollars({}) is None
    assert m._dollars(None) is None


# --- Football-Data (Stage K) -------------------------------------------------

def test_ah_line_rejects_typos_and_off_grid_lines():
    assert k.ah_line('-2.75') == -2.75
    assert k.ah_line('0') == 0.0
    assert k.ah_line('-275') is None        # 1314_SC2.csv
    assert k.ah_line('0.3') is None         # not a quarter line
    assert k.ah_line('') is None


def test_prices_must_be_prices():
    assert k.fnum('1.91') == 1.91
    assert k.fnum('0') is None              # Football-Data writes 0.00 for "no price"
    assert k.fnum('1.0') is None
    assert k.fnum('') is None


def test_season_labels():
    assert k.season_label('JPN-J1', '2026/2027', False)[0] == '2026-27'
    assert k.season_label('MEX-LMX', '2020/2021', True)[0] == '2020'   # Stage G's style
    assert k.season_label('ARG-PD', '2014', True)[0] == '2014'


def test_new_files_are_uk_local_time():
    # J1, Gamba Osaka v Vissel Kobe: "09:00" in the file, 08:00Z on ESPN (BST).
    assert k.uk_to_utc('20/09/2026', '09:00') == datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    # MLS, Colorado v Columbus, March 2012 (GMT): "23:00" is 23:00Z.
    assert k.uk_to_utc('10/03/2012', '23:00') == datetime(2012, 3, 10, 23, 0, tzinfo=timezone.utc)
