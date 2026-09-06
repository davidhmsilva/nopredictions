#!/usr/bin/env python3
"""
Stage A: Football-Data.co.uk ingestion.

Downloads CSVs for configured leagues and seasons, parses them, and upserts
into the schema defined in 001_schema.sql.

Usage:
    # Full run (all leagues, 2010-11 -> current)
    python stage_a_football_data.py

    # Specific leagues/seasons
    python stage_a_football_data.py --leagues ENG-PR ESP-LL --seasons 2023-24 2024-25

    # Dry run (download + parse only, no DB writes)
    python stage_a_football_data.py --dry-run

    # Use a different cache directory
    python stage_a_football_data.py --cache-dir /path/to/cache
"""

# PEP 563: defer annotation evaluation so `X | None` syntax works on Python 3.9.
# (The CLAUDE.md targets Python 3.9+, and this file uses 3.10-style unions.)
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, time
from io import BytesIO
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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv('DATABASE_URL')
FD_BASE = 'https://www.football-data.co.uk/mmz4281'

# League internal code -> Football-Data code.
# Must match 002_seeds.sql.
LEAGUES = {
    'ENG-PR':  'E0',   'ENG-CH':  'E1',   'ENG-L1':  'E2',   'ENG-L2':  'E3',
    'ENG-CON': 'EC',
    'SCO-PR':  'SC0',  'SCO-CH':  'SC1',  'SCO-L1':  'SC2',  'SCO-L2':  'SC3',
    'GER-BL1': 'D1',   'GER-BL2': 'D2',
    'ITA-SA':  'I1',   'ITA-SB':  'I2',
    'ESP-LL':  'SP1',  'ESP-L2':  'SP2',
    'FRA-L1':  'F1',   'FRA-L2':  'F2',
    'NED-ED':  'N1',
    'BEL-JPL': 'B1',
    'POR-PL':  'P1',
    'TUR-SL':  'T1',
    'GRE-SL':  'G1',
}


def generate_seasons(start_year: int, end_year_exclusive: int) -> list:
    """2010, 2025 -> ['2010-11', '2011-12', ..., '2024-25']."""
    return [f'{y}-{str(y + 1)[-2:]}' for y in range(start_year, end_year_exclusive)]


DEFAULT_SEASONS = generate_seasons(2010, 2027)  # ends at 2026-27 (live season)


def season_to_fd_code(season: str) -> str:
    """'2010-11' -> '1011'. '2024-25' -> '2425'."""
    start, end = season.split('-')
    return start[-2:] + end


