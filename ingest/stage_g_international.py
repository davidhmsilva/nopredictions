#!/usr/bin/env python3
"""
Stage G: International football results ingestion.

Downloads the Mart Jürisoo international results dataset from GitHub and
upserts national team matches into the same schema used by Stage A.

Source: https://github.com/martj42/international_results

Tournaments ingested:
  - FIFA World Cup (finals + qualifiers)
  - UEFA European Championship (finals + qualifiers)
  - UEFA Nations League
  - Copa América
  - CONCACAF Nations League / Gold Cup
  - Friendlies (since 2015 only — volume control)
  - African Cup of Nations (finals + qualifiers)
  - AFC Asian Cup (finals + qualifiers)

Usage:
    python stage_g_international.py                    # full run
    python stage_g_international.py --min-year 2020    # recent only
    python stage_g_international.py --dry-run           # no DB writes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL')
CSV_URL = 'https://raw.githubusercontent.com/martj42/international_results/master/results.csv'

TOURNAMENT_TO_LEAGUE = {
    'FIFA World Cup': 'INT-WC',
    'FIFA World Cup qualification': 'INT-WCQ',
    'UEFA European Championship': 'INT-EURO',
    'UEFA European Championship qualification': 'INT-EUROQ',
    'UEFA Nations League': 'INT-UNL',
    'Copa América': 'INT-COPA',
    'Copa América qualification': 'INT-COPA',
    'CONCACAF Nations League': 'INT-CONCACAF',
    'CONCACAF Gold Cup': 'INT-CONCACAF',
    'CONCACAF Championship': 'INT-CONCACAF',
    'African Cup of Nations': 'INT-AFCON',
    'African Cup of Nations qualification': 'INT-AFCONQ',
    'AFC Asian Cup': 'INT-AFC',
    'AFC Asian Cup qualification': 'INT-AFCQ',
    'Friendly': 'INT-FR',
}

LEAGUE_DEFS = {
    'INT-WC':       ('FIFA World Cup',                'INTL', 1, True),
    'INT-WCQ':      ('FIFA World Cup Qualification',  'INTL', 2, False),
    'INT-EURO':     ('UEFA European Championship',    'INTL', 1, True),
    'INT-EUROQ':    ('UEFA Euro Qualification',       'INTL', 2, False),
    'INT-UNL':      ('UEFA Nations League',           'INTL', 2, False),
    'INT-COPA':     ('Copa América',                  'INTL', 1, True),
    'INT-CONCACAF': ('CONCACAF',                      'INTL', 2, True),
    'INT-AFCON':    ('African Cup of Nations',        'INTL', 1, True),
    'INT-AFCONQ':   ('AFCON Qualification',           'INTL', 2, False),
    'INT-AFC':      ('AFC Asian Cup',                 'INTL', 1, True),
    'INT-AFCQ':     ('AFC Asian Cup Qualification',   'INTL', 2, False),
    'INT-FR':       ('International Friendlies',      'INTL', 3, False),
}


def download_csv(cache_dir: Path | None) -> pd.DataFrame | None:
    cache_file = None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / 'international_results.csv'
        if cache_file.exists():
            age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
            if age_hours < 24:
                log.info(f'cache hit: {cache_file} ({age_hours:.0f}h old)')
                return pd.read_csv(cache_file)

    log.info(f'downloading {CSV_URL}')
    resp = requests.get(CSV_URL, timeout=60)
    resp.raise_for_status()

    if cache_file:
        cache_file.write_bytes(resp.content)

    from io import BytesIO
    return pd.read_csv(BytesIO(resp.content))


def determine_season(match_date: date, tournament: str) -> str:
    if 'World Cup' in tournament and 'qualification' not in tournament.lower():
        return f'{match_date.year}'
    if match_date.month >= 7:
        return f'{match_date.year}-{str(match_date.year + 1)[-2:]}'
    return f'{match_date.year - 1}-{str(match_date.year)[-2:]}'


_league_cache: dict[str, int] = {}


def ensure_league(cur, code: str) -> int:
    if code in _league_cache:
        return _league_cache[code]

    cur.execute('SELECT id FROM leagues WHERE code = %s', (code,))
    row = cur.fetchone()
    if row:
        _league_cache[code] = row[0]
        return row[0]

    name, country, tier, is_cup = LEAGUE_DEFS[code]
    cur.execute(
        '''INSERT INTO leagues (code, name, country, tier, is_cup)
           VALUES (%s, %s, %s, %s, %s) RETURNING id''',
        (code, name, country, tier, is_cup),
    )
    lid = cur.fetchone()[0]
    _league_cache[code] = lid
    return lid


_season_cache: dict[tuple[int, str], int] = {}


def ensure_season(cur, league_id: int, label: str) -> int:
    key = (league_id, label)
    if key in _season_cache:
        return _season_cache[key]

    cur.execute(
        'SELECT id FROM seasons WHERE league_id = %s AND label = %s',
        (league_id, label),
    )
    row = cur.fetchone()
    if row:
        _season_cache[key] = row[0]
        return row[0]

    if '-' in label:
        start_year = int(label.split('-')[0])
    else:
        start_year = int(label)

    cur.execute(
        '''INSERT INTO seasons (league_id, label, start_date, end_date)
           VALUES (%s, %s, %s, %s) RETURNING id''',
        (league_id, label,
         date(start_year, 1, 1), date(start_year + 1, 12, 31)),
    )
    sid = cur.fetchone()[0]
    _season_cache[key] = sid
    return sid


_team_cache: dict[str, int] = {}


def get_or_create_team(cur, name: str) -> int:
    key = name.lower()
    if key in _team_cache:
        return _team_cache[key]

    cur.execute(
        '''SELECT team_id FROM team_aliases
           WHERE source = 'international' AND LOWER(alias) = LOWER(%s)''',
        (name,),
    )
    row = cur.fetchone()
    if row:
        _team_cache[key] = row[0]
        return row[0]

    cur.execute(
        'SELECT id FROM teams WHERE LOWER(canonical_name) = LOWER(%s)',
        (name,),
    )
    row = cur.fetchone()
    if row:
        team_id = row[0]
    else:
        cur.execute(
            '''INSERT INTO teams (canonical_name, country)
               VALUES (%s, %s) RETURNING id''',
            (name, name),
        )
        team_id = cur.fetchone()[0]

    cur.execute(
        '''INSERT INTO team_aliases (team_id, source, alias)
           VALUES (%s, 'international', %s)
           ON CONFLICT (source, alias) DO NOTHING''',
        (team_id, name),
    )
    _team_cache[key] = team_id
    return team_id


def upsert_match(cur, season_id, home_id, away_id, kickoff,
                 home_score, away_score, neutral: bool) -> int:
    status = 'finished' if home_score is not None else 'scheduled'
    cur.execute(
        '''INSERT INTO matches
           (season_id, home_team_id, away_team_id, kickoff_utc,
            status, home_score, away_score, fd_source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'international')
           ON CONFLICT (season_id, home_team_id, away_team_id, kickoff_utc)
           DO UPDATE SET
               home_score = COALESCE(EXCLUDED.home_score, matches.home_score),
               away_score = COALESCE(EXCLUDED.away_score, matches.away_score),
               status     = EXCLUDED.status
           RETURNING id''',
        (season_id, home_id, away_id, kickoff,
         status, home_score, away_score),
    )
    return cur.fetchone()[0]


def log_start(cur, resource: str) -> int:
    cur.execute(
        '''INSERT INTO data_ingestion_log (source, resource, status)
           VALUES ('international', %s, 'running') RETURNING id''',
        (resource,),
    )
    return cur.fetchone()[0]


def log_finish(cur, log_id: int, rows: int, status='succeeded', error=None):
    cur.execute(
        '''UPDATE data_ingestion_log
           SET finished_at = NOW(), rows_ingested = %s,
               status = %s, error_message = %s
           WHERE id = %s''',
        (rows, status, error, log_id),
    )


def main():
    parser = argparse.ArgumentParser(description='Stage G: International football results')
    parser.add_argument('--min-year', type=int, default=2010,
                        help='Earliest year to ingest (default: 2010)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Download + parse only, no DB writes')
    parser.add_argument('--cache-dir', type=str, default='.cache/international',
                        help='Cache directory')
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    df = download_csv(cache_dir)
    if df is None or df.empty:
        log.error('no data downloaded')
        sys.exit(1)

    log.info(f'downloaded {len(df)} total rows')

    df['date'] = pd.to_datetime(df['date'], format='%Y-%m-%d', errors='coerce')
    df = df.dropna(subset=['date'])
    df = df[df['date'].dt.year >= args.min_year]

    relevant = df[df['tournament'].isin(TOURNAMENT_TO_LEAGUE)]
    if 'Friendly' in TOURNAMENT_TO_LEAGUE:
        friendly_mask = (relevant['tournament'] == 'Friendly') & (relevant['date'].dt.year < 2015)
        relevant = relevant[~friendly_mask]

    log.info(f'{len(relevant)} matches after filtering (min_year={args.min_year})')

    if args.dry_run:
        for league_code in relevant['tournament'].map(TOURNAMENT_TO_LEAGUE).unique():
            count = (relevant['tournament'].map(TOURNAMENT_TO_LEAGUE) == league_code).sum()
            log.info(f'  {league_code}: {count} matches')
        log.info('dry run — no DB writes')
        return

    if not DATABASE_URL:
        log.error('DATABASE_URL not set')
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    log_id = log_start(cur, f'international/{args.min_year}+')
    conn.commit()

    processed = 0
    skipped = 0

    try:
        for _, row in relevant.iterrows():
            tournament = row['tournament']
            league_code = TOURNAMENT_TO_LEAGUE[tournament]
            match_date = row['date'].date()

            league_id = ensure_league(cur, league_code)
            season_label = determine_season(match_date, tournament)
            season_id = ensure_season(cur, league_id, season_label)

            home_name = str(row['home_team']).strip()
            away_name = str(row['away_team']).strip()
            home_id = get_or_create_team(cur, home_name)
            away_id = get_or_create_team(cur, away_name)

            if home_id == away_id:
                skipped += 1
                continue

            home_score = int(row['home_score']) if pd.notna(row['home_score']) else None
            away_score = int(row['away_score']) if pd.notna(row['away_score']) else None
            neutral = str(row.get('neutral', 'FALSE')).upper() == 'TRUE'

            kickoff = datetime.combine(match_date, time(20, 0))

            upsert_match(cur, season_id, home_id, away_id, kickoff,
                         home_score, away_score, neutral)
            processed += 1

            if processed % 1000 == 0:
                conn.commit()
                log.info(f'  ... {processed} matches processed')

        log_finish(cur, log_id, processed)
        conn.commit()
        log.info(f'done: {processed} matches ingested, {skipped} skipped')

        cur.execute('''
            SELECT l.code, COUNT(m.id)
            FROM matches m
            JOIN seasons s ON m.season_id = s.id
            JOIN leagues l ON s.league_id = l.id
            WHERE l.country = 'INTL'
            GROUP BY l.code ORDER BY COUNT(m.id) DESC
        ''')
        for code, count in cur.fetchall():
            log.info(f'  {code}: {count} matches in DB')

    except Exception as e:
        conn.rollback()
        cur2 = conn.cursor()
        log_finish(cur2, log_id, processed, 'failed', str(e)[:500])
        conn.commit()
        log.error(f'failed: {e}')
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
