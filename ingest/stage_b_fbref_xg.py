#!/usr/bin/env python3
"""
Stage B: FBref xG enrichment.

Scrapes match-level xG from FBref schedule pages and upserts into match_stats.
Covers Big 5 leagues + UEFA Champions League, from 2017-18 onwards
(that is when FBref xG data becomes available).

Rate limit: 1 request per 3.5 seconds (FBref asks for ~1 req/3s).

Usage:
    # Dry run — no DB writes, just shows what would be scraped
    python stage_b_fbref_xg.py --dry-run

    # Single league / season
    python stage_b_fbref_xg.py --leagues ENG-PR --seasons 2023-24

    # Full run (all Big 5 + UCL, 2017-18 -> 2025-26)
    python stage_b_fbref_xg.py --continue-on-error
"""

from __future__ import annotations

import argparse
import difflib
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    import cloudscraper
    _scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'darwin', 'mobile': False}
    )
    _USE_CLOUDSCRAPER = True
except ImportError:
    _scraper = None
    _USE_CLOUDSCRAPER = False

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL')
FBREF_BASE   = 'https://fbref.com'
RATE_SECS    = 3.5   # slightly above 3s to be safe with FBref


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Big 5 + UCL. xg_from = first season where FBref has xG data.
LEAGUES: dict[str, dict] = {
    'ENG-PR':  {'fbref_id': 9,  'fbref_name': 'Premier-League',        'xg_from': 2017},
    'GER-BL1': {'fbref_id': 20, 'fbref_name': 'Bundesliga',             'xg_from': 2017},
    'ITA-SA':  {'fbref_id': 11, 'fbref_name': 'Serie-A',                'xg_from': 2017},
    'ESP-LL':  {'fbref_id': 12, 'fbref_name': 'La-Liga',                'xg_from': 2017},
    'FRA-L1':  {'fbref_id': 13, 'fbref_name': 'Ligue-1',                'xg_from': 2018},
    'UEFA-CL': {'fbref_id': 8,  'fbref_name': 'Champions-League',       'xg_from': 2017},
}

XG_START_YEAR = 2017

# Known FBref → Football-Data name mappings.
# We seed the team_aliases table automatically, but bootstrapping common
# mismatches here avoids unmatched rows on the first run.
KNOWN_ALIASES: dict[str, str] = {
    # FBref name (lower)              # canonical name (lower) used by FD
    "manchester city":                "man city",
    "manchester united":              "man united",
    "tottenham hotspur":              "tottenham",
    "wolverhampton wanderers":        "wolves",
    "nottingham forest":              "nott'm forest",
    "sheffield united":               "sheffield utd",
    "queens park rangers":            "qpr",
    "west bromwich albion":           "west brom",
    "brighton and hove albion":       "brighton",
    "bayer leverkusen":               "leverkusen",
    "rb leipzig":                     "rB Leipzig",
    "borussia dortmund":              "dortmund",
    "borussia mönchengladbach":       "m'gladbach",
    "eintracht frankfurt":            "ein frankfurt",
    "internazionale":                 "inter",
    "ac milan":                       "milan",
    "atletico madrid":                "ath madrid",
    "athletic bilbao":                "ath bilbao",
    "real betis":                     "betis",
    "deportivo alavés":               "alaves",
    "paris saint-germain":            "psg",
    "olympique marseille":            "marseille",
    "olympique lyonnais":             "lyon",
    "as monaco":                      "monaco",
}


def generate_seasons(start_year: int, end_year_excl: int) -> list[str]:
    return [f'{y}-{str(y + 1)[-2:]}' for y in range(start_year, end_year_excl)]


DEFAULT_SEASONS = generate_seasons(XG_START_YEAR, 2026)


def season_to_fbref(season: str) -> str:
    """'2023-24' → '2023-2024'"""
    start, _ = season.split('-')
    return f'{start}-{int(start) + 1}'


def build_url(league_code: str, season: str) -> str:
    cfg = LEAGUES[league_code]
    fs  = season_to_fbref(season)
    return (
        f'{FBREF_BASE}/en/comps/{cfg["fbref_id"]}/{fs}/schedule/'
        f'{fs}-{cfg["fbref_name"]}-Scores-and-Fixtures'
    )


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------