# Bookmaker column mappings. Each key is our internal bookmaker code.
# For each we try closing odds first, fall back to opening. If neither set
# is present in the CSV, we skip that bookmaker for that match.
BOOKMAKER_COLUMNS = {
    'B365': {
        'closing_1x2':  ('B365CH', 'B365CD', 'B365CA'),
        'opening_1x2':  ('B365H',  'B365D',  'B365A'),
        'closing_ou25': ('B365C>2.5', 'B365C<2.5'),
        'opening_ou25': ('B365>2.5',  'B365<2.5'),
    },
    'PSC': {  # Pinnacle closing specifically — sharp reference
        'closing_1x2':  ('PSCH', 'PSCD', 'PSCA'),
        'closing_ou25': ('PC>2.5', 'PC<2.5'),
    },
    'PS': {   # Pinnacle opening
        'closing_1x2':  ('PSH', 'PSD', 'PSA'),
        'closing_ou25': ('P>2.5', 'P<2.5'),
    },
    'BW':    {'closing_1x2': ('BWCH',  'BWCD',  'BWCA'),
              'opening_1x2': ('BWH',   'BWD',   'BWA')},
    'WH':    {'closing_1x2': ('WHCH',  'WHCD',  'WHCA'),
              'opening_1x2': ('WHH',   'WHD',   'WHA')},
    'VC':    {'closing_1x2': ('VCCH',  'VCCD',  'VCCA'),
              'opening_1x2': ('VCH',   'VCD',   'VCA')},
    'BFEX':  {'closing_1x2': ('BFECH', 'BFECD', 'BFECA')},
    'MAX':   {'closing_1x2': ('MaxCH', 'MaxCD', 'MaxCA'),
              'closing_ou25': ('MaxC>2.5', 'MaxC<2.5')},
    'AVG':   {'closing_1x2': ('AvgCH', 'AvgCD', 'AvgCA'),
              'closing_ou25': ('AvgC>2.5', 'AvgC<2.5')},
}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_csv(fd_league_code: str, season: str, cache_dir: Path | None):
    fd_season = season_to_fd_code(season)
    url = f'{FD_BASE}/{fd_season}/{fd_league_code}.csv'

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f'{fd_season}_{fd_league_code}.csv'
        if cache_file.exists():
            log.debug(f'cache hit: {cache_file}')
            return read_fd_csv(cache_file)

    log.info(f'downloading {url}')
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f'failed to download {url}: {e}')
        return None

    # Football-Data.co.uk runs Apache mod_negotiation: when a season/league file
    # does not exist yet it does NOT 404 — it returns a "300 Multiple Choices"
    # HTML page, or silently serves a *different* league whose filename is close
    # enough (2627/E0.csv -> EC.csv, 2627/SP1.csv -> P1.csv). Both were observed
    # on 2026-08-15 and the second one silently filed Conference results as
    # Premier League. Validate the payload before it is cached or parsed.
    head = resp.content[:512].lstrip().lower()
    if head.startswith(b'<!doctype') or head.startswith(b'<html'):
        log.warning(f'{url}: server returned HTML, not a CSV (file not published yet)')
        return None

    df = read_fd_csv(BytesIO(resp.content))
    if df is None or df.empty:
        return df

    if 'Div' in df.columns:
        divs = set(df['Div'].dropna().astype(str).str.strip().unique())
        if divs and fd_league_code not in divs:
            log.warning(
                f'{url}: Div column says {sorted(divs)} but {fd_league_code} was '
                f'requested — content negotiation served the wrong league, skipping'
            )
            return None

    if cache_dir is not None:
        cache_file.write_bytes(resp.content)

    return df


def read_fd_csv(source):
    """Read an FD CSV, handling common encoding issues."""
    try:
        df = pd.read_csv(source, encoding='utf-8')
    except (UnicodeDecodeError, pd.errors.ParserError):
        if hasattr(source, 'seek'):
            source.seek(0)
        df = pd.read_csv(source, encoding='latin-1', on_bad_lines='skip')
    except pd.errors.EmptyDataError:
        return None

    df = df.dropna(how='all')
    if 'HomeTeam' in df.columns:
        df = df[df['HomeTeam'].notna()]
    return df


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------

def parse_kickoff(row):
    """Return a naive datetime (local time to the league). Stored as UTC
    approximation — precise TZ conversion is a future enhancement, only
    matters for kickoff time, not for date which is what backtests use."""
    date_str = row.get('Date')
    if pd.isna(date_str):
        return None

    d = None
    for fmt in ('%d/%m/%Y', '%d/%m/%y'):
        try:
            d = datetime.strptime(str(date_str).strip(), fmt).date()
            break
        except ValueError:
            continue
    if d is None:
        return None

    t = time(15, 0)
    time_str = row.get('Time') if 'Time' in row.index else None
    if time_str is not None and not pd.isna(time_str) and str(time_str).strip():
        try:
            t = datetime.strptime(str(time_str).strip(), '%H:%M').time()
        except ValueError:
            pass

    return datetime.combine(d, t)


