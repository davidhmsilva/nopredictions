#!/usr/bin/env python3
"""
Stage M archive: save Polymarket's minute price history before the old
endpoint dies.

DATA ONLY. Files, not the database.

`GET clob.polymarket.com/prices-history` shuts down on 2026-10-30 00:00 UTC
(@PolymarketDevs, 2026-10-25). Its replacement, data-api `/v2/prices-history`,
keeps 1-minute points for >= 7 days, 5-minute for >= 60 and 30-minute for
>= 90; past that only 3h / 12h buckets. Measured on a June 2026 token: the old
endpoint returned 272 minute points around kick-off, the new one 0, and its
`as_of` at kick-off answered with a 3h bucket starting 6.5h earlier. So any
minute path not saved by the deadline is gone.

Stage M (`stage_m_price_paths.py`) already holds ~55k football paths, but only
Stage J's universe (1X2 token0 of fixtures linked to `matches`) and only from
kick-off -90'. This fills the gaps, straight from Gamma's closed events so the
unlinked leagues (World Cup, summer leagues) are in:

  tier          universe (traded markets, volumeNum > 0)       window
  soccer_oct    soccer, end date 2026-10-03 -> today           KO-96h -> KO+3h
  soccer_jul    soccer, 2026-07-01 -> 2026-08-01               KO-96h -> KO+3h
  stage_m_pre   the Stage M tokens (--stage-m-tokens file)     KO-96h -> KO-90'
  nfl           NFL, 2025-09-01 -> today                       KO-96h -> KO+5h
  nba           NBA, 2025-10-01 -> today                       KO-96h -> KO+5h
  soccer_rest   soccer, 2026-06-01 -> 2026-10-03 minus July    KO-96h -> KO+3h
                (Stage M tokens skipped; run it last, if time is left)

`p` is the old endpoint's price, which is NOT a fill: (best bid + best ask)/2
with a missing bid counted as 0 and a missing ask as 1 (see Stage M). Token0
only: that rule is symmetric, so token1's series is 1 - p.

Output, one gzip member per batch:

  ingest/data/price_paths_v1/<tier>/YYYY-MM.jsonl.gz
      {"m": condition_id, "tok": token0, "ko": ko_ts, "q": question,
       "smt": sportsMarketType, "ev": event slug, "win": 1|0|null (token0),
       "t": [min from KO], "p": [price]}

Only CHANGES are kept: a point whose price equals the previous one is dropped
(the first and last points always stay). Read a path as a step function.
`done.txt` per tier makes every run resumable.

Usage:
    python stage_m_archive.py --tier soccer_oct --limit 20 --dry-run
    python stage_m_archive.py --tier all --stage-m-tokens stage_m_tokens.jsonl
    python stage_m_archive.py --summary
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('stage_m_archive')

OUT_DIR = Path(__file__).resolve().parent / 'data' / 'price_paths_v1'
GAMMA_KEYSET = 'https://gamma-api.polymarket.com/events/keyset'
PM_URL = 'https://clob.polymarket.com/prices-history'
DEADLINE = dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)

SOCCER, NFL, NBA = 100350, 450, 745
H = 3600
D = dt.date

TIERS = {
    'soccer_oct':  dict(tag=SOCCER, start=D(2026, 10, 3), end=None, before=96 * H, after=3 * H),
    'soccer_jul':  dict(tag=SOCCER, start=D(2026, 7, 1), end=D(2026, 8, 1), before=96 * H, after=3 * H),
    'stage_m_pre': dict(tag=None, before=96 * H, after=-90 * 60),
    'nfl':         dict(tag=NFL, start=D(2025, 9, 1), end=None, before=96 * H, after=5 * H),
    'nba':         dict(tag=NBA, start=D(2025, 10, 1), end=None, before=96 * H, after=5 * H),
    'soccer_rest': dict(tag=SOCCER, start=D(2026, 6, 1), end=D(2026, 10, 3), before=96 * H, after=3 * H,
                        skip=(D(2026, 7, 1), D(2026, 8, 1))),
}
CHUNK = 2000
ORDER = ['soccer_oct', 'soccer_jul', 'stage_m_pre', 'nfl', 'nba', 'soccer_rest']


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


def _get(session, limiter, url: str, params: dict):
    """GET with backoff on 429/5xx/network errors. None on 400/404."""
    delay = 2.0
    for _ in range(8):
        if limiter:
            limiter.wait()
        try:
            r = session.get(url, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(delay); delay = min(delay * 2, 60)
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


def _ts(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip().replace(' ', 'T').replace('Z', '+00:00')
    if s.endswith('+00'):
        s += ':00'
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp())


def _winner0(m: dict):
    try:
        p0 = float(json.loads(m.get('outcomePrices') or '[]')[0])
    except (ValueError, IndexError, TypeError):
        return None
    return 1 if p0 >= 0.99 else 0 if p0 <= 0.01 else None


def _events_on(session, tag: int, day: dt.date) -> list[dict]:
    """One day of closed events. `/events` refuses offsets past 10,000 and a
    busy soccer day has more, so this pages `/events/keyset` by
    `after_cursor` (any other name is silently ignored and returns page 1
    forever). A fixture event's endDate IS its kick-off."""
    out, cursor = [], None
    while True:
        params = {'tag_id': tag, 'closed': 'true', 'limit': 100, 'volume_min': 0.01,
                  'end_date_min': day.isoformat(),
                  'end_date_max': (day + dt.timedelta(days=1)).isoformat()}
        if cursor:
            params['after_cursor'] = cursor
        r = _get(session, None, GAMMA_KEYSET, params) or {}
        out += r.get('events') or []
        cursor = r.get('next_cursor')
        if not cursor or not r.get('events'):
            return out


