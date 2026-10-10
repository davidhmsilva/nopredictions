#!/usr/bin/env python3
"""
The cross-venue maker window, replayed against real trades.

cross_venue_arb_sports.py records, each minute, the "maker window": rest a bid
on one venue, and if it fills, take the other leg of a $1 basket on the other
venue. A positive window says the pair WOULD lock a profit if the bid were
filled and the other venue had not moved. This asks whether it does, given
when fills actually arrive.

THE ORDER
    At snapshot k, for every leg with a bid: the best hedge on the OTHER venue
    (any leg completing a $1 basket in every scenario) and the window
        1 - bid - maker fee(bid) - hedge ask - taker fee(hedge ask).
    If the window clears `--threshold-pp`, an order for `--size` shares rests
    at that bid -- joining the queue, not improving it -- until snapshot k+1.

THE FILL, from the venue's own taker prints
    A print BELOW our price means every bid at our price was consumed, ours
    included: filled in full. A print AT our price fills us only past the
    size that was already queued there when we joined (back of the queue;
    cancellations ahead of us are ignored, so this is conservative).
    Polymarket: a bid on token T is consumed by a taker SELL of T, or by a
    taker BUY of the complementary token at 1 - p (the same book seen from the
    other side). Kalshi: a YES bid by `taker_side == 'no'`, a NO bid by
    `taker_side == 'yes'`.

THE HEDGE, bracketed
    optimistic  at the hedge ask quoted when the order was posted -- an
                instant hedge, as if the other venue could not have moved.
                This is the window itself, i.e. what reading the window as
                profit assumes.
    delayed     at the best hedge ask in the NEXT snapshot (<= ~60s after
                the fill). A real bot hedges in under a second, so the truth
                sits between the two; the gap between them is how much the
                other venue moved, which is the adverse selection.

Per filled share: payout - bid - maker fee - hedge ask - taker fee. Clustered
by game for the CI, because fills on one game are not independent.

    python maker_window_sim.py data/arb_scan/scan_2026-10-05_night_v2.jsonl
    python maker_window_sim.py <file> --threshold-pp 0.5 --size 100
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

import venues  # noqa: E402

log = logging.getLogger('maker_sim')

DATA_API = 'https://data-api.polymarket.com'
CACHE = Path(__file__).parent / 'data' / 'arb_scan' / 'trades_cache'

#: Our order is not in the book before this many seconds after the snapshot.
POST_LATENCY_S = 2.0
#: A snapshot more than this far from the previous one ends the order unseen.
MAX_GAP_S = 150.0
EPS = 1e-6


def ts(s: str) -> float:
    # Kalshi writes 1-6 fractional digits ('21:55:19.23115'); Python 3.9's
    # fromisoformat accepts only 3 or 6.
    s = s.replace('Z', '+00:00')
    s = re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], s, count=1)
    return datetime.fromisoformat(s).timestamp()


# ---------------------------------------------------------------------------
# Legs and hedges
# ---------------------------------------------------------------------------

class L:
    __slots__ = ('label', 'venue', 'ref', 'market', 'side', 'pay', 'taker', 'maker',
                 'bid', 'bid_size', 'ask', 'ask_size')

    def __init__(self, row):
        (self.label, self.venue, self.ref, self.market, self.side, self.pay, self.taker,
         self.maker, self.bid, self.bid_size, self.ask, self.ask_size) = row


def hedges(legs: list, i: int) -> list:
    """Legs on the other venue that, with leg i, pay >= $1 in every scenario."""
    a = legs[i]
    out = []
    for j, b in enumerate(legs):
        if b.venue == a.venue:
            continue
        m = min(x + y for x, y in zip(a.pay, b.pay))
        if m >= 1 - EPS:
            out.append((j, m))
    return out


def window(rest: L, hedge: L, m: float) -> float | None:
    if rest.bid is None or hedge.ask is None or not (0 < rest.bid < 1):
        return None
    b, a = rest.bid, hedge.ask
    return m - b - rest.maker * b * (1 - b) - a - hedge.taker * a * (1 - a)


def best_hedge(legs: list, i: int):
    """(window per share, hedge leg) for the cheapest hedge of leg i, or None."""
    best = None
    for j, m in hedges(legs, i):
        h = legs[j]
        if h.ask is None:
            continue
        cost = h.ask + h.taker * h.ask * (1 - h.ask)
        if best is None or m - cost > best[0]:
            best = (m - cost, h)
    return best


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def _cached(name: str, fetch):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if p.exists():
        return json.loads(p.read_text())
    data = fetch()
    p.write_text(json.dumps(data))
    return data


def pm_trades(condition_id: str, t0: float, t1: float) -> list:
    """Every taker print on one Polymarket market in [t0, t1], newest first,
    paged backwards on `end` (offset pagination stops at a few thousand)."""
    def fetch():
        out, seen, end = [], set(), int(t1)
        for _ in range(200):
            r = requests.get(f'{DATA_API}/trades', params={
                'market': condition_id, 'takerOnly': 'true', 'limit': 500,
                'start': int(t0), 'end': end}, timeout=30)
            r.raise_for_status()
            page = r.json() or []
            fresh = 0
            for x in page:
                key = (x.get('transactionHash'), x.get('asset'), x.get('size'), x.get('price'),
                       x.get('timestamp'), x.get('proxyWallet'))
                if key not in seen:
                    seen.add(key)
                    out.append({'t': float(x['timestamp']), 'asset': str(x['asset']),
                                'side': x['side'], 'price': float(x['price']),
                                'size': float(x['size'])})
                    fresh += 1
            if len(page) < 500 or fresh == 0:
                break
            end = int(min(float(x['timestamp']) for x in page))
        return out
    return _cached(f'pm_{condition_id}_{int(t0)}_{int(t1)}.json', fetch)


def kalshi_trades(ticker: str, t0: float, t1: float) -> list:
    def fetch():
        out, cursor = [], ''
        for _ in range(200):
            params = {'ticker': ticker, 'min_ts': int(t0), 'max_ts': int(t1), 'limit': 1000}
            if cursor:
                params['cursor'] = cursor
            d = venues._paced_get('/markets/trades', params)
            for x in d.get('trades') or []:
                out.append({'t': ts(x['created_time']), 'taker_side': x.get('taker_side'),
                            'yes': float(x['yes_price_dollars']),
                            'size': float(x.get('count_fp') or x.get('count') or 0)})
            cursor = d.get('cursor') or ''
            if not cursor or not d.get('trades'):
                break
        return out
    return _cached(f'k_{ticker}_{int(t0)}_{int(t1)}.json', fetch)


def bid_prints(leg: L, trades: list, complement: str | None) -> list:
    """(time, price in leg-space, size) for every print that consumed bids on
    this leg."""
    out = []
    if leg.venue == venues.POLYMARKET:
        for x in trades:
            if x['asset'] == leg.ref and x['side'] == 'SELL':
                out.append((x['t'], x['price'], x['size']))
            elif complement and x['asset'] == complement and x['side'] == 'BUY':
                out.append((x['t'], 1.0 - x['price'], x['size']))
    else:
        for x in trades:
            if leg.side == 'YES' and x['taker_side'] == 'no':
                out.append((x['t'], x['yes'], x['size']))
            elif leg.side == 'NO' and x['taker_side'] == 'yes':
                out.append((x['t'], 1.0 - x['yes'], x['size']))
    out.sort()
    return out


def fill(prints: list, price: float, queue: float, size: float, t0: float, t1: float):
    """(fill time, filled shares) for a bid at `price` resting from t0 to t1."""
    filled, at_level, first = 0.0, 0.0, None
    for t, p, s in prints:
        if t < t0 + POST_LATENCY_S or t >= t1:
            continue
        if p < price - EPS:
            return (first if first is not None else t), size     # traded through us
        if abs(p - price) <= EPS:
            at_level += s
            got = min(size, max(0.0, at_level - queue))
            if got > filled:
                filled = got
                if first is None:
                    first = t
                if filled >= size - EPS:
                    return first, size
    return (first, filled) if filled > 0 else None


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------

def load(paths) -> dict:
    games = defaultdict(list)
    for p in paths:
        for line in open(p):
            r = json.loads(line)
            if 'tob' in r:
                games[(r['sport'], r['game'])].append(r)
    for rs in games.values():
        rs.sort(key=lambda r: r['t'])
    return games


def replay(games: dict, threshold_pp: float, size: float) -> list:
    orders = []
    for (sport, game), rs in games.items():
        for k in range(len(rs) - 1):
            r0, r1 = rs[k], rs[k + 1]
            t0, t1 = ts(r0['t']), ts(r1['t'])
            if t1 - t0 > MAX_GAP_S:
                continue
            legs0 = [L(x) for x in r0['tob']]
            legs1 = [L(x) for x in r1['tob']]
            if [x.ref for x in legs0] != [x.ref for x in legs1]:
                continue
            for i, leg in enumerate(legs0):
                if leg.bid is None or not (0 < leg.bid < 1):
                    continue
                bh = best_hedge(legs0, i)
                if bh is None:
                    continue
                w = bh[0] - leg.bid - leg.maker * leg.bid * (1 - leg.bid)
                if 100 * w <= threshold_pp:
                    continue
                orders.append({'sport': sport, 'game': game, 'state': r0['state'], 'i': i,
                               'leg': leg, 'hedge0': bh[1], 'window': w, 't0': t0, 't1': t1,
                               'legs0': legs0, 'legs1': legs1})
    return orders


def attach_fills(orders: list, size: float) -> None:
    # One trade fetch per market over the span it was needed.
    span = defaultdict(lambda: [float('inf'), 0.0])
    for o in orders:
        leg = o['leg']
        key = (leg.venue, leg.market if leg.venue == venues.POLYMARKET else leg.ref)
        span[key][0] = min(span[key][0], o['t0'])
        span[key][1] = max(span[key][1], o['t1'])
    log.info('fetching trades for %d markets', len(span))
    trades = {}
    for (venue, ref), (a, b) in span.items():
        try:
            trades[(venue, ref)] = (pm_trades(ref, a - 5, b + 5) if venue == venues.POLYMARKET
                                    else kalshi_trades(ref, a - 5, b + 5))
        except Exception as e:      # noqa: BLE001
            log.warning('trades %s %s: %s', venue, ref[:20], e)
            trades[(venue, ref)] = None

    for o in orders:
        leg = o['leg']
        key = (leg.venue, leg.market if leg.venue == venues.POLYMARKET else leg.ref)
        tr = trades.get(key)
        if tr is None:
            o['status'] = 'no_trades'
            continue
        comp = None
        if leg.venue == venues.POLYMARKET:
            comp = next((x.ref for x in o['legs0'] if x.venue == leg.venue
                         and x.market == leg.market and x.ref != leg.ref), None)
        prints = bid_prints(leg, tr, comp)
        f = fill(prints, leg.bid, leg.bid_size or 0.0, size, o['t0'], o['t1'])
        if f is None:
            o['status'] = 'unfilled'
            continue
        o['fill_t'], o['filled'] = f
        b = leg.bid
        cost_rest = b + leg.maker * b * (1 - b)
        o['pnl_opt'] = o['window']          # hedged at the price seen when posting
        bh1 = best_hedge(o['legs1'], o['i'])
        if bh1 is None:
            o['status'] = 'unhedged'
            continue
        o['pnl_delay'] = bh1[0] - cost_rest
        o['status'] = 'filled'


def _boot_ci(groups: dict, stat, n=2000, seed=7):
    keys = list(groups)
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        sample = [x for _ in keys for x in groups[rnd.choice(keys)]]
        if sample:
            vals.append(stat(sample))
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def report(orders: list, size: float, threshold_pp: float) -> None:
    print(f'\nMaker window replay · post when window > {threshold_pp:.2f}pp · {size:.0f} shares '
          f'per order · back of the queue · order lives one snapshot (~60s)\n')
    by = defaultdict(list)
    for o in orders:
        rest = 'rest PM' if o['leg'].venue == venues.POLYMARKET else 'rest K'
        by[(o['sport'], o['state'], rest)].append(o)
        by[('ALL', o['state'], rest)].append(o)
        by[('ALL', 'all', 'all')].append(o)
    hdr = (f"{'sport':7s}{'state':6s}{'rests':8s}{'orders':>7s}{'filled':>7s}{'fill%':>6s}"
           f"{'window@post':>12s}{'fill: instant hedge':>21s}{'hedge next min':>16s}"
           f"{'  95% CI (next min)':>22s}{'unhedged':>9s}")
    print(hdr)
    print('-' * len(hdr))
    for key in sorted(by, key=lambda k: (k[0] == 'ALL', k)):
        os_ = by[key]
        f = [o for o in os_ if o.get('status') == 'filled']
        unh = sum(1 for o in os_ if o.get('status') == 'unhedged')
        nofeed = sum(1 for o in os_ if o.get('status') == 'no_trades')
        n = len(os_) - nofeed
        w = 100 * statistics.mean(o['window'] for o in os_)
        if f:
            sh = sum(o['filled'] for o in f)
            opt = 100 * sum(o['pnl_opt'] * o['filled'] for o in f) / sh
            dly = 100 * sum(o['pnl_delay'] * o['filled'] for o in f) / sh
            groups = defaultdict(list)
            for o in f:
                groups[o['game']].append(o)
            lo, hi = _boot_ci(groups, lambda xs: 100 * sum(o['pnl_delay'] * o['filled'] for o in xs)
                              / sum(o['filled'] for o in xs))
            tail = f'{opt:+20.2f}pp{dly:+14.2f}pp   [{lo:+6.2f}, {hi:+6.2f}]'
        else:
            tail = f"{'—':>21s}{'—':>16s}{'':22s}"
        print(f"{key[0]:7s}{key[1]:6s}{key[2]:8s}{n:7d}{len(f):7d}{100 * len(f) / max(n, 1):6.1f}"
              f"{w:+11.2f}pp{tail}{unh:9d}")
    f = [o for o in orders if o.get('status') == 'filled']
    if f:
        usd = sum(o['pnl_delay'] * o['filled'] for o in f)
        usd_opt = sum(o['pnl_opt'] * o['filled'] for o in f)
        print(f'\nP&L over the whole replay, {len(f)} fills: ${usd:+.2f} hedged a minute late, '
              f'${usd_opt:+.2f} hedged instantly at the posted price.')
        # The window's promise vs what fills deliver: adverse selection.
        mw = 100 * statistics.mean(o['window'] for o in f)
        print(f'Window when the FILLED orders were posted: {mw:+.2f}pp. '
              f'The gap to the delayed hedge is what the other venue moved by the time you were filled.')
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', type=Path, nargs='+')
    ap.add_argument('--threshold-pp', type=float, default=0.0)
    ap.add_argument('--size', type=float, default=100.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
    games = load(args.files)
    orders = replay(games, args.threshold_pp, args.size)
    log.info('%d game-snapshots → %d orders posted', sum(len(v) for v in games.values()), len(orders))
    attach_fills(orders, args.size)
    report(orders, args.size, args.threshold_pp)


if __name__ == '__main__':
    main()