def fetch(url: str, cache_dir: Path) -> str | None:
    """Fetch URL with filesystem cache. Rate-limits every live request."""
    slug = url.replace('https://', '').replace('/', '_') + '.html'
    cache_file = cache_dir / slug

    if cache_file.exists():
        log.debug(f'cache hit: {cache_file.name}')
        return cache_file.read_text(encoding='utf-8')

    time.sleep(RATE_SECS)
    log.info(f'fetching {url}')

    def _get(u: str):
        if _USE_CLOUDSCRAPER:
            return _scraper.get(u, timeout=30)
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-GB,en;q=0.9',
        }
        return requests.get(u, headers=headers, timeout=30)

    try:
        r = _get(url)
        if r.status_code == 429:
            log.warning('Rate limited — sleeping 60s')
            time.sleep(60)
            r = _get(url)
        if r.status_code == 403:
            log.warning('403 Forbidden — sleeping 15s and retrying')
            time.sleep(15)
            r = _get(url)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning(f'fetch failed: {e}')
        return None

    cache_file.write_text(r.text, encoding='utf-8')
    return r.text


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def parse_schedule(html: str, league_code: str, season: str) -> list[dict]:
    """
    Parse FBref schedule page.
    Returns list of dicts: {date, home_team, away_team, home_xg, away_xg}.
    Rows without xG are dropped.
    """
    soup  = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', id=lambda x: x and x.startswith('sched_'))
    if not table:
        log.warning(f'{league_code}/{season}: schedule table not found in HTML')
        return []

    def cell(tr, stat: str) -> str | None:
        td = tr.find(['td', 'th'], {'data-stat': stat})
        return td.get_text(strip=True) if td else None

    rows = []
    for tr in table.select('tbody tr'):
        # Skip divider/header rows inserted by FBref
        if 'class' in tr.attrs and any(c in tr['class'] for c in ('thead', 'spacer')):
            continue

        date_str   = cell(tr, 'date')
        home_team  = cell(tr, 'home_team')
        away_team  = cell(tr, 'away_team')
        score      = cell(tr, 'score')
        xg_home_s  = cell(tr, 'xg')     # home xG column
        xg_away_s  = cell(tr, 'xg_2')   # away xG column

        # Must have date, teams, and a played score
        if not date_str or not home_team or not away_team:
            continue
        if not score or '–' not in score and '-' not in score:
            continue   # not yet played

        try:
            match_date = datetime.strptime(date_str.strip(), '%Y-%m-%d').date()
        except ValueError:
            continue

        def to_float(s: str | None) -> float | None:
            if not s or not s.strip():
                return None
            try:
                return float(s.strip())
            except ValueError:
                return None

        home_xg = to_float(xg_home_s)
        away_xg = to_float(xg_away_s)

        # Only keep rows that actually have xG
        if home_xg is None and away_xg is None:
            continue

        rows.append({
            'date':      match_date,
            'home_team': home_team.strip(),
            'away_team': away_team.strip(),
            'home_xg':   home_xg,
            'away_xg':   away_xg,
        })

    return rows


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def load_team_alias_map(cur) -> dict[str, int]:
    """Returns {lower(alias) -> team_id} for source='fbref'."""
    cur.execute("SELECT LOWER(alias), team_id FROM team_aliases WHERE source = 'fbref'")
    return dict(cur.fetchall())


def load_all_canonical_teams(cur) -> dict[str, int]:
    """Returns {lower(canonical_name) -> team_id}."""
    cur.execute('SELECT LOWER(canonical_name), id FROM teams')
    return dict(cur.fetchall())


def register_alias(cur, team_id: int, fbref_name: str, alias_map: dict):
    cur.execute(
        '''INSERT INTO team_aliases (team_id, source, alias)
           VALUES (%s, 'fbref', %s)
           ON CONFLICT (source, alias) DO NOTHING''',
        (team_id, fbref_name),
    )
    alias_map[fbref_name.lower()] = team_id


def resolve_team(cur, fbref_name: str,
                 alias_map: dict[str, int],
                 canonical_map: dict[str, int]) -> int | None:
    """
    Resolve FBref team name → our team_id.

    Resolution order:
      1. Exact fbref alias match (already registered).
      2. Known manual alias from KNOWN_ALIASES constant.
      3. Exact canonical name match (case-insensitive).
      4. Fuzzy match against all canonical names (cutoff 0.82).
    """
    key = fbref_name.lower()

    # 1. Already in alias table
    if key in alias_map:
        return alias_map[key]

    # 2. Known manual alias
    if key in KNOWN_ALIASES:
        canonical_key = KNOWN_ALIASES[key].lower()
        if canonical_key in canonical_map:
            team_id = canonical_map[canonical_key]
            register_alias(cur, team_id, fbref_name, alias_map)
            return team_id

    # 3. Exact canonical match
    if key in canonical_map:
        team_id = canonical_map[key]
        register_alias(cur, team_id, fbref_name, alias_map)
        return team_id

    # 4. Fuzzy match
    matches = difflib.get_close_matches(key, canonical_map.keys(), n=1, cutoff=0.82)
    if matches:
        team_id = canonical_map[matches[0]]
        log.debug(f'fuzzy: "{fbref_name}" → "{matches[0]}" (team_id={team_id})')
        register_alias(cur, team_id, fbref_name, alias_map)
        return team_id

    return None