def _rows_of(evs: list[dict], skip_toks: set[str], now: int) -> list[dict]:
    rows = []
    for e in evs:
        for m in e.get('markets') or []:
            cid = m.get('conditionId')
            if not cid or float(m.get('volumeNum') or 0) <= 0:
                continue
            try:
                tok = json.loads(m.get('clobTokenIds') or '[]')[0]
            except (ValueError, IndexError):
                continue
            if tok in skip_toks:
                continue
            ko = _ts(m.get('gameStartTime')) or _ts(e.get('startTime'))
            if not ko or ko > now:
                continue
            rows.append({'m': cid, 'tok': tok, 'ko': ko, 'q': m.get('question'),
                         'smt': m.get('sportsMarketType'), 'ev': e.get('slug'),
                         'win': _winner0(m)})
    return rows


def gamma_universe(tier: dict, skip_toks: set[str]) -> list[dict]:
    """Every traded market of the tier, newest first. Each day's events are
    reduced to rows as they arrive: a year of raw NBA/NFL events held at once
    does not fit beside the daemons on the 3.8GB server."""
    session = requests.Session()
    end = tier['end'] or dt.date.today()
    days = [tier['start'] + dt.timedelta(days=i) for i in range((end - tier['start']).days + 1)]
    lo, hi = tier.get('skip') or (None, None)
    if lo:
        days = [d for d in days if not (lo <= d < hi)]
    now = int(time.time())
    with ThreadPoolExecutor(4) as ex:
        per_day = list(ex.map(lambda d: _rows_of(_events_on(session, tier['tag'], d), skip_toks, now), days))
    rows, seen = [], set()
    for day_rows in per_day:
        for r in day_rows:
            if r['m'] not in seen:
                seen.add(r['m'])
                rows.append(r)
    return sorted(rows, key=lambda r: r['ko'], reverse=True)


def stage_m_universe(path: Path) -> list[dict]:
    rows = []
    with open(path) as fh:
        for ln in fh:
            r = json.loads(ln)
            rows.append({'m': r['m'], 'tok': r['tok'], 'ko': int(r['ko']),
                         'q': None, 'smt': None, 'ev': None, 'win': None})
    return sorted(rows, key=lambda r: r['ko'], reverse=True)


def compress(hist: list[dict], ko: int) -> tuple[list[int], list[float]]:
    """Minutes from KO and prices, keeping only the points where the price
    changes (plus the first and the last)."""
    hist = sorted(hist, key=lambda x: x['t'])
    t, p = [], []
    for i, h in enumerate(hist):
        v = round(float(h['p']), 4)
        if p and v == p[-1] and i != len(hist) - 1:
            continue
        t.append(round((h['t'] - ko) / 60))
        p.append(v)
    return t, p


def fetch(session, limiter, row: dict, tier: dict) -> dict:
    ko = row['ko']
    d = _get(session, limiter, PM_URL, {
        'market': row['tok'], 'startTs': ko - tier['before'],
        'endTs': ko + tier['after'], 'fidelity': 1,
    })
    t, p = compress((d or {}).get('history') or [], ko)
    return {**row, 't': t, 'p': p}


def done_ids(name: str) -> set[str]:
    f = OUT_DIR / name / 'done.txt'
    if not f.exists():
        return set()
    return {ln.strip() for ln in f.read_text().splitlines() if ln.strip()}


