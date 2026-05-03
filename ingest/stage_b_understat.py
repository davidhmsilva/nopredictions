#!/usr/bin/env python3
"""
Stage B (v2): Understat xG enrichment.

Replaces the FBref scraper. Uses the `understatapi` library to fetch match-level
xG for Big 5 leagues from 2014/15 onwards and upsert into match_stats.

Coverage:
  Leagues : EPL, La_Liga, Bundesliga, Serie_A, Ligue_1
  Seasons : 2014 -> 2024  (i.e. 2014/15 through 2024/25)

Usage:
    python stage_b_understat.py --dry-run
    python stage_b_understat.py --leagues EPL --seasons 2023
    python stage_b_understat.py                          # full run
    python stage_b_understat.py --continue-on-error      # full run, skip errors
"""

from __future__ import annotations

import argparse
import logging
import os
import time
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
RATE_SECS    = 2.0   # understat is more relaxed than FBref

# Understat league codes -> our league codes
LEAGUE_MAP = {
    'EPL':        'ENG-PR',
    'La_Liga':    'ESP-LL',
    'Bundesliga': 'GER-BL1',
    'Serie_A':    'ITA-SA',
    'Ligue_1':    'FRA-L1',
}

# Seasons understat uses: integer year = start of season (2014 = 2014/15)
SEASONS = list(range(2014, 2025))   # 2014/15 -> 2024/25


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def load_team_aliases(cur) -> dict[str, int]:
    """Return {alias_lower -> team_id} for all known aliases."""
    cur.execute("SELECT LOWER(alias), team_id FROM team_aliases")
    return {row[0]: row[1] for row in cur.fetchall()}


def load_match_index(cur) -> dict[tuple, int]:
    """
    Return {(date_str, home_team_id, away_team_id) -> match_id}.
    date_str = 'YYYY-MM-DD'
    """
    cur.execute("""
        SELECT DATE(kickoff_utc)::text, home_team_id, away_team_id, id
        FROM matches
        WHERE kickoff_utc >= '2014-01-01'
    """)
    return {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}


