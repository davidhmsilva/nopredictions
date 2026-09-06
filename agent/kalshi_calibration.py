#!/usr/bin/env python3
"""
Reproduce Kalshi's calibration study (Kalshi Research, August 2026) on the only
slice we care about: SOCCER 1X2.

Why bother when they published the result? Because the published reliability
curves sit BELOW the 45-degree line at essentially every bucket and every
horizon — a uniform ~1.5-2.5pp YES-rich tilt at 1 day, 5-8pp at 3 months. A
uniform downward shift is not favourite-longshot bias (that tilts the two ends
in opposite directions). It is what you see when the plotted "predicted
probability" still carries the overround. Kalshi's soccer 1X2 overround is
~3.3pp; half of that is 1.65pp, which is the size of the gap.

So the question this script answers is exactly one question:

    does the YES-rich tilt survive de-vigging?

If it does, there is a real bias to fade. If it does not, the tilt is the
market's margin drawn as if it were an error, and the venue is calibrated —
which is the same verdict we already reached for Polymarket in
finding_pm_mid_is_pinnacle.

Method, and where it deliberately differs from theirs:

  * Universe: every SETTLED event in a soccer *GAME* series (1X2). Three
    mutually-exclusive legs per event, so the de-vig is well defined. Their
    study pools eleven categories; pooling is what lets a margin hide.
  * Price: mid of (yes_bid, yes_ask) from the hourly candlestick close. Not
    last trade — a last trade is whichever side crossed, which is itself a
    source of tilt.
  * Anchor: estimated KICKOFF, which is not a field the API exposes.
    `occurrence_datetime` looks like the obvious choice and is a trap: it is
    byte-identical to `expected_expiration_time` on every event checked, i.e.
    the SCHEDULED EXPIRY, running a median +0.91h AFTER the winner was even
    declared. Anchoring on it puts the "1h before" panel at roughly half time,
    which is why that panel first came back with a Brier of 0.067 and 1,512
    legs sitting above 0.95 an hour "before" the match.
    `close_time` is when the winner is declared: kickoff + 45 + 15 + 45 +
    stoppage + a short declaration lag. Measured against known kickoffs
    (Arsenal-Coventry 21 Aug, Sirius-Hacken 21 Aug, Al Jazira-Al Ittihad
    11 Aug) that lag is 2.04-2.06h, so kickoff = close_time - 2.05h. Use
    --anchor close to see the close-anchored view instead.
  * Book filter: an untraded Kalshi book quotes 0.01/1.00, whose "mid" is 0.505
    of nothing. Every leg of an event must show a real two-sided book or the
    event is dropped, and the drop count is reported rather than buried.

Usage:
    python kalshi_calibration.py --harvest-events    # settled events + results
    python kalshi_calibration.py --harvest-prices    # candlesticks (slow)
    python kalshi_calibration.py --analyze           # the tables
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'ingest'))
from stage_i_kalshi import discover_soccer_series  # noqa: E402

API = os.getenv('KALSHI_API',
                'https://api.elections.kalshi.com/trade-api/v2').rstrip('/')
CACHE = Path(__file__).resolve().parent.parent / '.cache' / 'kalshi_calib'
EVENTS_F = CACHE / 'events.jsonl'
PRICES_F = CACHE / 'prices.jsonl'

# Horizons before kickoff, in hours. Kalshi's soccer books open ~2 weeks out,
# so their 1-month and 3-month panels have no soccer analogue at all.
HORIZONS = [('1h', 1), ('6h', 6), ('1d', 24), ('3d', 72), ('1w', 168)]

# close_time (winner declared) minus this = kickoff. See the module docstring:
# 90 min + 15 min half time + stoppage + declaration lag.
KICKOFF_LAG_H = 2.05

# An hourly grid means the candle at-or-before the target is <=1h stale. Allow
# a little more so a market that opened late is not silently dropped.
MAX_STALENESS_H = 6

# A leg wider than this is not a book, it is a placeholder. 26% of Kalshi
# markets quote 0.02/0.81 (see CLAUDE.md, Stage I).
MAX_SPREAD = 0.15

# Sanity band on the sum of the three raw mids. Outside this, something is
# wrong with the book, not with the market's opinion.
OVERROUND_BAND = (0.90, 1.35)

_local = threading.local()


class RateLimiter:
    """
    Global token bucket. Kalshi publishes no public-endpoint rate limit but
    penalises bursts: 16 threads with per-thread exponential backoff collapsed
    to 0.6 req/s, four times SLOWER than 6 threads, because every worker sat in
    its own backoff. Pacing globally is strictly better than retrying locally.
    """

    def __init__(self, rate: float):
        self.interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if self.next_at < now:
                self.next_at = now
            slot = self.next_at
            self.next_at += self.interval
        d = slot - time.monotonic()
        if d > 0:
            time.sleep(d)

    def penalise(self, seconds: float = 2.0):
        """A 429 pushes the whole bucket back, not just the caller."""
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


LIMITER = RateLimiter(float(os.getenv('KALSHI_RATE', '5')))


def session() -> requests.Session:
    s = getattr(_local, 's', None)
    if s is None:
        s = requests.Session()
        s.headers.update({'accept': 'application/json'})
        _local.s = s
    return s


def get(path: str, **params):
    """GET with 429-aware backoff. Kalshi publishes no public rate limit."""
    for attempt in range(6):
        LIMITER.wait()
        try:
            r = session().get(API + path, params=params, timeout=30)
        except requests.RequestException:
            LIMITER.penalise(1.0)
            continue
        if r.status_code == 429:
            LIMITER.penalise(2.0 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f'GET {path} failed after retries')


def anchor_of(markets, mode: str = 'kickoff'):
    """Anchor time for an event's three legs. See KICKOFF_LAG_H."""
    for m in markets:
        close = _iso(m.get('close_time'))
        if close:
            return close if mode == 'close' else close - timedelta(
                hours=KICKOFF_LAG_H)
    return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Harvest: settled events with nested markets (carries `result` per leg)
