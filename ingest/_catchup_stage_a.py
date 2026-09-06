"""Ad-hoc catch-up driver for Stage A.

Reuses stage_a_football_data.ingest_league_season but filters each CSV down to
rows kicking off after (max kickoff already in the DB - buffer), so a
league-season that is 95% loaded commits in seconds instead of minutes.

Upserts are idempotent, so the buffer overlap is safe.

Usage:
    python _catchup_stage_a.py --season 2025-26 --leagues ENG-PR ESP-LL
    python _catchup_stage_a.py --season 2025-26            # all leagues
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

import stage_a_football_data as sa

BUFFER_DAYS = 3


def db_max_kickoff(cur, league_code, season):
    cur.execute(
        """SELECT max(m.kickoff_utc)
             FROM matches m
             JOIN seasons s ON m.season_id = s.id
             JOIN leagues l ON s.league_id = l.id
            WHERE l.code = %s AND s.label = %s""",
        (league_code, season),
    )
    row = cur.fetchone()
    return row[0] if row else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default='2025-26')
    ap.add_argument('--leagues', nargs='+', default=list(sa.LEAGUES.keys()))
    ap.add_argument('--cache-dir', default='/tmp/fresh')
    args = ap.parse_args()

    load_dotenv()
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute('SELECT code, id FROM bookmakers')
    bookmaker_ids = dict(cur.fetchall())
    cache_dir = Path(args.cache_dir)

    orig_download = sa.download_csv
    total = 0

    for lg in args.leagues:
        if lg not in sa.LEAGUES:
            print(f'unknown league {lg}, skipping')
            continue
        cutoff = db_max_kickoff(cur, lg, args.season)

        def patched(fd_code, season, cd, _cutoff=cutoff):
            df = orig_download(fd_code, season, cd)
            if df is None or df.empty or _cutoff is None:
                return df
            dates = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
            keep = dates >= (pd.Timestamp(_cutoff).tz_localize(None)
                             - pd.Timedelta(days=BUFFER_DAYS))
            return df[keep.fillna(True)]

        sa.download_csv = patched
        try:
            n = sa.ingest_league_season(conn, lg, args.season,
                                        bookmaker_ids, cache_dir)
            total += n
            print(f'{lg}: {n} rows processed (cutoff {cutoff})')
        except Exception as e:  # noqa: BLE001
            print(f'{lg}: FAILED {e}')
        finally:
            sa.download_csv = orig_download

    print(f'TOTAL processed: {total}')
    conn.close()


if __name__ == '__main__':
    main()