def load_xg_done(cur) -> set[int]:
    """Return set of match_ids that already have xG populated."""
    cur.execute("SELECT match_id FROM match_stats WHERE home_xg IS NOT NULL")
    return {r[0] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Team name matching
# ---------------------------------------------------------------------------

def fuzzy_match(name: str, aliases: dict[str, int], threshold: float = 0.7) -> int | None:
    """Find the best-matching team_id for a given name string."""
    key = name.lower().strip()
    if key in aliases:
        return aliases[key]

    # Try common normalizations
    for variant in [
        key.replace(' fc', '').strip(),
        key.replace('fc ', '').strip(),
        key.replace(' cf', '').strip(),
        key.replace('.', '').strip(),
    ]:
        if variant in aliases:
            return aliases[variant]

    # Fuzzy fallback
    best_score = 0.0
    best_id    = None
    for alias, tid in aliases.items():
        score = SequenceMatcher(None, key, alias).ratio()
        if score > best_score:
            best_score = score
            best_id    = tid

    if best_score >= threshold:
        return best_id

    return None


# ---------------------------------------------------------------------------
# Understat fetch + upsert
# ---------------------------------------------------------------------------

def process_league_season(
    understat_league: str,
    season_year: int,
    aliases: dict[str, int],
    match_index: dict[tuple, int],
    xg_done: set[int],
    cur,
    dry_run: bool,
) -> tuple[int, int, int]:
    """
    Fetch one league+season from Understat and upsert xG into match_stats.
    Returns (enriched, skipped_already_done, unmatched).
    """
    try:
        import understatapi
    except ImportError:
        raise RuntimeError("understatapi not installed. Run: pip install understatapi")

    client = understatapi.UnderstatClient()
    time.sleep(RATE_SECS)

    try:
        matches = client.league(league=understat_league).get_match_data(season=str(season_year))
    except Exception as e:
        log.warning(f"  fetch error {understat_league}/{season_year}: {e}")
        return 0, 0, 0

    played = [m for m in matches if m.get('isResult') and m.get('xG')]
    log.info(f"  {understat_league} {season_year}/{season_year+1}: "
             f"{len(played)} played matches with xG (of {len(matches)} total)")

    enriched = skipped = unmatched = 0

    for m in played:
        xg_h = m['xG'].get('h')
        xg_a = m['xG'].get('a')
        if xg_h is None or xg_a is None:
            continue

        # Parse date (Understat gives "2023-08-11 19:00:00")
        date_str = m['datetime'][:10]

        # Resolve team IDs
        home_name = m['h']['title']
        away_name = m['a']['title']
        home_id   = fuzzy_match(home_name, aliases)
        away_id   = fuzzy_match(away_name, aliases)

        if home_id is None or away_id is None:
            log.debug(f"  unmatched teams: {home_name!r} | {away_name!r}")
            unmatched += 1
            continue

        # Find match in our DB
        match_id = match_index.get((date_str, home_id, away_id))
        if match_id is None:
            # Try ±1 day (timezone edge cases)
            from datetime import date, timedelta
            d = date.fromisoformat(date_str)
            for delta in [-1, 1]:
                alt = (d + timedelta(days=delta)).isoformat()
                match_id = match_index.get((alt, home_id, away_id))
                if match_id:
                    break

        if match_id is None:
            log.debug(f"  no DB match: {date_str} {home_name} vs {away_name}")
            unmatched += 1
            continue

        if match_id in xg_done:
            skipped += 1
            continue

        if not dry_run:
            cur.execute("""
                INSERT INTO match_stats (match_id, home_xg, away_xg, xg_source)
                VALUES (%s, %s, %s, 'understat')
                ON CONFLICT (match_id) DO UPDATE
                  SET home_xg   = EXCLUDED.home_xg,
                      away_xg   = EXCLUDED.away_xg,
                      xg_source = 'understat',
                      updated_at = now()
                WHERE match_stats.home_xg IS NULL
            """, (match_id, float(xg_h), float(xg_a)))

        xg_done.add(match_id)
        enriched += 1

    return enriched, skipped, unmatched


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Stage B v2: Understat xG enrichment')
    parser.add_argument('--dry-run', action='store_true', help='No DB writes')
    parser.add_argument(
        '--leagues', nargs='+',
        choices=list(LEAGUE_MAP.keys()),
        default=list(LEAGUE_MAP.keys()),
        help='Understat league codes to run',
    )
    parser.add_argument(
        '--seasons', nargs='+', type=int,
        default=SEASONS,
        help='Season start years (e.g. 2023 for 2023/24)',
    )
    parser.add_argument('--continue-on-error', action='store_true')
    args = parser.parse_args()

    if args.dry_run:
        log.info('DRY RUN — no DB writes')

    conn = get_conn()
    conn.autocommit = False
    cur  = conn.cursor()

    log.info('Loading team aliases and match index from DB...')
    aliases     = load_team_aliases(cur)
    match_index = load_match_index(cur)
    xg_done     = load_xg_done(cur)
    log.info(f'  {len(aliases)} aliases | {len(match_index)} matches | {len(xg_done)} already have xG')

    total_enriched = total_skipped = total_unmatched = 0

    for league in args.leagues:
        for season in args.seasons:
            log.info(f'Processing {league} {season}/{season+1}...')
            try:
                e, s, u = process_league_season(
                    league, season, aliases, match_index,
                    xg_done, cur, args.dry_run
                )
                total_enriched  += e
                total_skipped   += s
                total_unmatched += u
                if not args.dry_run and e > 0:
                    conn.commit()
                log.info(f'  -> enriched={e} skipped={s} unmatched={u}')
            except Exception as exc:
                log.error(f'Error on {league}/{season}: {exc}')
                conn.rollback()
                if not args.continue_on_error:
                    raise

    if not args.dry_run:
        conn.commit()

    cur.close()
    conn.close()

    log.info(
        f'DONE — enriched={total_enriched} '
        f'skipped={total_skipped} '
        f'unmatched={total_unmatched}'
    )


if __name__ == '__main__':
    main()
