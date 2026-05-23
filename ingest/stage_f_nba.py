#!/usr/bin/env python3
"""
Stage F: NBA data ingestion via nba_api.

Downloads game results for configured seasons (regular season + playoffs),
upserts into the existing schema (leagues, teams, matches), and computes
Elo ratings from scratch.

Usage:
    # Full run (2014-15 → current, ~10 seasons)
    python stage_f_nba.py

    # Specific seasons
    python stage_f_nba.py --seasons 2024-25 2025-26

    # Only playoffs
    python stage_f_nba.py --playoffs-only

    # Dry run
    python stage_f_nba.py --dry-run

    # Recalculate Elo from existing DB data (no fetch)
    python stage_f_nba.py --elo-only
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [nba_ingest] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

# NBA league code in our DB
NBA_LEAGUE_CODE = "USA-NBA"
NBA_LEAGUE_NAME = "NBA"
NBA_COUNTRY = "USA"

# Seasons to ingest (nba_api format: "2024-25" = the 2024-25 season)
DEFAULT_SEASONS = [
    "2014-15", "2015-16", "2016-17", "2017-18", "2018-19",
    "2019-20", "2020-21", "2021-22", "2022-23", "2023-24",
    "2024-25", "2025-26",
]

# Elo parameters
ELO_INITIAL = 1500
ELO_K = 20
ELO_HOME_ADV = 100
ELO_SEASON_REGRESS = 0.25  # regress 25% to mean each season


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DATABASE_URL)


def _ensure_league(conn) -> int:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO leagues (code, name, country, tier, is_cup)
        VALUES (%s, %s, %s, 1, FALSE)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (NBA_LEAGUE_CODE, NBA_LEAGUE_NAME, NBA_COUNTRY))
    league_id = cur.fetchone()[0]
    conn.commit()
    return league_id


def _ensure_season(conn, league_id: int, label: str) -> int:
    # NBA season "2024-25" runs roughly Oct 2024 → Jun 2025
    start_year = int(label[:4])
    start_date = date(start_year, 10, 1)
    end_date = date(start_year + 1, 6, 30)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO seasons (league_id, label, start_date, end_date)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (league_id, label) DO UPDATE SET
            start_date = EXCLUDED.start_date,
            end_date = EXCLUDED.end_date
        RETURNING id
    """, (league_id, label, start_date, end_date))
    season_id = cur.fetchone()[0]
    conn.commit()
    return season_id


def _ensure_team(conn, name: str, abbrev: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM teams WHERE LOWER(canonical_name) = LOWER(%s) LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "INSERT INTO teams (canonical_name, country) VALUES (%s, 'USA') RETURNING id",
        (name,),
    )
    team_id = cur.fetchone()[0]

    # Add aliases: full name + abbreviation
    for alias_src, alias_val in [("nba_api", name), ("nba_api_abbrev", abbrev)]:
        cur.execute("""
            INSERT INTO team_aliases (team_id, source, alias)
            VALUES (%s, %s, %s)
            ON CONFLICT (source, alias) DO NOTHING
        """, (team_id, alias_src, alias_val))

    conn.commit()
    return team_id