def flush(name: str, buf: dict[str, list[dict]]) -> int:
    """Append the data, THEN mark the ids done: a crash between the two
    re-fetches a batch rather than losing it."""
    vdir = OUT_DIR / name
    vdir.mkdir(parents=True, exist_ok=True)
    ids, n = [], 0
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


def run_tier(name: str, args, stage_m_toks: set[str]) -> None:
    tier = TIERS[name]
    t0 = time.time()
    if name == 'stage_m_pre':
        if not args.stage_m_tokens:
            log.warning('stage_m_pre needs --stage-m-tokens; skipped')
            return
        rows = stage_m_universe(Path(args.stage_m_tokens))
    else:
        rows = gamma_universe(tier, stage_m_toks if name == 'soccer_rest' else set())
    done = done_ids(name)
    todo = [r for r in rows if r['m'] not in done]
    if args.limit:
        todo = todo[:args.limit]
    log.info(f'{name}: {len(rows)} traded markets ({time.time() - t0:.0f}s to list), '
             f'{len(done)} done, {len(todo)} to fetch')
    if not todo:
        return

    limiter = Limiter(args.rate)
    session = requests.Session()   # library default User-Agent, on purpose
    adapter = requests.adapters.HTTPAdapter(pool_connections=args.workers, pool_maxsize=args.workers)
    session.mount('https://', adapter)
    buf: dict[str, list[dict]] = defaultdict(list)
    written = empty = failed = 0
    t0 = time.time()

    def results():
        # In chunks: a finished future holds its record until the dict of
        # futures goes, and 120k of them grew the process ~5MB per 1,000.
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for c in range(0, len(todo), CHUNK):
                futs = {ex.submit(fetch, session, limiter, r, tier): r for r in todo[c:c + CHUNK]}
                for fut in as_completed(futs):
                    yield futs[fut], fut
                del futs

    for i, (r, fut) in enumerate(results(), 1):
        try:
            rec = fut.result()
        except Exception as e:
            failed += 1
            log.warning(f'{name} {r["m"]}: {e}')
            continue
        empty += not rec['t']
        if args.dry_run:
            if i <= 3:
                log.info(f'  sample {r["m"][:12]} {r["q"]}: {len(rec["t"])} changes, '
                         f't {rec["t"][:3]}..{rec["t"][-3:]}')
            continue
        month = dt.datetime.fromtimestamp(r['ko'], tz=dt.timezone.utc).strftime('%Y-%m')
        buf[month].append(rec)
        if sum(len(v) for v in buf.values()) >= 500:
            written += flush(name, buf)
        if i % 5000 == 0:
            el = time.time() - t0
            left = (len(todo) - i) / (i / el) / 3600
            log.info(f'{name}: {i}/{len(todo)} ({i / el:.1f}/s, ~{left:.1f}h left), '
                     f'{empty} empty, {failed} failed')
    if not args.dry_run:
        written += flush(name, buf)
    log.info(f'{name}: wrote {written}, empty {empty}, failed {failed} in {time.time() - t0:.0f}s')


def iter_paths(name: str):
    for f in sorted((OUT_DIR / name).glob('*.jsonl.gz')):
        with gzip.open(f, 'rt') as fh:
            for line in fh:
                yield json.loads(line)


def summary() -> None:
    for name in ORDER:
        vdir = OUT_DIR / name
        if not vdir.exists():
            print(f'{name}: nothing yet')
            continue
        n = pts = empty = 0
        for rec in iter_paths(name):
            n += 1
            pts += len(rec['t'])
            empty += not rec['t']
        size = sum(f.stat().st_size for f in vdir.glob('*.jsonl.gz'))
        print(f'{name}: {n} markets, {pts} price changes, {empty} empty, {size / 1e6:.1f} MB')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tier', default='all', choices=ORDER + ['all'])
    ap.add_argument('--stage-m-tokens', help='jsonl of {m, tok, ko} for the Stage M tokens')
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--rate', type=float, default=12.0, help='requests per second, all workers')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--summary', action='store_true')
    args = ap.parse_args()
    if args.summary:
        summary()
        return
    if dt.datetime.now(dt.timezone.utc) >= DEADLINE:
        log.warning('past the shutdown of clob /prices-history; expect nothing')
    stage_m_toks = set()
    if args.stage_m_tokens:
        with open(args.stage_m_tokens) as fh:
            stage_m_toks = {json.loads(ln)['tok'] for ln in fh}
    for name in (ORDER if args.tier == 'all' else [args.tier]):
        run_tier(name, args, stage_m_toks)


if __name__ == '__main__':
    main()
