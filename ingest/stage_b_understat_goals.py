#!/usr/bin/env python3
"""
Stage B (v3): Understat goal-event ingestion.

Pulls minute-level goal events from Understat shot data and populates
`goal_events`. Powers the late-goal strategy and any timing-based research.

Coverage:
  Leagues : EPL, La_Liga, Bundesliga, Serie_A, Ligue_1
  Seasons : 2014 -> 2024  (i.e. 2014/15 through 2024/25)

Two-pass:
  1. Pull season match list (one HTTP call per league/season).
  2. For each match in our DB, pull shots and filter result IN ('Goal','OwnGoal').

Usage:
    python stage_b_understat_goals.py --dry-run --leagues EPL --seasons 2023
    python stage_b_understat_goals.py --leagues EPL                    # one league full
    python stage_b_understat_goals.py                                  # full run
    python stage_b_understat_goals.py --continue-on-error
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date, timedelta
from difflib import SequenceMatcher

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL')
RATE_SECS    = 2.0      # per-match shot fetch

LEAGUE_MAP = {
    'EPL':        'ENG-PR',
    'La_Liga':    'ESP-LL',
    'Bundesliga': 'GER-BL1',
    'Serie_A':    'ITA-SA',
    'Ligue_1':    'FRA-L1',
}

SEASONS = list(range(2014, 2025))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def load_team_aliases(cur) -> dict[str, int]:
    cur.execute("SELECT LOWER(alias), team_id FROM team_aliases")
    return {row[0]: row[1] for row in cur.fetchall()}


def load_match_index(cur) -> dict[tuple, int]:
    cur.execute("""
        SELECT DATE(kickoff_utc)::text, home_team_id, away_team_id, id
        FROM matches
        WHERE kickoff_utc >= '2014-01-01'
    """)
    return {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}


def load_goals_done(cur) -> set[int]:
    """Match ids that already have at least one goal_event from understat."""
    cur.execute("SELECT DISTINCT match_id FROM goal_events WHERE source = 'understat'")
    return {r[0] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Team name matching (same logic as stage_b_understat.py)
# ---------------------------------------------------------------------------

def fuzzy_match(name: str, aliases: dict[str, int], threshold: float = 0.7) -> int | None:
    key = name.lower().strip()
    if key in aliases:
        return aliases[key]
    for variant in [
        key.replace(' fc', '').strip(),
        key.replace('fc ', '').strip(),
        key.replace(' cf', '').strip(),
        key.replace('.', '').strip(),
    ]:
        if variant in aliases:
            return aliases[variant]
    best_score, best_id = 0.0, None
    for alias, tid in aliases.items():
        s = SequenceMatcher(None, key, alias).ratio()
        if s > best_score:
            best_score, best_id = s, tid
    return best_id if best_score >= threshold else None


def resolve_match_id(date_str: str, home_id: int, away_id: int,
                     match_index: dict[tuple, int]) -> int | None:
    mid = match_index.get((date_str, home_id, away_id))
    if mid:
        return mid
    # Tolerate ±1 day timezone drift
    d = date.fromisoformat(date_str)
    for delta in (-1, 1):
        mid = match_index.get(((d + timedelta(days=delta)).isoformat(), home_id, away_id))
        if mid:
            return mid
    return None


# ---------------------------------------------------------------------------
# Shot -> goal_event mapping
# ---------------------------------------------------------------------------

def shot_to_goal(shot: dict) -> dict | None:
    """Convert an Understat shot dict to a goal_event dict, or None."""
    result = shot.get('result')
    if result not in ('Goal', 'OwnGoal'):
        return None

    # Side that benefits: for OwnGoal, flip from the shooter's side
    shooter = shot.get('h_a')
    if result == 'OwnGoal':
        scoring_side = 'a' if shooter == 'h' else 'h'
    else:
        scoring_side = shooter

    minute_raw = shot.get('minute', '0')
    try:
        minute = int(minute_raw)
    except (TypeError, ValueError):
        return None

    xg = shot.get('xG')
    try:
        xg_val = float(xg) if xg is not None else None
    except (TypeError, ValueError):
        xg_val = None

    return {
        'minute':      minute,
        'team_side':   scoring_side,
        'is_own_goal': result == 'OwnGoal',
        'is_penalty':  shot.get('situation') == 'Penalty',
        'xg_at_shot':  xg_val,
        'player_name': (shot.get('player') or '').strip() or None,
    }


# ---------------------------------------------------------------------------
# Per-match ingestion
# ---------------------------------------------------------------------------

def ingest_match(client, understat_match_id: str, db_match_id: int,
                 cur, dry_run: bool) -> int:
    """Fetch shots for a match and insert goal events. Returns goals inserted."""
    try:
        shots = client.match(match=understat_match_id).get_shot_data()
    except Exception as e:
        log.warning(f"  shot fetch failed for understat={understat_match_id}: {e}")
        return 0

    goals = []
    for shot in (shots.get('h') or []) + (shots.get('a') or []):
        g = shot_to_goal(shot)
        if g:
            goals.append(g)

    if not goals or dry_run:
        return len(goals)

    rows = [
        (db_match_id, g['minute'], g['team_side'], g['is_own_goal'],
         g['is_penalty'], g['xg_at_shot'], g['player_name'], 'understat')
        for g in goals
    ]
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO goal_events
          (match_id, minute, team_side, is_own_goal, is_penalty,
           xg_at_shot, player_name, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_id, minute, team_side, player_name, source) DO NOTHING
    """, rows)
    return len(goals)


