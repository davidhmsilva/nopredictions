#!/usr/bin/env python3
"""
Stage M: minute-by-minute price paths for every settled football market.

DATA ONLY. Files, not the database (the full tape is ~15M points).

`pm_ticks` only starts on 2026-07-21, so every in-play question about
Polymarket has been answerable only forward. The history was public all along:

  Polymarket  CLOB /prices-history?market=<token>&startTs&endTs&fidelity=1
              One point a minute, CLOSED markets included. `p` is
              (best bid + best ask)/2 with a MISSING bid counted as 0 and a
              missing ask as 1 — measured against the live tape on 2026-10-04:
              two-sided book → the mid; ask-only → ask/2; bid-only →
              (bid+1)/2; empty → 0.5000. Never a fill, and on a one-sided
              book not a price at all (reports/pm_inplay_calibration_2026-10-04.md).
  Kalshi      /series/<s>/markets/<t>/candlesticks?period_interval=1, or
              /historical/markets/<t>/candlesticks for anything settled before
              the cutoff Kalshi publishes at /historical/cutoff (different
              field names: `close` not `close_dollars`, `volume` not `volume_fp`).
              One candle a minute with the YES bid AND ask at its close, plus
              volume and open interest. The ask IS executable (top of book).

The universe is Stage J's `venue_market_history` (prices_status='ok': settled,
traded, kick-off known, linked to `matches`), so every path already has its
outcome (`winner`) and our match row. The window is kick-off −90' → +180',
which covers the pre-match close, both halves, stoppage and the post-whistle
window before resolution.

Output, one gzip member per batch (readable with gzip.open as one stream):

  ingest/data/price_paths/polymarket/YYYY-MM.jsonl.gz
      {"m": condition_id, "tok": token0, "ko": ko_ts, "t": [min from KO], "p": [price]}
  ingest/data/price_paths/kalshi/YYYY-MM.jsonl.gz
      {"m": ticker, "ko": ko_ts, "t": [...], "b": [bid], "a": [ask],
       "l": [last trade or null], "v": [volume], "oi": [open interest]}

`t` is minutes from the listed kick-off (`venue_market_history.kickoff_utc`,
already corrected for Football-Data's London clock by Stage J).
Prices are token0's (PM) / YES (Kalshi). Join back on market_id.

Usage:
    python stage_m_price_paths.py --venue polymarket --limit 20 --dry-run
    python stage_m_price_paths.py --venue both               # resumable
    python stage_m_price_paths.py --summary
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

from db_pool import ingest_url

load_dotenv(Path(__file__).resolve().parent / '.env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('stage_m')

DATABASE_URL = os.getenv('DATABASE_URL')
OUT_DIR = Path(__file__).resolve().parent / 'data' / 'price_paths'

PM_URL = 'https://clob.polymarket.com/prices-history'
KX_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
KX_URL = KX_BASE + '/series/{series}/markets/{ticker}/candlesticks'
KX_HIST_URL = KX_BASE + '/historical/markets/{ticker}/candlesticks'

BEFORE_KO_S = 90 * 60
AFTER_KO_S = 180 * 60

# Requests per second, shared by every worker of a venue. Kalshi refused 112
# of 139 calls at 10 concurrent unpaced on /events (CLAUDE.md, 2026-09-20);
# 4 workers 0.25s apart answered with zero 429s, i.e. ~16/s. Both are kept
# well under what was measured, because a 429 costs a backoff and a retry.
RATE = {'polymarket': 8.0, 'kalshi': 8.0}


class Limiter:
    def __init__(self, per_s: float):
        self.gap = 1.0 / per_s
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.gap
        if at > now:
            time.sleep(at - now)


def _get(session: requests.Session, limiter: Limiter, url: str, params: dict):
    """GET with backoff on 429/5xx. Returns parsed JSON, or None when the
    server says the thing does not exist. Raises after repeated failures."""
    delay = 2.0
    for attempt in range(6):
        limiter.wait()
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            log.debug(f'{url}: {e}; retry in {delay:.0f}s')
            time.sleep(delay); delay *= 2
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (400, 404):
            return None
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(delay); delay = min(delay * 2, 60)
            continue
        raise RuntimeError(f'{url} -> HTTP {r.status_code}: {r.text[:200]}')
    raise RuntimeError(f'{url}: gave up after retries')


def fetch_pm(session, limiter, row) -> dict | None:
    ko = row['ko']
    d = _get(session, limiter, PM_URL, {
        'market': row['token0'], 'startTs': ko - BEFORE_KO_S,
        'endTs': ko + AFTER_KO_S, 'fidelity': 1,
    })
    hist = (d or {}).get('history') or []
    if not hist:
        return {'m': row['market_id'], 'tok': row['token0'], 'ko': ko, 't': [], 'p': []}
    hist.sort(key=lambda x: x['t'])
    return {
        'm': row['market_id'], 'tok': row['token0'], 'ko': ko,
        't': [round((h['t'] - ko) / 60) for h in hist],
        'p': [round(float(h['p']), 4) for h in hist],
    }


def _num(v, nd: int):
    return round(float(v), nd) if v not in (None, '') else None


def _dollars(block: dict | None):
    """The live route names the close `close_dollars`, the historical one
    `close`, both in dollars. Reading only one shape stored every
    historical quote as None while the candle count looked healthy."""
    if not block:
        return None
    v = block.get('close_dollars')
    return _num(v if v is not None else block.get('close'), 4)


_KX_CUTOFF: dict = {}


def kalshi_cutoff(session) -> int:
    """Kalshi moves a market to /historical/ once it settled before this
    instant (2026-08-04 when written): the live candlestick route then 404s,
    and the historical one 404s for anything newer."""
    if 'ts' not in _KX_CUTOFF:
        r = session.get(f'{KX_BASE}/historical/cutoff', timeout=30)
        r.raise_for_status()
        raw = r.json()['market_settled_ts']
        _KX_CUTOFF['ts'] = int(datetime.fromisoformat(raw.replace('Z', '+00:00')).timestamp())
    return _KX_CUTOFF['ts']


def fetch_kalshi(session, limiter, row) -> dict | None:
    ko = row['ko']
    ticker = row['market_id']
    series = ticker.split('-')[0]
    params = {'start_ts': ko - BEFORE_KO_S, 'end_ts': ko + AFTER_KO_S, 'period_interval': 1}
    live = KX_URL.format(series=series, ticker=ticker)
    hist = KX_HIST_URL.format(ticker=ticker)
    # Settlement comes a few hours after kick-off, so a day's margin decides
    # the first route; the other is tried when the first says "not found".
    first, second = (hist, live) if ko < kalshi_cutoff(session) - 86400 else (live, hist)
    d = _get(session, limiter, first, params)
    if d is None:
        d = _get(session, limiter, second, params)
    candles = (d or {}).get('candlesticks') or []
    out = {'m': ticker, 'ko': ko, 't': [], 'b': [], 'a': [], 'l': [], 'v': [], 'oi': []}
    for c in sorted(candles, key=lambda x: x['end_period_ts']):
        out['t'].append(round((c['end_period_ts'] - ko) / 60))
        out['b'].append(_dollars(c.get('yes_bid')))
        out['a'].append(_dollars(c.get('yes_ask')))
        out['l'].append(_dollars(c.get('price')))
        v = c.get('volume_fp', c.get('volume'))
        out['v'].append(_num(v, 2))
        oi = c.get('open_interest_fp', c.get('open_interest'))
        out['oi'].append(_num(oi, 2))
    return out


FETCH = {'polymarket': fetch_pm, 'kalshi': fetch_kalshi}


def load_universe(venue: str) -> list[dict]:
    conn = psycopg2.connect(ingest_url())
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """SELECT market_id, token0, extract(epoch FROM kickoff_utc)::bigint
           FROM venue_market_history
           WHERE venue = %s AND prices_status = 'ok' AND kickoff_utc IS NOT NULL
             AND kickoff_utc < now() - interval '6 hours'
           ORDER BY kickoff_utc""",
        (venue,),
    )
    rows = [{'market_id': m, 'token0': t, 'ko': int(k)} for m, t, k in cur.fetchall()]
    conn.close()
    if venue == 'polymarket':
        rows = [r for r in rows if r['token0']]
    return rows


def done_ids(venue: str) -> set[str]:
    f = OUT_DIR / venue / 'done.txt'
    if not f.exists():
        return set()
    return {ln.strip() for ln in f.read_text().splitlines() if ln.strip()}


def flush(venue: str, buf: dict[str, list[dict]]) -> int:
    """Append one gzip member per month file, THEN mark the ids done, so a crash
    between the two re-fetches a batch rather than losing it."""
    vdir = OUT_DIR / venue
    vdir.mkdir(parents=True, exist_ok=True)
    n = 0
    ids = []
    for month, recs in buf.items():
        if not recs:
            continue
        payload = ''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in recs)
        with open(vdir / f'{month}.jsonl.gz', 'ab') as fh:
            fh.write(gzip.compress(payload.encode()))
        ids.extend(r['m'] for r in recs)
        n += len(recs)
    if ids:
        with open(vdir / 'done.txt', 'a') as fh:
            fh.write(''.join(i + '\n' for i in ids))
    buf.clear()
    return n


def run_venue(venue: str, workers: int, limit: int | None, dry_run: bool) -> None:
    rows = load_universe(venue)
    done = done_ids(venue)
    todo = [r for r in rows if r['market_id'] not in done]
    if limit:
        todo = todo[:limit]
    log.info(f'{venue}: {len(rows)} settled markets, {len(done)} done, {len(todo)} to fetch')
    if not todo:
        return

    limiter = Limiter(RATE[venue])
    session = requests.Session()   # library default User-Agent, on purpose
    fetch = FETCH[venue]
    buf: dict[str, list[dict]] = defaultdict(list)
    written = empty = failed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, session, limiter, r): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:
                failed += 1
                log.warning(f'{venue} {r["market_id"]}: {e}')
                continue
            if not rec['t']:
                empty += 1
            month = datetime.fromtimestamp(r['ko'], tz=timezone.utc).strftime('%Y-%m')
            if dry_run:
                if i <= 3:
                    log.info(f'  sample {r["market_id"]}: {len(rec["t"])} points, '
                             f't {rec["t"][:3]}..{rec["t"][-3:]}')
                continue
            buf[month].append(rec)
            if sum(len(v) for v in buf.values()) >= 500:
                written += flush(venue, buf)
            if i % 2000 == 0:
                el = time.time() - t0
                log.info(f'{venue}: {i}/{len(todo)} ({i / el:.1f}/s), '
                         f'{empty} empty, {failed} failed')
    if not dry_run:
        written += flush(venue, buf)
    log.info(f'{venue}: wrote {written}, empty {empty}, failed {failed} '
             f'in {time.time() - t0:.0f}s')


def iter_paths(venue: str):
    """Read every stored path of a venue back, in file order."""
    for f in sorted((OUT_DIR / venue).glob('*.jsonl.gz')):
        with gzip.open(f, 'rt') as fh:
            for line in fh:
                yield json.loads(line)


def summary() -> None:
    for venue in ('polymarket', 'kalshi'):
        vdir = OUT_DIR / venue
        if not vdir.exists():
            print(f'{venue}: nothing yet'); continue
        n = pts = empty = 0
        inplay = 0
        for rec in iter_paths(venue):
            n += 1
            pts += len(rec['t'])
            empty += not rec['t']
            inplay += any(0 <= t <= 95 for t in rec['t'])
        size = sum(f.stat().st_size for f in vdir.glob('*.jsonl.gz'))
        print(f'{venue}: {n} markets, {pts} points, {empty} empty, '
              f'{inplay} with in-play points, {size / 1e6:.1f} MB')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--venue', choices=['polymarket', 'kalshi', 'both'], default='both')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--summary', action='store_true')
    args = ap.parse_args()
    if args.summary:
        summary(); return
    venues = ['polymarket', 'kalshi'] if args.venue == 'both' else [args.venue]
    if len(venues) == 2 and not args.dry_run:
        # Different hosts, different limits: run both at once.
        with ThreadPoolExecutor(max_workers=2) as ex:
            for f in [ex.submit(run_venue, v, args.workers, args.limit, False) for v in venues]:
                f.result()
    else:
        for v in venues:
            run_venue(v, args.workers, args.limit, args.dry_run)


if __name__ == '__main__':
    main()
