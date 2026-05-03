#!/usr/bin/env python3
"""
Stage C: ClubElo ingestion.

Downloads ELO rating histories for all football clubs from api.clubelo.com
and stores them in the club_elo table.

The club_elo table stores each club's ELO rating as a series of date ranges
(from_date, to_date). To get a club's ELO on any given match date:

    SELECT elo FROM club_elo
    WHERE club_name = 'Arsenal'
      AND from_date <= '2023-08-11'
      AND to_date   >= '2023-08-11';

Coverage: all clubs ever ranked by ClubElo (goes back to 1939 for some clubs).
Relevant for our purposes: 2010 -> present.

Usage:
    python stage_c_clubelo.py --dry-run
    python stage_c_clubelo.py                    # full run (~600 clubs, ~20 min)
    python stage_c_clubelo.py --continue-on-error
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import time

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
CLUBELO_BASE = 'http://api.clubelo.com'
RATE_SECS    = 1.5   # ClubElo is a small free service — be polite

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}

# Focus on European leagues that matter for our project
TARGET_COUNTRIES = {'ENG', 'ESP', 'GER', 'ITA', 'FRA', 'POR', 'NED', 'BEL',
                    'TUR', 'GRE', 'SCO', 'EUR'}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def get_all_clubs(country: str | None = None) -> list[str]:
    """Fetch the ranked list of clubs from ClubElo for a reference date."""
    url = f'{CLUBELO_BASE}/2024-01-01'
    r   = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()

    clubs = []
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        c = row.get('Country', '').strip().upper()
        if TARGET_COUNTRIES and c not in TARGET_COUNTRIES:
            continue
        clubs.append(row['Club'].strip())
    return clubs


def fetch_club_history(club_name: str) -> list[dict]:
    """
    Download full ELO history for a club.
    Returns list of {club_name, country, level, elo, from_date, to_date}.
    """
    url = f'{CLUBELO_BASE}/{club_name.replace(" ", "%20")}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 404:
            return []
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning(f'  fetch failed for {club_name!r}: {e}')
        return []

    rows = []
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        try:
            rows.append({
                'club_name': row['Club'].strip(),
                'country':   row.get('Country', '').strip() or None,
                'level':     int(row['Level']) if row.get('Level', '').strip() else None,
                'elo':       float(row['Elo']),
                'from_date': row['From'].strip(),
                'to_date':   row['To'].strip(),
            })
        except (KeyError, ValueError):
            continue
    return rows


def get_already_ingested(cur) -> set[str]:
    cur.execute("SELECT DISTINCT club_name FROM club_elo")
    return {r[0] for r in cur.fetchall()}


def upsert_club(cur, rows: list[dict], dry_run: bool) -> int:
    if not rows or dry_run:
        return len(rows)

    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO club_elo (club_name, country, level, elo, from_date, to_date)
        VALUES %s
        ON CONFLICT DO NOTHING
        """,
        [(r['club_name'], r['country'], r['level'],
          r['elo'], r['from_date'], r['to_date']) for r in rows],
    )
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Stage C: ClubElo ingestion')
    parser.add_argument('--dry-run', action='store_true', help='No DB writes')
    parser.add_argument('--continue-on-error', action='store_true')
    parser.add_argument(
        '--clubs', nargs='+',
        help='Specific club names to ingest (default: all European clubs)',
    )
    args = parser.parse_args()

    if args.dry_run:
        log.info('DRY RUN — no DB writes')

    conn = get_conn()
    conn.autocommit = False
    cur  = conn.cursor()

    already_done = get_already_ingested(cur)
    log.info(f'{len(already_done)} clubs already in club_elo table')

    if args.clubs:
        clubs = args.clubs
    else:
        log.info('Fetching club list from ClubElo...')
        clubs = get_all_clubs()
        log.info(f'Found {len(clubs)} European clubs to process')

    total_rows = 0

    for i, club in enumerate(clubs, 1):
        if club in already_done:
            log.debug(f'[{i}/{len(clubs)}] {club} — already done, skipping')
            continue

        log.info(f'[{i}/{len(clubs)}] {club}')
        time.sleep(RATE_SECS)

        try:
            rows = fetch_club_history(club)
            if not rows:
                log.warning(f'  no data for {club!r}')
                continue

            n = upsert_club(cur, rows, args.dry_run)
            total_rows += n
            log.info(f'  -> {n} ELO records')

            if not args.dry_run and i % 20 == 0:
                conn.commit()
                log.info('  (committed batch)')

        except Exception as exc:
            log.error(f'Error on {club}: {exc}')
            conn.rollback()
            if not args.continue_on_error:
                raise

    if not args.dry_run:
        conn.commit()

    cur.close()
    conn.close()
    log.info(f'DONE — {total_rows} total ELO records ingested for {len(clubs)} clubs')


if __name__ == '__main__':
    main()
