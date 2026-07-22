#!/usr/bin/env python3
"""
Stage H: NBA historical closing lines.

Loads a pre-scraped sportsbookreview.com archive into `nba_odds`, joined to the
NBA games already in `matches` (league USA-NBA), then rebuilds `bt_nba` so the
Hypothesis Tester can backtest NBA moneylines, spreads and totals.

Source: https://github.com/flancast90/sportsbookreview-scraper  (MIT)
        data/nba_archive_10Y.json — seasons 2011-12 .. 2021-22

Known limits — these are deliberate and must stay visible to users:
  * Coverage ends with the 2021-22 season. The upstream host
    (sportsbookreviewsonline.com) no longer serves the source files, so the
    range cannot currently be extended from this source.
  * Our `matches` table starts at 2014-15, so the joinable window is
    2014-15 .. 2021-22.
  * The line is sportsbookreview's CONSENSUS close, not Pinnacle.
  * The archive stores the spread/total LINE but not its price, so spread and
    total backtests assume the market-standard -110 (1.9091 decimal).
    Moneyline uses the real archived prices.
  * The 2nd-half columns in the archive are corrupt (~25% of rows carry
    impossible values) and are not ingested.

Usage:
    python stage_h_nba_odds.py                # download (cached) + load + refresh
    python stage_h_nba_odds.py --dry-run      # parse and report, no DB writes
    python stage_h_nba_odds.py --no-refresh   # load nba_odds but skip bt_nba
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger('stage_h')

DATABASE_URL = os.getenv('DATABASE_URL')

SOURCE_URL = (
    'https://raw.githubusercontent.com/flancast90/sportsbookreview-scraper/'
    'main/data/nba_archive_10Y.json'
)
CACHE = Path(__file__).parent / '.cache' / 'nba' / 'nba_archive_10Y.json'

# Archive nickname -> teams.canonical_name. The archive uses bare nicknames with
# a few mangled spellings; a handful of one-off strays ('0', 'Golden State') are
# parse artifacts in the source and are dropped.
TEAM_MAP = {
    'Hawks': 'Atlanta Hawks',
    'Celtics': 'Boston Celtics',
    'Nets': 'Brooklyn Nets',
    'Hornets': 'Charlotte Hornets',
    'Bulls': 'Chicago Bulls',
    'Cavaliers': 'Cleveland Cavaliers',
    'Mavericks': 'Dallas Mavericks',
    'Nuggets': 'Denver Nuggets',
    'Pistons': 'Detroit Pistons',
    'Warriors': 'Golden State Warriors',
    'Rockets': 'Houston Rockets',
    'Pacers': 'Indiana Pacers',
    'Clippers': 'LA Clippers',
    'Lakers': 'Los Angeles Lakers',
    'Grizzlies': 'Memphis Grizzlies',
    'Heat': 'Miami Heat',
    'Bucks': 'Milwaukee Bucks',
    'Timberwolves': 'Minnesota Timberwolves',
    'Pelicans': 'New Orleans Pelicans',
    'Knicks': 'New York Knicks',
    'Thunder': 'Oklahoma City Thunder',
    'Magic': 'Orlando Magic',
    'Seventysixers': 'Philadelphia 76ers',
    'Suns': 'Phoenix Suns',
    'Trailblazers': 'Portland Trail Blazers',
    'Kings': 'Sacramento Kings',
    'Spurs': 'San Antonio Spurs',
    'Raptors': 'Toronto Raptors',
    'Jazz': 'Utah Jazz',
    'Wizards': 'Washington Wizards',
}

# Seasons present in our matches table; anything earlier has no games to join to.
MIN_SEASON = 2014


def american_to_decimal(ml) -> float | None:
    """American moneyline -> decimal odds. Returns None for junk/missing."""
    try:
        v = float(ml)
    except (TypeError, ValueError):
        return None
    if v == 0 or abs(v) < 100:
        return None
    return round(1 + (100 / abs(v) if v < 0 else v / 100), 4)


def as_num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


# Plausible NBA ranges, used to detect the source's column-shift bug.
TOTAL_LO, TOTAL_HI = 150.0, 260.0
SPREAD_MAX = 30.0


def unswap(spread, total, home_is_fav: bool | None) -> tuple[float | None, float | None]:
    """Repair rows where the archive swapped the spread and total columns.

    ~7.4% of rows carry the total in the spread column and vice versa. The swap
    is unambiguous — a spread never reaches 150 and a total never drops below
    150 — so the magnitudes are recoverable exactly. The spread's SIGN is lost
    (the total column is unsigned), so it is inferred from the moneyline
    favourite. That inference was validated against the 9,176 unambiguous clean
    rows: the moneyline favourite is the spread favourite in 100% of them.

    Returns (spread_home, total), either of which may be None.
    """
    swapped = (
        total is not None and spread is not None
        and total < TOTAL_LO and TOTAL_LO <= abs(spread) <= TOTAL_HI
    )
    if not swapped:
        # drop values that are implausible even after the swap check
        if spread is not None and abs(spread) > SPREAD_MAX:
            spread = None
        if total is not None and not (TOTAL_LO <= total <= TOTAL_HI):
            total = None
        return spread, total

    magnitude = abs(total)
    if home_is_fav is None:
        repaired_spread = None          # cannot orient the line without a favourite
    else:
        repaired_spread = -magnitude if home_is_fav else magnitude
    return repaired_spread, abs(spread)


def fetch(force: bool = False) -> list[dict]:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if CACHE.exists() and not force:
        log.info('using cached archive %s', CACHE)
        return json.loads(CACHE.read_text())
    log.info('downloading %s', SOURCE_URL)
    r = requests.get(SOURCE_URL, timeout=180)
    r.raise_for_status()
    CACHE.write_bytes(r.content)
    log.info('cached %.1f MB -> %s', len(r.content) / 1e6, CACHE)
    return json.loads(r.content)


def parse_rows(raw: list[dict]) -> list[dict]:
    """Normalise archive rows into join-ready records."""
    out = []
    skipped = {'season': 0, 'team': 0, 'date': 0, 'score': 0, 'no_price': 0}
    repaired = {'col_swap': 0, 'swap_unoriented': 0}
    for r in raw:
        season = r.get('season')
        if not isinstance(season, (int, float)) or int(season) < MIN_SEASON:
            skipped['season'] += 1
            continue

        home = TEAM_MAP.get(str(r.get('home_team')))
        away = TEAM_MAP.get(str(r.get('away_team')))
        if not home or not away or home == away:
            skipped['team'] += 1
            continue

        try:
            game_date = datetime.strptime(str(int(r['date'])), '%Y%m%d').date()
        except (KeyError, TypeError, ValueError):
            skipped['date'] += 1
            continue

        hs, aws = as_num(r.get('home_final')), as_num(r.get('away_final'))
        if hs is None or aws is None:
            skipped['score'] += 1
            continue

        ml_h = american_to_decimal(r.get('home_close_ml'))
        ml_a = american_to_decimal(r.get('away_close_ml'))
        home_is_fav = None if (ml_h is None or ml_a is None or ml_h == ml_a) else ml_h < ml_a

        raw_sp, raw_tot = as_num(r.get('home_close_spread')), as_num(r.get('close_over_under'))
        if (raw_tot is not None and raw_sp is not None
                and raw_tot < TOTAL_LO and TOTAL_LO <= abs(raw_sp) <= TOTAL_HI):
            repaired['col_swap'] += 1
            if home_is_fav is None:
                repaired['swap_unoriented'] += 1

        sp_c, tot_c = unswap(
            as_num(r.get('home_close_spread')), as_num(r.get('close_over_under')), home_is_fav)
        sp_o, tot_o = unswap(
            as_num(r.get('home_open_spread')), as_num(r.get('open_over_under')), home_is_fav)
        if sp_c is not None and sp_o is not None and sp_c * sp_o < 0:
            sp_o = None      # opening line disagrees on orientation — untrustworthy

        if ml_h is None and sp_c is None and tot_c is None:
            skipped['no_price'] += 1
            continue

        out.append({
            'season': int(season),
            'date': game_date,
            'home': home,
            'away': away,
            'home_score': int(hs),
            'away_score': int(aws),
            'close_ml_home': ml_h,
            'close_ml_away': ml_a,
            'open_spread_home': sp_o,
            'close_spread_home': sp_c,
            'open_total': tot_o,
            'close_total': tot_c,
        })

    log.info('parsed %d rows (skipped: %s)', len(out), skipped)
    log.info(
        'column-swap repair: %d rows unswapped, %d of those left without a '
        'spread (no moneyline favourite to orient the line)',
        repaired['col_swap'], repaired['swap_unoriented'],
    )
    return out


def load_nba_matches(conn) -> dict:
    """(date, home, away) -> (match_id, home_score, away_score) for NBA games.

    Indexed by the UTC calendar date AND the previous day, because a US evening
    tip-off rolls over into the next UTC date — the archive stores the local
    game date.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.id, m.kickoff_utc, th.canonical_name, ta.canonical_name,
                   m.home_score, m.away_score
            FROM matches m
            JOIN seasons s ON s.id = m.season_id
            JOIN leagues l ON l.id = s.league_id AND l.code = 'USA-NBA'
            JOIN teams th ON th.id = m.home_team_id
            JOIN teams ta ON ta.id = m.away_team_id
            WHERE m.status = 'finished' AND m.home_score IS NOT NULL
        """)
        rows = cur.fetchall()

    idx: dict = {}
    for mid, ko, home, away, hs, aws in rows:
        # canonicalise the duplicate Clippers identity in `teams`
        home = 'LA Clippers' if home == 'Los Angeles Clippers' else home
        away = 'LA Clippers' if away == 'Los Angeles Clippers' else away
        d = ko.date()
        for cand in (d, d - timedelta(days=1)):
            idx.setdefault((cand, home, away), []).append((mid, hs, aws))
    log.info('indexed %d NBA matches from DB', len(rows))
    return idx


def join(records: list[dict], idx: dict) -> tuple[list[tuple], dict]:
    """Match archive rows to match_ids. Requires the final score to agree."""
    resolved, stats = [], {'matched': 0, 'no_match': 0, 'score_mismatch': 0}
    seen: set[int] = set()

    for rec in records:
        cands = idx.get((rec['date'], rec['home'], rec['away']), [])
        if not cands:
            stats['no_match'] += 1
            continue
        hit = next(
            (mid for mid, hs, aws in cands
             if hs == rec['home_score'] and aws == rec['away_score']),
            None,
        )
        if hit is None:
            stats['score_mismatch'] += 1
            continue
        if hit in seen:
            continue
        seen.add(hit)
        stats['matched'] += 1
        resolved.append((
            hit, 'sbr-archive',
            rec['close_ml_home'], rec['close_ml_away'],
            rec['open_spread_home'], rec['close_spread_home'],
            rec['open_total'], rec['close_total'],
        ))
    return resolved, stats


def upsert(conn, rows: list[tuple]) -> int:
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO nba_odds (
                match_id, source, close_ml_home, close_ml_away,
                open_spread_home, close_spread_home, open_total, close_total
            ) VALUES %s
            ON CONFLICT (match_id) DO UPDATE SET
                source            = EXCLUDED.source,
                close_ml_home     = EXCLUDED.close_ml_home,
                close_ml_away     = EXCLUDED.close_ml_away,
                open_spread_home  = EXCLUDED.open_spread_home,
                close_spread_home = EXCLUDED.close_spread_home,
                open_total        = EXCLUDED.open_total,
                close_total       = EXCLUDED.close_total,
                ingested_at       = now()
            """,
            rows,
            page_size=500,
        )
        # execute_values reports only the last batch, so count what we sent
        return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description='Stage H: NBA historical closing lines')
    ap.add_argument('--dry-run', action='store_true', help='parse and report, no DB writes')
    ap.add_argument('--no-refresh', action='store_true', help='skip refresh_bt_nba()')
    ap.add_argument('--force-download', action='store_true', help='ignore the local cache')
    args = ap.parse_args()

    if not DATABASE_URL:
        log.error('DATABASE_URL not set')
        return 1

    records = parse_rows(fetch(force=args.force_download))
    if not records:
        log.error('no usable rows in the archive')
        return 1

    conn = psycopg2.connect(DATABASE_URL)
    try:
        rows, stats = join(records, load_nba_matches(conn))
        log.info(
            'join: matched=%d  no_match=%d  score_mismatch=%d  (%.1f%% of parsed)',
            stats['matched'], stats['no_match'], stats['score_mismatch'],
            100 * stats['matched'] / len(records),
        )
        if args.dry_run:
            log.info('dry run — no writes')
            return 0

        n = upsert(conn, rows)
        conn.commit()
        log.info('nba_odds upserted: %d rows', n)

        if not args.no_refresh:
            with conn.cursor() as cur:
                cur.execute('SELECT refresh_bt_nba()')
                built = cur.fetchone()[0]
            conn.commit()
            log.info('bt_nba rebuilt: %d rows', built)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