# ---------------------------------------------------------------------------

def harvest_events():
    CACHE.mkdir(parents=True, exist_ok=True)
    series = discover_soccer_series(CACHE.parent / 'kalshi', ['GAME'],
                                    use_cache=True)
    print(f'{len(series)} soccer GAME series', flush=True)
    n = 0
    with EVENTS_F.open('w') as out:
        for i, s in enumerate(series, 1):
            tk = s['ticker']
            cursor, got = None, 0
            while True:
                p = dict(series_ticker=tk, status='settled', limit=200,
                         with_nested_markets='true')
                if cursor:
                    p['cursor'] = cursor
                d = get('/events', **p)
                evs = d.get('events', [])
                for e in evs:
                    e['_series'] = tk
                    e['_competition'] = (e.get('product_metadata') or {}).get(
                        'competition') or tk
                    out.write(json.dumps(e) + '\n')
                got += len(evs)
                cursor = d.get('cursor')
                if not cursor or not evs:
                    break
                time.sleep(0.15)
            n += got
            print(f'  [{i:3d}/{len(series)}] {tk:26s} {got:5d}  (total {n})',
                  flush=True)
            time.sleep(0.15)
    print(f'wrote {n} settled events -> {EVENTS_F}')


# ---------------------------------------------------------------------------
# Harvest: hourly bid/ask candlesticks per leg
# ---------------------------------------------------------------------------

def _event_rows():
    with EVENTS_F.open() as f:
        for line in f:
            yield json.loads(line)