def _upsert_match(conn, season_id: int, home_id: int, away_id: int,
                   kickoff: datetime, home_score: int, away_score: int,
                   game_id: str, season_type: str) -> int:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO matches
            (season_id, home_team_id, away_team_id, kickoff_utc,
             status, home_score, away_score, fd_source)
        VALUES (%s, %s, %s, %s, 'finished', %s, %s, %s)
        ON CONFLICT (season_id, home_team_id, away_team_id, kickoff_utc)
        DO UPDATE SET
            home_score = EXCLUDED.home_score,
            away_score = EXCLUDED.away_score,
            status = 'finished',
            updated_at = NOW()
        RETURNING id
    """, (season_id, home_id, away_id, kickoff, home_score, away_score,
          f"nba_api:{game_id}:{season_type}"))
    match_id = cur.fetchone()[0]
    return match_id


# ── NBA API fetching ─────────────────────────────────────────────────────────

def _fetch_season_games(season: str, season_type: str) -> list[dict]:
    """Fetch all games for a season+type via nba_api."""
    from nba_api.stats.endpoints import leaguegamefinder

    log.info(f"  Fetching {season} {season_type}...")
    gf = leaguegamefinder.LeagueGameFinder(
        season_nullable=season,
        league_id_nullable="00",
        season_type_nullable=season_type,
        timeout=30,
    )
    df = gf.get_data_frames()[0]
    if df.empty:
        return []

    # Each game appears twice (one row per team). Group by GAME_ID.
    games = {}
    for _, row in df.iterrows():
        gid = row["GAME_ID"]
        if gid not in games:
            games[gid] = {"game_id": gid, "date": row["GAME_DATE"], "rows": []}
        games[gid]["rows"].append(row)

    results = []
    for gid, g in games.items():
        if len(g["rows"]) != 2:
            continue
        r1, r2 = g["rows"]
        # Home team has "vs." in MATCHUP, away has "@"
        if "vs." in r1["MATCHUP"]:
            home, away = r1, r2
        elif "vs." in r2["MATCHUP"]:
            home, away = r2, r1
        else:
            continue

        results.append({
            "game_id": gid,
            "date": g["date"],
            "home_team": home["TEAM_NAME"],
            "home_abbrev": home["TEAM_ABBREVIATION"],
            "away_team": away["TEAM_NAME"],
            "away_abbrev": away["TEAM_ABBREVIATION"],
            "home_score": int(home["PTS"]),
            "away_score": int(away["PTS"]),
            "season_type": season_type,
        })

    return results


# ── Elo model ────────────────────────────────────────────────────────────────

class NBAElo:
    """Simple Elo rating system for NBA with home advantage and season regression."""

    def __init__(self, k: float = ELO_K, home_adv: float = ELO_HOME_ADV,
                 season_regress: float = ELO_SEASON_REGRESS):
        self.k = k
        self.home_adv = home_adv
        self.season_regress = season_regress
        self.ratings: dict[str, float] = {}
        self.history: list[dict] = []

    def _get(self, team: str) -> float:
        if team not in self.ratings:
            self.ratings[team] = ELO_INITIAL
        return self.ratings[team]

    def expected(self, home: str, away: str) -> float:
        """Expected win probability for home team."""
        diff = self._get(home) - self._get(away) + self.home_adv
        return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

    def predict(self, home: str, away: str) -> dict:
        """Return home/away win probabilities."""
        p_home = self.expected(home, away)
        return {"home_win": round(p_home, 4), "away_win": round(1.0 - p_home, 4)}

    def update(self, home: str, away: str, home_won: bool,
               margin: int = 0, game_date: str = "") -> dict:
        """Update ratings after a game. Returns pre-game predictions."""
        pre = self.predict(home, away)

        # Margin-of-victory multiplier (capped)
        mov_mult = max(1.0, math.log(abs(margin) + 1) * 0.6) if margin != 0 else 1.0

        actual_home = 1.0 if home_won else 0.0
        delta = self.k * mov_mult * (actual_home - pre["home_win"])

        self.ratings[home] = self._get(home) + delta
        self.ratings[away] = self._get(away) - delta

        record = {
            "date": game_date,
            "home": home,
            "away": away,
            "home_elo_pre": round(self._get(home) - delta, 1),
            "away_elo_pre": round(self._get(away) + delta, 1),
            "home_elo_post": round(self._get(home), 1),
            "away_elo_post": round(self._get(away), 1),
            "pred_home": pre["home_win"],
            "pred_away": pre["away_win"],
            "home_won": home_won,
            "margin": margin,
        }
        self.history.append(record)
        return pre

    def regress_to_mean(self):
        """Regress all ratings toward 1500 at season boundary."""
        for team in self.ratings:
            self.ratings[team] = (
                ELO_INITIAL * self.season_regress
                + self.ratings[team] * (1 - self.season_regress)
            )
        log.info(f"  Elo season regression applied ({self.season_regress:.0%} to mean)")

    def save(self, path: str):
        """Save current ratings to JSON."""
        data = {
            "ratings": dict(sorted(self.ratings.items(), key=lambda x: -x[1])),
            "params": {
                "k": self.k,
                "home_adv": self.home_adv,
                "season_regress": self.season_regress,
                "n_games": len(self.history),
            },
            "updated_at": datetime.now().isoformat(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        log.info(f"Saved Elo ratings to {path} ({len(self.ratings)} teams)")

    @classmethod
    def load(cls, path: str) -> "NBAElo":
        with open(path) as f:
            data = json.load(f)
        elo = cls(
            k=data["params"]["k"],
            home_adv=data["params"]["home_adv"],
            season_regress=data["params"]["season_regress"],
        )
        elo.ratings = data["ratings"]
        return elo


# ── Main pipeline ────────────────────────────────────────────────────────────

def ingest(seasons: list[str], playoffs_only: bool = False,
           dry_run: bool = False) -> dict:
    """Fetch NBA games and upsert into DB."""

    conn = None if dry_run else _conn()
    league_id = None if dry_run else _ensure_league(conn)

    team_cache: dict[str, int] = {}
    total_games = 0
    total_upserted = 0

    for season in seasons:
        season_id = None if dry_run else _ensure_season(conn, league_id, season)

        types = ["Playoffs"] if playoffs_only else ["Regular Season", "Playoffs"]
        for stype in types:
            games = _fetch_season_games(season, stype)
            log.info(f"  {season} {stype}: {len(games)} games")
            total_games += len(games)

            if dry_run:
                continue

            for g in games:
                # Ensure teams
                for role in ["home", "away"]:
                    tname = g[f"{role}_team"]
                    if tname not in team_cache:
                        team_cache[tname] = _ensure_team(
                            conn, tname, g[f"{role}_abbrev"]
                        )

                kickoff = datetime.strptime(g["date"], "%Y-%m-%d")
                _upsert_match(
                    conn, season_id,
                    team_cache[g["home_team"]],
                    team_cache[g["away_team"]],
                    kickoff,
                    g["home_score"], g["away_score"],
                    g["game_id"], stype,
                )
                total_upserted += 1

            if not dry_run:
                conn.commit()

            time.sleep(0.6)  # rate limit

    if conn:
        conn.close()

    log.info(f"Ingestion complete: {total_games} games fetched, {total_upserted} upserted")
    return {"fetched": total_games, "upserted": total_upserted}


def build_elo(output_path: str) -> NBAElo:
    """Build Elo ratings from all NBA games in the DB."""
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT m.id, m.kickoff_utc, m.home_score, m.away_score,
               m.fd_source,
               ht.canonical_name AS home_team,
               at.canonical_name AS away_team,
               s.label AS season_label
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        JOIN seasons s ON s.id = m.season_id
        JOIN leagues l ON l.id = s.league_id
        WHERE l.code = %s
          AND m.status = 'finished'
          AND m.home_score IS NOT NULL
        ORDER BY m.kickoff_utc ASC
    """, (NBA_LEAGUE_CODE,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        log.warning("No NBA games found in DB. Run ingestion first.")
        return NBAElo()

    log.info(f"Building Elo from {len(rows)} NBA games...")

    elo = NBAElo()
    prev_season = None

    for row in rows:
        season = row["season_label"]
        if prev_season and season != prev_season:
            elo.regress_to_mean()
        prev_season = season

        margin = row["home_score"] - row["away_score"]
        home_won = margin > 0
        elo.update(
            row["home_team"], row["away_team"],
            home_won, margin,
            game_date=str(row["kickoff_utc"])[:10],
        )

    # Save
    elo.save(output_path)

    # Print top/bottom ratings
    sorted_ratings = sorted(elo.ratings.items(), key=lambda x: -x[1])
    log.info("Top 10 Elo ratings:")
    for team, rating in sorted_ratings[:10]:
        log.info(f"  {rating:.0f}  {team}")
    log.info("Bottom 5:")
    for team, rating in sorted_ratings[-5:]:
        log.info(f"  {rating:.0f}  {team}")

    # Accuracy check on last season
    if elo.history:
        last_season_games = [h for h in elo.history if "2025" in h["date"]]
        if last_season_games:
            correct = sum(
                1 for h in last_season_games
                if (h["pred_home"] > 0.5) == h["home_won"]
            )
            acc = correct / len(last_season_games)
            log.info(f"2025-26 accuracy: {acc:.1%} ({correct}/{len(last_season_games)})")

    return elo


def main():
    parser = argparse.ArgumentParser(description="NBA data ingestion + Elo")
    parser.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    parser.add_argument("--playoffs-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--elo-only", action="store_true",
                        help="Skip fetch, only rebuild Elo from DB")
    parser.add_argument("--elo-output", default=os.path.join(
        os.path.dirname(__file__), "..", "agent", "nba_elo_ratings.json"))
    args = parser.parse_args()

    if not args.elo_only:
        log.info(f"Ingesting {len(args.seasons)} NBA seasons...")
        result = ingest(args.seasons, args.playoffs_only, args.dry_run)
        log.info(f"Done: {result}")

    if not args.dry_run:
        log.info("Building Elo ratings...")
        build_elo(args.elo_output)


if __name__ == "__main__":
    main()