def cell_float(row, col):
    if col not in row.index:
        return None
    v = row[col]
    if pd.isna(v) or v == '':
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def cell_int(row, col):
    v = cell_float(row, col)
    return int(v) if v is not None else None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_league_id(cur, league_code: str) -> int:
    cur.execute('SELECT id FROM leagues WHERE code = %s', (league_code,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f'league not seeded: {league_code}')
    return row[0]


def ensure_season(cur, league_id: int, label: str, start_year: int) -> int:
    cur.execute(
        'SELECT id FROM seasons WHERE league_id = %s AND label = %s',
        (league_id, label),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        '''INSERT INTO seasons (league_id, label, start_date, end_date)
           VALUES (%s, %s, %s, %s)
           RETURNING id''',
        (league_id, label,
         date(start_year, 7, 1), date(start_year + 1, 6, 30)),
    )
    return cur.fetchone()[0]


_TEAM_CACHE: dict = {}  # (source, name_lower) -> team_id


def get_or_create_team(cur, fd_name: str, country: str | None) -> int:
    key = ('football-data', fd_name.lower())
    if key in _TEAM_CACHE:
        return _TEAM_CACHE[key]

    cur.execute(
        '''SELECT team_id FROM team_aliases
           WHERE source = 'football-data' AND LOWER(alias) = LOWER(%s)''',
        (fd_name,),
    )
    row = cur.fetchone()
    if row:
        _TEAM_CACHE[key] = row[0]
        return row[0]

    # No alias -> try canonical name match (case-insensitive)
    cur.execute(
        'SELECT id FROM teams WHERE LOWER(canonical_name) = LOWER(%s)',
        (fd_name,),
    )
    row = cur.fetchone()
    if row:
        team_id = row[0]
    else:
        cur.execute(
            '''INSERT INTO teams (canonical_name, country)
               VALUES (%s, %s) RETURNING id''',
            (fd_name, country),
        )
        team_id = cur.fetchone()[0]

    cur.execute(
        '''INSERT INTO team_aliases (team_id, source, alias)
           VALUES (%s, 'football-data', %s)
           ON CONFLICT (source, alias) DO NOTHING''',
        (team_id, fd_name),
    )
    _TEAM_CACHE[key] = team_id
    return team_id


def get_bookmaker_id_map(cur) -> dict:
    cur.execute('SELECT code, id FROM bookmakers')
    return dict(cur.fetchall())


def upsert_match(cur, season_id, home_id, away_id, kickoff,
                 fthg, ftag, hthg, htag) -> int:
    cur.execute(
        '''INSERT INTO matches
           (season_id, home_team_id, away_team_id, kickoff_utc,
            status, home_score, away_score, home_score_ht, away_score_ht,
            fd_source)
           VALUES (%s, %s, %s, %s,
                   'finished', %s, %s, %s, %s,
                   'football-data')
           ON CONFLICT (season_id, home_team_id, away_team_id, kickoff_utc)
           DO UPDATE SET
               home_score     = EXCLUDED.home_score,
               away_score     = EXCLUDED.away_score,
               home_score_ht  = EXCLUDED.home_score_ht,
               away_score_ht  = EXCLUDED.away_score_ht,
               status         = 'finished'
           RETURNING id''',
        (season_id, home_id, away_id, kickoff, fthg, ftag, hthg, htag),
    )
    return cur.fetchone()[0]


def upsert_match_stats(cur, match_id, row) -> bool:
    """Only write if at least one stat is present."""
    stats = {
        'home_shots':         cell_int(row, 'HS'),
        'away_shots':         cell_int(row, 'AS'),
        'home_shots_on_tgt':  cell_int(row, 'HST'),
        'away_shots_on_tgt':  cell_int(row, 'AST'),
        'home_corners':       cell_int(row, 'HC'),
        'away_corners':       cell_int(row, 'AC'),
        'home_fouls':         cell_int(row, 'HF'),
        'away_fouls':         cell_int(row, 'AF'),
        'home_yellow':        cell_int(row, 'HY'),
        'away_yellow':        cell_int(row, 'AY'),
        'home_red':           cell_int(row, 'HR'),
        'away_red':           cell_int(row, 'AR'),
    }
    if all(v is None for v in stats.values()):
        return False

    cur.execute(
        '''INSERT INTO match_stats
           (match_id, home_shots, away_shots,
            home_shots_on_tgt, away_shots_on_tgt,
            home_corners, away_corners, home_fouls, away_fouls,
            home_yellow, away_yellow, home_red, away_red, stats_source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   'football-data')
           ON CONFLICT (match_id) DO UPDATE SET
               home_shots        = EXCLUDED.home_shots,
               away_shots        = EXCLUDED.away_shots,
               home_shots_on_tgt = EXCLUDED.home_shots_on_tgt,
               away_shots_on_tgt = EXCLUDED.away_shots_on_tgt,
               home_corners      = EXCLUDED.home_corners,
               away_corners      = EXCLUDED.away_corners,
               home_fouls        = EXCLUDED.home_fouls,
               away_fouls        = EXCLUDED.away_fouls,
               home_yellow       = EXCLUDED.home_yellow,
               away_yellow       = EXCLUDED.away_yellow,
               home_red          = EXCLUDED.home_red,
               away_red          = EXCLUDED.away_red''',
        (match_id,
         stats['home_shots'],        stats['away_shots'],
         stats['home_shots_on_tgt'], stats['away_shots_on_tgt'],
         stats['home_corners'],      stats['away_corners'],
         stats['home_fouls'],        stats['away_fouls'],
         stats['home_yellow'],       stats['away_yellow'],
         stats['home_red'],          stats['away_red']),
    )
    return True


def upsert_odds_for_match(cur, match_id, row, bookmaker_ids, observed_at) -> int:
    """Insert closing odds snapshots (one per bookmaker with any data).
    Returns count of bookmakers inserted."""
    inserted = 0
    for bm_code, spec in BOOKMAKER_COLUMNS.items():
        if bm_code not in bookmaker_ids:
            continue

        cols_1x2 = spec.get('closing_1x2') or spec.get('opening_1x2')
        if not cols_1x2:
            continue
        h = cell_float(row, cols_1x2[0])
        d = cell_float(row, cols_1x2[1])
        a = cell_float(row, cols_1x2[2])
        if h is None and d is None and a is None:
            continue

        ou_cols = spec.get('closing_ou25') or spec.get('opening_ou25')
        over, under = None, None
        if ou_cols:
            over = cell_float(row, ou_cols[0])
            under = cell_float(row, ou_cols[1])

        cur.execute(
            '''INSERT INTO match_odds
               (match_id, bookmaker_id, observed_at, snapshot_type,
                home_odds, draw_odds, away_odds,
                over_2_5_odds, under_2_5_odds)
               VALUES (%s, %s, %s, 'closing', %s, %s, %s, %s, %s)
               ON CONFLICT (match_id, bookmaker_id, snapshot_type, observed_at)
               DO UPDATE SET
                   home_odds       = EXCLUDED.home_odds,
                   draw_odds       = EXCLUDED.draw_odds,
                   away_odds       = EXCLUDED.away_odds,
                   over_2_5_odds   = EXCLUDED.over_2_5_odds,
                   under_2_5_odds  = EXCLUDED.under_2_5_odds''',
            (match_id, bookmaker_ids[bm_code], observed_at,
             h, d, a, over, under),
        )
        inserted += 1
    return inserted


def log_start(cur, source, resource) -> int:
    cur.execute(
        '''INSERT INTO data_ingestion_log (source, resource, status)
           VALUES (%s, %s, 'running') RETURNING id''',
        (source, resource),
    )
    return cur.fetchone()[0]


def log_finish(cur, log_id, rows_ingested, status='succeeded', error=None):
    cur.execute(
        '''UPDATE data_ingestion_log
           SET finished_at = NOW(),
               rows_ingested = %s,
               status = %s,
               error_message = %s
           WHERE id = %s''',
        (rows_ingested, status, error, log_id),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def ingest_league_season(conn, league_code: str, season: str,
                         bookmaker_ids: dict, cache_dir: Path | None) -> int:
    fd_code = LEAGUES[league_code]
    resource = f'{season}/{fd_code}'

    cur = conn.cursor()
    log_id = log_start(cur, 'football-data', resource)
    conn.commit()

    try:
        df = download_csv(fd_code, season, cache_dir)
        if df is None or df.empty:
            log.warning(f'{resource}: no data')
            log_finish(cur, log_id, 0)
            conn.commit()
            return 0

        league_id = get_league_id(cur, league_code)
        start_year = int(season.split('-')[0])
        season_id = ensure_season(cur, league_id, season, start_year)

        cur.execute('SELECT country FROM leagues WHERE id = %s', (league_id,))
        country = cur.fetchone()[0]

        processed = 0
        for _, row in df.iterrows():
            if pd.isna(row.get('HomeTeam')) or pd.isna(row.get('AwayTeam')):
                continue
            fthg = cell_int(row, 'FTHG')
            ftag = cell_int(row, 'FTAG')
            if fthg is None or ftag is None:
                continue  # match not played/unresolved
            kickoff = parse_kickoff(row)
            if kickoff is None:
                continue

            home_name = str(row['HomeTeam']).strip()
            away_name = str(row['AwayTeam']).strip()
            home_id = get_or_create_team(cur, home_name, country)
            away_id = get_or_create_team(cur, away_name, country)
            if home_id == away_id:
                continue  # data corruption

            match_id = upsert_match(
                cur, season_id, home_id, away_id, kickoff,
                fthg, ftag, cell_int(row, 'HTHG'), cell_int(row, 'HTAG'),
            )
            upsert_match_stats(cur, match_id, row)
            upsert_odds_for_match(cur, match_id, row, bookmaker_ids, kickoff)
            processed += 1

        log_finish(cur, log_id, processed)
        conn.commit()
        log.info(f'  {resource}: {processed} matches')
        return processed

    except Exception as e:
        conn.rollback()
        cur2 = conn.cursor()
        log_finish(cur2, log_id, 0, status='failed', error=str(e))
        conn.commit()
        log.error(f'{resource} failed: {e}')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--leagues', nargs='+', default=list(LEAGUES.keys()),
                        help='league codes to ingest (default: all)')
    parser.add_argument('--seasons', nargs='+', default=DEFAULT_SEASONS,
                        help="seasons in 'YYYY-YY' format (default: 2010-11 -> 2025-26)")
    parser.add_argument('--cache-dir', type=Path, default=Path('.cache/fd'),
                        help='directory to cache downloaded CSVs')
    parser.add_argument('--dry-run', action='store_true',
                        help='download+parse only, no DB writes')
    parser.add_argument('--continue-on-error', action='store_true',
                        help='log errors and continue to next CSV')
    args = parser.parse_args()

    if args.dry_run:
        log.info('DRY RUN (no DB writes)')
        total_rows = 0
        for lg in args.leagues:
            if lg not in LEAGUES:
                log.warning(f'unknown league {lg}, skipping'); continue
            for s in args.seasons:
                df = download_csv(LEAGUES[lg], s, args.cache_dir)
                if df is not None:
                    log.info(f'{lg}/{s}: {len(df)} rows, {len(df.columns)} cols')
                    total_rows += len(df)
        log.info(f'TOTAL rows across all CSVs: {total_rows}')
        return

    if not DATABASE_URL:
        log.error('DATABASE_URL required (set in .env). See .env.example.')
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        bookmaker_ids = get_bookmaker_id_map(cur)
        if not bookmaker_ids:
            log.error('bookmakers table empty — did you run 002_seeds.sql?')
            sys.exit(1)

        total = 0
        for lg in args.leagues:
            if lg not in LEAGUES:
                log.warning(f'unknown league code {lg}, skipping')
                continue
            for s in args.seasons:
                try:
                    total += ingest_league_season(
                        conn, lg, s, bookmaker_ids, args.cache_dir,
                    )
                except Exception:
                    if not args.continue_on_error:
                        raise
        log.info(f'TOTAL: {total} matches ingested')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