def harvest_prices(workers: int = 6, limit: int | None = None):
    done = set()
    if PRICES_F.exists():
        with PRICES_F.open() as f:
            for line in f:
                try:
                    done.add(json.loads(line)['ticker'])
                except Exception:
                    pass
        print(f'{len(done)} legs already cached — resuming')

    jobs = []
    for e in _event_rows():
        mkts = e.get('markets') or []
        if len(mkts) != 3:
            continue
        anchor = anchor_of(mkts)
        if not anchor:
            continue
        for m in mkts:
            t = m.get('ticker')
            if t and t not in done:
                jobs.append((e['_series'], t, anchor))
    if limit:
        jobs = jobs[:limit]
    print(f'{len(jobs)} candlestick calls to make', flush=True)

    lock = threading.Lock()
    out = PRICES_F.open('a')
    counter = {'n': 0, 'err': 0, 't0': time.time()}

    def work(job):
        series, ticker, anchor = job
        start = int((anchor - timedelta(days=15)).timestamp())
        end = int((anchor + timedelta(hours=6)).timestamp())
        try:
            d = get(f'/series/{series}/markets/{ticker}/candlesticks',
                    start_ts=start, end_ts=end, period_interval=60)
        except Exception:
            with lock:
                counter['err'] += 1
            return
        pts = []
        for c in d.get('candlesticks', []):
            ts = c.get('end_period_ts')
            b = _f((c.get('yes_bid') or {}).get('close_dollars'))
            a = _f((c.get('yes_ask') or {}).get('close_dollars'))
            if ts is None or b is None or a is None:
                continue
            pts.append([ts, b, a])
        rec = {'ticker': ticker, 'series': series,
               'anchor': int(anchor.timestamp()), 'pts': pts}
        with lock:
            out.write(json.dumps(rec) + '\n')
            counter['n'] += 1
            if counter['n'] % 500 == 0:
                el = time.time() - counter['t0']
                rate = counter['n'] / el
                left = (len(jobs) - counter['n']) / rate / 60
                print(f"  {counter['n']}/{len(jobs)}  {rate:.1f}/s  "
                      f"~{left:.0f} min left  errs={counter['err']}", flush=True)
                out.flush()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, jobs))
    out.close()
    print(f"done: {counter['n']} legs, {counter['err']} errors")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_prices():
    px = {}
    if not PRICES_F.exists():
        return px
    with PRICES_F.open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            px[r['ticker']] = r
    return px


def price_at(rec, target_ts: int):
    """Last hourly close at or before target. None if too stale or missing."""
    best = None
    for ts, b, a in rec['pts']:
        if ts <= target_ts and (best is None or ts > best[0]):
            best = (ts, b, a)
    if best is None:
        return None
    if target_ts - best[0] > MAX_STALENESS_H * 3600:
        return None
    return best


def build(horizon_h: int, px: dict, mode: str = 'kickoff'):
    """Return (rows, drops) for one horizon. One row per LEG."""
    rows = []
    drops = defaultdict(int)
    for e in _event_rows():
        mkts = e.get('markets') or []
        if len(mkts) != 3:
            drops['not_three_legs'] += 1
            continue
        anchor = anchor_of(mkts, mode)
        if not anchor:
            drops['no_anchor'] += 1
            continue
        target = int((anchor - timedelta(hours=horizon_h)).timestamp())

        legs = []
        ok = True
        for m in mkts:
            rec = px.get(m.get('ticker'))
            if rec is None:
                ok = False
                drops['no_candles'] += 1
                break
            p = price_at(rec, target)
            if p is None:
                ok = False
                drops['no_price_at_horizon'] += 1
                break
            _, b, a = p
            if a - b > MAX_SPREAD or a <= b:
                ok = False
                drops['placeholder_book'] += 1
                break
            res = (m.get('result') or '').lower()
            if res not in ('yes', 'no'):
                ok = False
                drops['unresolved'] += 1
                break
            legs.append({'mid': (a + b) / 2, 'bid': b, 'ask': a,
                         'won': 1 if res == 'yes' else 0,
                         'sub': m.get('yes_sub_title') or ''})
        if not ok:
            continue
        if sum(l['won'] for l in legs) != 1:
            drops['not_exactly_one_winner'] += 1
            continue
        s = sum(l['mid'] for l in legs)
        if not (OVERROUND_BAND[0] <= s <= OVERROUND_BAND[1]):
            drops['overround_out_of_band'] += 1
            continue
        for l in legs:
            rows.append({
                'raw': l['mid'],
                'devig': l['mid'] / s,
                'bid': l['bid'],
                'ask': l['ask'],
                'won': l['won'],
                'overround': s,
                'comp': e.get('_competition'),
                'event': e.get('event_ticker'),
            })
    return rows, drops


BINS = [(0.00, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40),
        (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90),
        (0.90, 0.95), (0.95, 1.00)]


def reliability(rows, key):
    out = []
    for lo, hi in BINS:
        sel = [r for r in rows if lo <= r[key] < hi]
        if not sel:
            continue
        n = len(sel)
        k = sum(r['won'] for r in sel)
        pred = sum(r[key] for r in sel) / n
        obs = k / n
        lo_ci, hi_ci = wilson(k, n)
        out.append({'lo': lo, 'hi': hi, 'n': n, 'pred': pred, 'obs': obs,
                    'ci': (lo_ci, hi_ci), 'gap': obs - pred})
    return out


