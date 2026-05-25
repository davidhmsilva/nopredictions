#!/usr/bin/env python3
"""
Stage G: American leagues ingestion via API-Football.

Fetches fixtures + xG for MLS, Brasileirão, Argentina, Liga MX,
Copa Libertadores, Chile, Colombia.  Uses api-football.com (Pro plan).

API cost: ~20 calls for fixtures (all results), + N calls for xG stats.

Usage:
    python stage_g_americas.py --dry-run          # preview
    python stage_g_americas.py                    # ingest fixtures (no xG)
    python stage_g_americas.py --with-xg          # also fetch xG (costs ~1 call/fixture)
    python stage_g_americas.py --leagues MLS BRA  # specific leagues
    python stage_g_americas.py --seasons 2024 2025
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL')
API_KEY = os.getenv('FOOTBALL_API_KEY')
API_BASE = 'https://v3.football.api-sports.io'

# API-Football league IDs → our league codes
LEAGUES = {
    'MLS':          {'api_id': 253, 'code': 'USA-MLS',      'seasons': range(2020, 2026)},
    'BRA':          {'api_id': 71,  'code': 'BRA-SA',       'seasons': range(2020, 2026)},
    'ARG':          {'api_id': 128, 'code': 'ARG-PD',       'seasons': range(2020, 2026)},
    'MEX':          {'api_id': 262, 'code': 'MEX-LMX',      'seasons': range(2020, 2026)},
    'LIBERTADORES': {'api_id': 13,  'code': 'CONMEBOL-CL',  'seasons': range(2020, 2026)},
    'CHL':          {'api_id': 265, 'code': 'CHL-PD',       'seasons': range(2022, 2026)},
    'COL':          {'api_id': 239, 'code': 'COL-PA',       'seasons': range(2022, 2026)},
}

RATE_SECS = 0.5  # Pro plan allows 300 req/min


def api_get(endpoint: str, params: dict) -> dict:
    time.sleep(RATE_SECS)
    r = requests.get(f'{API_BASE}/{endpoint}',
                     headers={'x-apisports-key': API_KEY},
                     params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get('errors'):
        log.warning(f'API errors: {data["errors"]}')
    return data


def ensure_season(cur, conn, league_id: int, year: int) -> int:
    label = str(year)
    cur.execute('SELECT id FROM seasons WHERE league_id = %s AND label = %s',
                (league_id, label))
    r = cur.fetchone()
    if r:
        return r[0]
    cur.execute(
        '''INSERT INTO seasons (league_id, label, start_date, end_date)
           VALUES (%s, %s, %s, %s) RETURNING id''',
        (league_id, label, f'{year}-01-01', f'{year}-12-31'),
    )
    sid = cur.fetchone()[0]
    conn.commit()
    log.info(f'  auto-created season {label}')
    return sid


def ensure_team(cur, conn, name: str, api_team_id: int) -> int:
    """Find or create team. Also registers api-football alias."""
    # Check alias first
    cur.execute(
        "SELECT team_id FROM team_aliases WHERE source = 'api-football' AND alias = %s",
        (name,),
    )
    r = cur.fetchone()
    if r:
        return r[0]

    # Check canonical name
    cur.execute('SELECT id FROM teams WHERE canonical_name = %s', (name,))
    r = cur.fetchone()
    if r:
        team_id = r[0]
    else:
        cur.execute('INSERT INTO teams (canonical_name) VALUES (%s) RETURNING id', (name,))
        team_id = cur.fetchone()[0]
        log.debug(f'  new team: {name} (id={team_id})')

    # Register alias
    cur.execute(
        '''INSERT INTO team_aliases (team_id, source, alias)
           VALUES (%s, 'api-football', %s)
           ON CONFLICT DO NOTHING''',
        (team_id, name),
    )
    conn.commit()
    return team_id


def ingest_league_season(conn, league_cfg: dict, year: int,
                         with_xg: bool, dry_run: bool) -> dict:
    code = league_cfg['code']
    api_id = league_cfg['api_id']

    log.info(f'Fetching {code} / {year}...')
    data = api_get('fixtures', {'league': api_id, 'season': year})
    fixtures = data.get('response', [])
    log.info(f'  {len(fixtures)} fixtures from API')

    if dry_run:
        ft_count = sum(1 for f in fixtures if f['fixture']['status']['short'] == 'FT')
        log.info(f'  {ft_count} finished (FT) — dry run, no writes')
        return {'fetched': len(fixtures), 'created': 0, 'xg_updated': 0}

    cur = conn.cursor()

    # Get league ID
    cur.execute('SELECT id FROM leagues WHERE code = %s', (code,))
    r = cur.fetchone()
    if not r:
        log.warning(f'{code}: not in DB, skipping')
        return {'fetched': 0, 'created': 0, 'xg_updated': 0}
    league_id = r[0]

    season_id = ensure_season(cur, conn, league_id, year)

    created = 0
    skipped = 0
    xg_updated = 0

    for fix in fixtures:
        status = fix['fixture']['status']['short']
        if status not in ('FT', 'AET', 'PEN'):
            continue

        home_name = fix['teams']['home']['name']
        away_name = fix['teams']['away']['name']
        home_id = ensure_team(cur, conn, home_name, fix['teams']['home']['id'])
        away_id = ensure_team(cur, conn, away_name, fix['teams']['away']['id'])

        kickoff = fix['fixture']['date']  # ISO 8601
        home_score = fix['goals']['home']
        away_score = fix['goals']['away']
        ht_home = fix['score']['halftime']['home']
        ht_away = fix['score']['halftime']['away']

        # Check if already exists
        cur.execute(
            '''SELECT id FROM matches
               WHERE season_id = %s AND home_team_id = %s AND away_team_id = %s
               AND kickoff_utc::date = %s::date''',
            (season_id, home_id, away_id, kickoff),
        )
        existing = cur.fetchone()
        if existing:
            # Still fetch xG for existing matches if requested
            if with_xg:
                # Check if xG already exists
                cur.execute('SELECT home_xg FROM match_stats WHERE match_id = %s', (existing[0],))
                has_xg = cur.fetchone()
                if not has_xg or has_xg[0] is None:
                    match_id = existing[0]
                    fix_id = fix['fixture']['id']
                    stats_data = api_get('fixtures/statistics', {'fixture': fix_id})
                    home_xg = away_xg = None
                    for team_stats in stats_data.get('response', []):
                        for s in team_stats.get('statistics', []):
                            if s['type'] == 'expected_goals' and s['value'] is not None:
                                try:
                                    xg_val = float(s['value'])
                                except (ValueError, TypeError):
                                    continue
                                if team_stats['team']['id'] == fix['teams']['home']['id']:
                                    home_xg = xg_val
                                else:
                                    away_xg = xg_val
                    if home_xg is not None or away_xg is not None:
                        cur.execute(
                            '''INSERT INTO match_stats (match_id, home_xg, away_xg, stats_source)
                               VALUES (%s, %s, %s, 'api-football')
                               ON CONFLICT (match_id) DO UPDATE SET
                                   home_xg = COALESCE(EXCLUDED.home_xg, match_stats.home_xg),
                                   away_xg = COALESCE(EXCLUDED.away_xg, match_stats.away_xg)''',
                            (match_id, home_xg, away_xg),
                        )
                        xg_updated += 1
                        if xg_updated % 100 == 0:
                            conn.commit()
                            log.info(f'    {xg_updated} xG enriched so far...')
            skipped += 1
            continue

        cur.execute(
            '''INSERT INTO matches
               (season_id, home_team_id, away_team_id, kickoff_utc,
                home_score, away_score, home_score_ht, away_score_ht,
                status, went_to_et, went_to_pens)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id''',
            (season_id, home_id, away_id, kickoff,
             home_score, away_score, ht_home, ht_away,
             'FT', status == 'AET', status == 'PEN'),
        )
        match_id = cur.fetchone()[0]
        created += 1

        # Fetch xG if requested (costs 1 API call per fixture)
        if with_xg:
            fix_id = fix['fixture']['id']
            stats_data = api_get('fixtures/statistics', {'fixture': fix_id})
            home_xg = away_xg = None
            for team_stats in stats_data.get('response', []):
                for s in team_stats.get('statistics', []):
                    if s['type'] == 'expected_goals' and s['value'] is not None:
                        try:
                            xg_val = float(s['value'])
                        except (ValueError, TypeError):
                            continue
                        if team_stats['team']['id'] == fix['teams']['home']['id']:
                            home_xg = xg_val
                        else:
                            away_xg = xg_val

            if home_xg is not None or away_xg is not None:
                cur.execute(
                    '''INSERT INTO match_stats (match_id, home_xg, away_xg, stats_source)
                       VALUES (%s, %s, %s, 'api-football')
                       ON CONFLICT (match_id) DO UPDATE SET
                           home_xg = COALESCE(EXCLUDED.home_xg, match_stats.home_xg),
                           away_xg = COALESCE(EXCLUDED.away_xg, match_stats.away_xg)''',
                    (match_id, home_xg, away_xg),
                )
                xg_updated += 1

    conn.commit()
    log.info(f'  {code}/{year}: {created} created, {skipped} skipped, {xg_updated} xG')
    return {'fetched': len(fixtures), 'created': created, 'xg_updated': xg_updated}


def main():
    parser = argparse.ArgumentParser(description='Ingest American leagues from API-Football')
    parser.add_argument('--leagues', nargs='+', default=list(LEAGUES.keys()),
                        help='League keys (MLS, BRA, ARG, MEX, LIBERTADORES, CHL, COL)')
    parser.add_argument('--seasons', nargs='+', type=int, default=None,
                        help='Seasons to ingest (default: all configured)')
    parser.add_argument('--with-xg', action='store_true',
                        help='Also fetch xG stats (1 API call per fixture)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not API_KEY:
        log.error('FOOTBALL_API_KEY not set in .env')
        return

    conn = psycopg2.connect(DATABASE_URL)
    totals = {'fetched': 0, 'created': 0, 'xg_updated': 0}

    for lg_key in args.leagues:
        if lg_key not in LEAGUES:
            log.warning(f'Unknown league key: {lg_key}')
            continue
        cfg = LEAGUES[lg_key]
        seasons = args.seasons or cfg['seasons']
        for yr in seasons:
            result = ingest_league_season(conn, cfg, yr, args.with_xg, args.dry_run)
            for k in totals:
                totals[k] += result[k]

    conn.close()
    log.info(f'DONE — {totals["created"]} matches created, {totals["xg_updated"]} xG enriched')


if __name__ == '__main__':
    main()