def process_league_season(understat_league: str, season_year: int,
                          aliases: dict, match_index: dict, goals_done: set,
                          cur, conn, dry_run: bool,
                          limit: int | None = None) -> tuple[int, int, int]:
    """Returns (matches_processed, goals_inserted, unmatched)."""
    import understatapi
    client = understatapi.UnderstatClient()
    time.sleep(RATE_SECS)

    try:
        matches = client.league(league=understat_league).get_match_data(season=str(season_year))
    except Exception as e:
        log.warning(f"  league fetch error {understat_league}/{season_year}: {e}")
        return 0, 0, 0

    played = [m for m in matches if m.get('isResult')]
    log.info(f"  {understat_league} {season_year}/{season_year+1}: "
             f"{len(played)} played matches")

    processed = inserted = unmatched = 0

    for m in played:
        if limit is not None and processed >= limit:
            break

        date_str = m['datetime'][:10]
        home_id = fuzzy_match(m['h']['title'], aliases)
        away_id = fuzzy_match(m['a']['title'], aliases)
        if home_id is None or away_id is None:
            unmatched += 1
            continue

        db_mid = resolve_match_id(date_str, home_id, away_id, match_index)
        if db_mid is None:
            unmatched += 1
            continue
        if db_mid in goals_done:
            continue

        understat_mid = m['id']
        time.sleep(RATE_SECS)
        n = ingest_match(client, understat_mid, db_mid, cur, dry_run)
        if not dry_run and n > 0:
            conn.commit()
        goals_done.add(db_mid)
        processed += 1
        inserted  += n

    return processed, inserted, unmatched


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description='Stage B v3: Understat goal-event ingestion')
    p.add_argument('--dry-run', action='store_true', help='No DB writes')
    p.add_argument('--leagues', nargs='+', choices=list(LEAGUE_MAP.keys()),
                   default=list(LEAGUE_MAP.keys()))
    p.add_argument('--seasons', nargs='+', type=int, default=SEASONS,
                   help='Season start years (e.g. 2023 for 2023/24)')
    p.add_argument('--limit-per-season', type=int, default=None,
                   help='Cap matches processed per league/season (for testing)')
    p.add_argument('--continue-on-error', action='store_true')
    args = p.parse_args()

    if args.dry_run:
        log.info('DRY RUN — no DB writes')

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    log.info('Loading team aliases and match index...')
    aliases     = load_team_aliases(cur)
    match_index = load_match_index(cur)
    goals_done  = load_goals_done(cur)
    log.info(f'  {len(aliases)} aliases | {len(match_index)} matches | '
             f'{len(goals_done)} matches already done')

    total_proc = total_ins = total_unm = 0
    for league in args.leagues:
        for season in args.seasons:
            log.info(f'Processing {league} {season}/{season+1}...')
            try:
                pr, ins, unm = process_league_season(
                    league, season, aliases, match_index, goals_done,
                    cur, conn, args.dry_run,
                    limit=args.limit_per_season,
                )
                total_proc += pr; total_ins += ins; total_unm += unm
                log.info(f'  -> processed={pr} goals_inserted={ins} unmatched={unm}')
            except Exception as exc:
                log.error(f'Error on {league}/{season}: {exc}')
                conn.rollback()
                if not args.continue_on_error:
                    raise

    cur.close()
    conn.close()
    log.info(f'DONE — matches={total_proc} goals={total_ins} unmatched={total_unm}')


if __name__ == '__main__':
    main()