def brier(rows, key):
    if not rows:
        return float('nan')
    return sum((r[key] - r['won']) ** 2 for r in rows) / len(rows)


def fmt_table(tab, title):
    lines = [f'  {title}',
             '   bucket        n      pred     obs     gap(pp)   95% CI on obs']
    for b in tab:
        lo, hi = b['ci']
        star = '' if (lo <= b['pred'] <= hi) else '  <-- CI excludes pred'
        lines.append(
            f"   {b['lo']:.2f}-{b['hi']:.2f} {b['n']:7d}   "
            f"{b['pred']*100:6.2f}  {b['obs']*100:6.2f}   "
            f"{b['gap']*100:+7.2f}   [{lo*100:5.1f},{hi*100:5.1f}]{star}")
    return '\n'.join(lines)


def ask_yield_table(rows):
    """
    The only translation that matters to us: buy every leg in the bucket at the
    ask, hold to settlement, 1u flat. Yield = (obs/ask_price) - 1.

    Kalshi's taker fee is ceil(0.07 * C * p * (1-p)) per contract, which peaks
    at 1.75pp of notional at p=0.50. It is charged on top, so the net column is
    the one to read. A bucket only means something if its CI clears zero AFTER
    the fee — nothing here is a result on its own, it is a place to look.
    """
    lines = ['  BUY-AT-ASK YIELD (1u flat, held to settlement)',
             '   bucket        n     ask     obs    gross%   fee(pp)    net%'
             '      95% CI on net%']
    for lo, hi in BINS:
        sel = [r for r in rows if lo <= r['ask'] < hi]
        if len(sel) < 30:
            continue
        n = len(sel)
        k = sum(r['won'] for r in sel)
        ask = sum(r['ask'] for r in sel) / n
        obs = k / n
        gross = obs / ask - 1
        fee = sum(0.07 * r['ask'] * (1 - r['ask']) for r in sel) / n
        # fee is per contract of $1 notional; per unit staked it is fee/ask
        fee_on_stake = fee / ask
        net = gross - fee_on_stake
        clo, chi = wilson(k, n)
        lines.append(
            f"   {lo:.2f}-{hi:.2f} {n:7d}  {ask*100:5.1f}  {obs*100:6.2f}  "
            f"{gross*100:+7.2f}  {fee_on_stake*100:6.2f}  {net*100:+7.2f}   "
            f"[{(clo/ask-1-fee_on_stake)*100:+7.2f},"
            f"{(chi/ask-1-fee_on_stake)*100:+7.2f}]")
    return '\n'.join(lines)


def favourite_longshot(rows):
    """
    The de-vig makes any *aggregate* tilt an identity: three legs normalised to
    1.000, exactly one winner. Splitting the legs by their RANK inside the event
    breaks that identity, and it is the classic test anyway — a favourite-
    longshot bias tilts the two ends in opposite directions, which is exactly
    the shape Kalshi's published curves do NOT show.
    """
    buckets = {0: [], 1: [], 2: []}
    for i in range(0, len(rows), 3):
        ev = rows[i:i + 3]
        if len(ev) != 3 or {r['event'] for r in ev} != {ev[0]['event']}:
            continue
        for rank, r in enumerate(sorted(ev, key=lambda x: -x['devig'])):
            buckets[rank].append(r)
    out = ['  BY RANK WITHIN EVENT (breaks the de-vig identity)',
           '   rank            n    devig pred    obs    tilt(pp)   95% CI on tilt']
    names = {0: 'favourite', 1: 'second', 2: 'longshot'}
    for rank in (0, 1, 2):
        sel = buckets[rank]
        if not sel:
            continue
        n = len(sel)
        k = sum(r['won'] for r in sel)
        pred = sum(r['devig'] for r in sel) / n
        lo, hi = wilson(k, n)
        out.append(f"   {names[rank]:12s} {n:6d}     {pred*100:6.2f}  "
                   f"{k/n*100:6.2f}    {(k/n-pred)*100:+7.2f}    "
                   f"[{(lo-pred)*100:+6.2f},{(hi-pred)*100:+6.2f}]")
    return '\n'.join(out)