def upsert_xg(cur, match_id: int, home_xg: float | None, away_xg: float | None):
    """Insert xG into match_stats, updating if row exists but xG is NULL."""
    cur.execute(
        '''INSERT INTO match_stats (match_id, home_xg, away_xg, stats_source)
           VALUES (%s, %s, %s, 'fbref')
           ON CONFLICT (match_id) DO UPDATE SET
               home_xg      = COALESCE(EXCLUDED.home_xg, match_stats.home_xg),
               away_xg      = COALESCE(EXCLUDED.away_xg, match_stats.away_xg),
               updated_at   = NOW()''',
        (match_id, home_xg, away_xg),
    )


# ---------------------------------------------------------------------------
# Core ingestion
# ---------------------------------------------------------------------------

def ingest(conn, league_code: str, season: str, cache_dir: Path, dry_run: bool) -> tuple[int, int]:
    """Returns (updated, unmatched)."""
    start_year = int(season.split('-')[0])
    cfg        = LEAGUES[league_code]

    if start_year < cfg['xg_from']:
        log.info(f'  {league_code}/{season}: skip (xG not on FBref before {cfg["xg_from"]}-XX)')
        return 0, 0

    url  = build_url(league_code, season)
    html = fetch(url, cache_dir)
    if not html:
        return 0, 0

    rows = parse_schedule(html, league_code, season)
    if not rows:
        log.info(f'  {league_code}/{season}: 0 rows with xG')
        return 0, 0

    if dry_run:
        log.info(f'  {league_code}/{season}: {len(rows)} rows with xG (dry run, no writes)')
        return len(rows), 0

    cur = conn.cursor()

    # Lookup league + season IDs
    cur.execute('SELECT id FROM leagues WHERE code = %s', (league_code,))
    r = cur.fetchone()
    if not r:
        log.warning(f'{league_code}: not in DB')
        return 0, 0
    league_id = r[0]

    cur.execute('SELECT id FROM seasons WHERE league_id = %s AND label = %s', (league_id, season))
    r = cur.fetchone()
    if not r:
        log.warning(f'{league_code}/{season}: season not in DB')
        return 0, 0
    season_id = r[0]

    alias_map     = load_team_alias_map(cur)
    canonical_map = load_all_canonical_teams(cur)

    updated   = 0
    unmatched = 0

    for row in rows:
        home_id = resolve_team(cur, row['home_team'], alias_map, canonical_map)
        away_id = resolve_team(cur, row['away_team'], alias_map, canonical_map)

        if home_id is None or away_id is None:
            missing = []
            if home_id is None: missing.append(row['home_team'])
            if away_id is None: missing.append(row['away_team'])
            log.debug(f'  unmatched teams: {", ".join(missing)}')
            unmatched += 1
            continue

        # Find match: season + teams + date
        cur.execute(
            '''SELECT id FROM matches
               WHERE season_id    = %s
               AND home_team_id   = %s
               AND away_team_id   = %s
               AND kickoff_utc::date = %s''',
            (season_id, home_id, away_id, row['date']),
        )
        match_row = cur.fetchone()

        if not match_row:
            log.debug(f'  no match in DB: {row["home_team"]} v {row["away_team"]} {row["date"]}')
            unmatched += 1
            continue

        upsert_xg(cur, match_row[0], row['home_xg'], row['away_xg'])
        updated += 1

    conn.commit()
    log.info(f'  {league_code}/{season}: {updated} updated, {unmatched} unmatched')
    return updated, unmatched


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--leagues', nargs='+', default=list(LEAGUES.keys()),
                        help='league codes (default: all Big 5 + UCL)')
    parser.add_argument('--seasons', nargs='+', default=DEFAULT_SEASONS,
                        help="seasons in 'YYYY-YY' format")
    parser.add_argument('--cache-dir', type=Path, default=Path('.cache/fbref'),
                        help='HTML cache directory')
    parser.add_argument('--dry-run', action='store_true',
                        help='fetch + parse only, no DB writes')
    parser.add_argument('--continue-on-error', action='store_true',
                        help='log errors and continue')
    args = parser.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        log.info('DRY RUN (no DB writes)')
        conn = None
    else:
        if not DATABASE_URL:
            log.error('DATABASE_URL not set. See ingest/.env')
            sys.exit(1)
        conn = psycopg2.connect(DATABASE_URL)

    try:
        total_updated   = 0
        total_unmatched = 0

        for lg in args.leagues:
            if lg not in LEAGUES:
                log.warning(f'Unknown league {lg}, skipping')
                continue
            for s in args.seasons:
                try:
                    u, un = ingest(conn, lg, s, args.cache_dir, args.dry_run)
                    total_updated   += u
                    total_unmatched += un
                except Exception as e:
                    log.error(f'{lg}/{s} failed: {e}')
                    if conn:
                        conn.rollback()
                    if not args.continue_on_error:
                        raise

        log.info(f'DONE — {total_updated} matches enriched, {total_unmatched} unmatched')

    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    main()
