"""Status and table features must be known before kick-off."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from factory import status_features as S  # noqa: E402


def _season(ss, results, start_id=0):
    """results: list of (home, away, hs, as) played one round per day."""
    rows = []
    for i, (h, a, hs, as_) in enumerate(results):
        rows.append({"id": start_id + i, "league_id": 1, "season_start": ss,
                     "kickoff_utc": pd.Timestamp(f"{ss}-08-01", tz="UTC") + pd.Timedelta(days=i // 2),
                     "home_team_id": h, "away_team_id": a, "home_score": hs, "away_score": as_})
    return rows


def _matches():
    # 2010: 1 > 2 > 3 > 4 ; 2011: 5 replaces 4 (promoted)
    s1 = _season(2010, [(1, 2, 2, 0), (3, 4, 1, 0), (1, 3, 1, 0), (2, 4, 1, 0),
                        (1, 4, 3, 0), (2, 3, 2, 1)])
    s2 = _season(2011, [(1, 2, 0, 1), (3, 5, 0, 0), (1, 3, 1, 1), (2, 5, 1, 0),
                        (1, 5, 0, 2), (2, 3, 0, 0)], start_id=100)
    return pd.DataFrame(s1 + s2)


def test_final_positions_and_newcomers():
    m = _matches()
    finals = S._final_tables(m)
    assert finals[(1, 2010)] == {1: 1, 2: 2, 3: 3, 4: 4}
    st = S._season_status(m, finals).set_index(["season_start", "team_id"])
    # the first season held has no history: unknown, never "newcomer"
    assert st.loc[(2010, 1), "status"] != st.loc[(2010, 1), "status"]
    assert st.loc[(2011, 5), "status"] == 4
    assert st.loc[(2011, 1), "rank3"] == 1 and st.loc[(2011, 1), "status"] == 1
    # the status of 2011 does not depend on anything played in 2011
    assert st.loc[(2011, 2), "prev_pos"] == 2


def test_table_position_never_sees_the_match_or_its_kickoff_batch():
    m = _matches()
    live = S._live_tables(m, {(1, 2010): 4, (1, 2011): 4}).set_index("match_id")
    # nobody has 5 games before any of these, so no position is claimed
    assert live["home_cur_pos"].isna().all()
    # two matches on the same day see the same played count
    assert live.loc[0, "home_played"] == 0 and live.loc[1, "home_played"] == 0
    assert live.loc[2, "home_played"] == 1
    assert live.loc[0, "rounds_left"] == 6


def test_min_played_gate_releases_a_position():
    rows = []
    for i in range(12):                      # 1 beats 2 twelve times, one game a day
        rows.append({"id": i, "league_id": 1, "season_start": 2010,
                     "kickoff_utc": pd.Timestamp("2010-08-01", tz="UTC") + pd.Timedelta(days=i),
                     "home_team_id": 1 if i % 2 == 0 else 2, "away_team_id": 2 if i % 2 == 0 else 1,
                     "home_score": 1 if i % 2 == 0 else 0, "away_score": 0 if i % 2 == 0 else 1})
    live = S._live_tables(pd.DataFrame(rows), {(1, 2010): 2}).set_index("match_id")
    assert np.isnan(live.loc[4, "home_cur_pos"])       # 4 played
    assert live.loc[5, "away_cur_pos"] == 1             # team 1, 5 played, top
    assert live.loc[6, "home_cur_pos"] == 1