def by_competition(rows, top=14):
    """
    Per-competition. NOT the de-vigged tilt — inside any set of complete events
    that is zero by construction, exactly as it is in aggregate. What varies is
    how much margin the book charges and how sharp it is, so: overround at the
    ask, and de-vigged Brier.
    """
    agg = defaultdict(lambda: {'n': 0, 'ask': 0.0, 'brier': 0.0, 'ev': set()})
    for r in rows:
        a = agg[r['comp']]
        a['n'] += 1
        a['ask'] += r['ask']
        a['brier'] += (r['devig'] - r['won']) ** 2
        a['ev'].add(r['event'])
    out = ['  BY COMPETITION',
           '   competition            legs   ask overround   devig Brier']
    for comp, a in sorted(agg.items(), key=lambda x: -x[1]['n'])[:top]:
        n = a['n']
        if n < 90:
            continue
        ov = a['ask'] / n * 3
        out.append(f"   {str(comp)[:20]:20s} {n:7d}      {(ov-1)*100:+6.2f}pp"
                   f"       {a['brier']/n:.5f}")
    return '\n'.join(out)


def analyze(args):
    px = load_prices()
    print(f'{len(px)} legs with candlesticks cached\n')
    for name, h in HORIZONS:
        if args.horizon and name != args.horizon:
            continue
        rows, drops = build(h, px, args.anchor)
        n_ev = len(rows) // 3
        print('=' * 78)
        print(f'HORIZON {name}  ({h}h before '
              f"{'kickoff' if args.anchor == 'kickoff' else 'market close'})")
        print(f'  {n_ev} events / {len(rows)} legs kept')
        if drops:
            print('  dropped: ' + ', '.join(
                f'{k}={v}' for k, v in sorted(drops.items(),
                                              key=lambda x: -x[1])))
        if not rows:
            print()
            continue
        ov = sum(r['overround'] for r in rows) / len(rows)
        print(f'  mean overround (sum of 3 raw mids): {ov:.4f} '
              f'= {(ov-1)*100:+.2f}pp')
        print(f"  Brier  ask {brier(rows,'ask'):.6f}   "
              f"raw-mid {brier(rows,'raw'):.6f}   "
              f"de-vigged {brier(rows,'devig'):.6f}   "
              f"bid {brier(rows,'bid'):.6f}")
        print()
        for key, label in (('ask', 'ASK  (what a taker actually pays)'),
                           ('raw', 'RAW MID (spread intact, not normalised)'),
                           ('devig', 'DE-VIGGED (three mids normalised to 1)')):
            print(fmt_table(reliability(rows, key), label))
            print()

        print('  AGGREGATE  (one row per leg; exactly one of three legs wins)')
        n = len(rows)
        k = sum(r['won'] for r in rows)
        lo, hi = wilson(k, n)
        for key, label in (('ask', 'ask'), ('raw', 'raw mid'),
                           ('devig', 'de-vigged'), ('bid', 'bid')):
            pred = sum(r[key] for r in rows) / n
            note = ''
            if key == 'devig':
                note = '   <- ZERO BY CONSTRUCTION, not a finding'
            print(f'    {label:11s}: predicted {pred*100:6.3f}%  '
                  f'observed {k/n*100:6.3f}%  '
                  f'tilt {(k/n-pred)*100:+.3f}pp{note}')
        print(f'    (observed CI [{lo*100:.3f},{hi*100:.3f}], n={n} legs)')
        print('    The de-vig normalises each event to 1.000 and exactly one leg')
        print('    wins, so its aggregate tilt is an identity. The de-vig test is')
        print('    the SHAPE of the bucket table above, never this number.')
        print()
        print(ask_yield_table(rows))
        print()
        print(favourite_longshot(rows))
        print()
        print(by_competition(rows))
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--harvest-events', action='store_true')
    ap.add_argument('--harvest-prices', action='store_true')
    ap.add_argument('--analyze', action='store_true')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--horizon', type=str, default=None)
    ap.add_argument('--anchor', choices=['kickoff', 'close'], default='kickoff')
    a = ap.parse_args()
    if a.harvest_events:
        harvest_events()
    if a.harvest_prices:
        harvest_prices(a.workers, a.limit)
    if a.analyze:
        analyze(a)
    if not (a.harvest_events or a.harvest_prices or a.analyze):
        ap.print_help()


if __name__ == '__main__':
    main()
