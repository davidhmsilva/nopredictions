"""
status_features.py — who a team IS in its league, and who it is playing, known
before kick-off.

Built from `matches` alone (every league-season we hold, odds or not), so the
tables are complete even where a match has no price.

Per team and league-season, fixed BEFORE the season starts:
  prev_pos   final position last season in this league (NaN if not in it)
  pos3       mean final position over up to the 3 previous seasons in this
             league (only seasons it played in the league)
  rank3      1..n rank of pos3 among this season's teams that were in the league
             last season; newcomers are not ranked
  status     1 elite (rank3 <= 3) · 2 upper (rank3 <= n/2) · 3 lower ·
             4 newcomer (promoted, or relegated INTO the league from above)

Per match, from the table as it stood before kick-off (matches that kick off
at the same instant are applied together, so none sees another's result):
  cur_pos    position in the table; NaN until the team has played 5 games
  played     league games already played this season
  rounds_left  2·(n−1) − played, the scheduled round-robin (split-season
             leagues — Scotland, Belgium — are approximate)

Nothing here reads a result from the match itself or from any later match.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

MIN_PLAYED_FOR_POS = 5
ELITE = 3


def _final_tables(m: pd.DataFrame) -> dict:
    """(league_id, season_start) -> {team_id: final position}."""
    out = {}
    for (lg, ss), g in m.groupby(["league_id", "season_start"], sort=False):
        pts, gd, gf = defaultdict(int), defaultdict(int), defaultdict(int)
        for h, a, hs, as_ in zip(g.home_team_id, g.away_team_id, g.home_score, g.away_score):
            pts[h] += 3 if hs > as_ else (1 if hs == as_ else 0)
            pts[a] += 3 if as_ > hs else (1 if hs == as_ else 0)
            gd[h] += hs - as_
            gd[a] += as_ - hs
            gf[h] += hs
            gf[a] += as_
        teams = set(g.home_team_id) | set(g.away_team_id)
        order = sorted(teams, key=lambda t: (-pts[t], -gd[t], -gf[t], t))
        out[(lg, ss)] = {t: i + 1 for i, t in enumerate(order)}
    return out


def _season_status(m: pd.DataFrame, finals: dict) -> pd.DataFrame:
    rows = []
    seasons = defaultdict(list)
    for lg, ss in finals:
        seasons[lg].append(ss)
    for lg, sss in seasons.items():
        sss = sorted(sss)
        for i, ss in enumerate(sss):
            g = m[(m.league_id == lg) & (m.season_start == ss)]
            teams = sorted(set(g.home_team_id) | set(g.away_team_id))
            n = len(teams)
            prev = finals.get((lg, ss - 1))
            if prev is None:            # no previous season held: status unknown, not "newcomer"
                for t in teams:
                    rows.append((lg, ss, t, n, np.nan, np.nan, np.nan, np.nan))
                continue
            pos3 = {}
            for t in teams:
                if t not in prev:
                    continue
                ps = [finals[(lg, s)][t] for s in (ss - 1, ss - 2, ss - 3)
                      if (lg, s) in finals and t in finals[(lg, s)]]
                pos3[t] = float(np.mean(ps))
            ranked = sorted(pos3, key=lambda t: (pos3[t], prev[t]))
            rank3 = {t: r + 1 for r, t in enumerate(ranked)}
            for t in teams:
                if t in rank3:
                    r = rank3[t]
                    st = 1 if r <= ELITE else (2 if r <= n / 2 else 3)
                    rows.append((lg, ss, t, n, float(prev[t]), pos3[t], float(r), float(st)))
                else:
                    rows.append((lg, ss, t, n, np.nan, np.nan, np.nan, 4.0))
    return pd.DataFrame(rows, columns=["league_id", "season_start", "team_id", "n_teams",
                                       "prev_pos", "pos3", "rank3", "status"])


def _live_tables(m: pd.DataFrame, n_teams: dict) -> pd.DataFrame:
    """Per match: each side's table position and games played before kick-off."""
    out = []
    for (lg, ss), g in m.groupby(["league_id", "season_start"], sort=False):
        g = g.sort_values("kickoff_utc", kind="mergesort")
        pts, gd, gf, pl = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
        teams = set(g.home_team_id) | set(g.away_team_id)
        n = n_teams[(lg, ss)]
        for ko, batch in g.groupby("kickoff_utc", sort=True):
            order = sorted(teams, key=lambda t: (-pts[t], -gd[t], -gf[t], t))
            pos = {t: i + 1 for i, t in enumerate(order)}
            for mid, h, a in zip(batch.id, batch.home_team_id, batch.away_team_id):
                out.append((mid,
                            float(pos[h]) if pl[h] >= MIN_PLAYED_FOR_POS else np.nan,
                            float(pos[a]) if pl[a] >= MIN_PLAYED_FOR_POS else np.nan,
                            float(pl[h]), float(pl[a]),
                            float(max(0, 2 * (n - 1) - max(pl[h], pl[a])))))
            for h, a, hs, as_ in zip(batch.home_team_id, batch.away_team_id,
                                     batch.home_score, batch.away_score):
                pts[h] += 3 if hs > as_ else (1 if hs == as_ else 0)
                pts[a] += 3 if as_ > hs else (1 if hs == as_ else 0)
                gd[h] += hs - as_
                gd[a] += as_ - hs
                gf[h] += hs
                gf[a] += as_
                pl[h] += 1
                pl[a] += 1
    return pd.DataFrame(out, columns=["match_id", "home_cur_pos", "away_cur_pos",
                                      "home_played", "away_played", "rounds_left"])


def build(conn) -> pd.DataFrame:
    """One row per match: status + table features for home and away."""
    m = pd.read_sql_query("""
        SELECT m.id, s.league_id, EXTRACT(YEAR FROM s.start_date)::int AS season_start,
               m.kickoff_utc, m.home_team_id, m.away_team_id, m.home_score, m.away_score
          FROM matches m
          JOIN seasons s ON s.id = m.season_id
          JOIN leagues l ON l.id = s.league_id
         WHERE NOT l.is_international AND NOT l.is_cup
           AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL""", conn)
    m["kickoff_utc"] = pd.to_datetime(m["kickoff_utc"], utc=True)
    finals = _final_tables(m)
    st = _season_status(m, finals)
    n_teams = {(r.league_id, r.season_start): r.n_teams
               for r in st.drop_duplicates(["league_id", "season_start"]).itertuples()}
    live = _live_tables(m, n_teams)
    keep = ["league_id", "season_start", "team_id", "prev_pos", "pos3", "rank3", "status"]
    f = m[["id", "league_id", "season_start", "home_team_id", "away_team_id"]].rename(columns={"id": "match_id"})
    for side in ("home", "away"):
        s = st[keep].rename(columns={c: f"{side}_{c}" for c in ("prev_pos", "pos3", "rank3", "status")})
        f = f.merge(s.rename(columns={"team_id": f"{side}_team_id"}),
                    on=["league_id", "season_start", f"{side}_team_id"], how="left")
    f = f.merge(live, on="match_id", how="left")
    f["n_teams"] = [n_teams.get((a, b)) for a, b in zip(f.league_id, f.season_start)]
    return f
