"""
Live lag recorder — sharp in-play line vs Polymarket, on the same clock.

Records two time series for ONE fixture, each row stamped with local monotonic
+ wall time, so the two can be cross-correlated afterwards:

  * api-football /odds/live, polled every N seconds. ONE call returns the whole
    live board, so the poll cost is independent of how many fixtures we watch.
    ⚠️ This feed carries NO bookmaker attribution — verified: no 'bookmaker',
    'betfair' or 'pinnacle' key anywhere in the payload. It is an anonymous
    aggregate. Any finding here is "PM vs an aggregate", never "PM vs Betfair".
  * Polymarket CLOB websocket (market channel) — real-time book + price_change
    for the event's YES tokens. A REST /book heartbeat runs alongside it so a
    silently dead socket is detectable rather than being read as "no movement".

Output: JSONL, one row per event, in reports/lag/<slug>.jsonl.
Row types: meta | af | pm_ws | pm_rest | note

Usage:
    python live_lag_recorder.py --slug mex-jua-ame-2026-08-21 --fixture 1550931
    python live_lag_recorder.py --slug ... --fixture ... --minutes 75
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any

import requests
import websockets
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))

AF_URL = 'https://v3.football.api-sports.io/odds/live'
GAMMA = 'https://gamma-api.polymarket.com/events'
CLOB = 'https://clob.polymarket.com'
WS_URLS = [
    # The old ws-subscribe.polymarket.com host no longer resolves, and the RTDS
    # service (ws-live-data) answers a CLOB subscription with "CLOB messages are
    # not supported anymore". This is the live one, per the CLOB docs (2026-08).
    'wss://ws-subscriptions-clob.polymarket.com/ws/market',
]

# Markets kept from the api-football payload. Everything else is dropped to keep
# the file readable; the 1X2 arm is the one comparable with PM's three markets.
AF_MARKETS = {'Fulltime Result', 'Match Goals', 'Over/Under Line', 'Draw No Bet'}

_t0 = time.monotonic()


def now() -> dict[str, float]:
    return {'t': round(time.time(), 3), 'el': round(time.monotonic() - _t0, 3)}


class Writer:
    def __init__(self, path: str):
        self.f = open(path, 'a', buffering=1)

    def row(self, kind: str, **kw: Any) -> None:
        self.f.write(json.dumps({'kind': kind, **now(), **kw}, separators=(',', ':')) + '\n')


def pm_event(slug: str) -> dict:
    r = requests.get(GAMMA, params={'slug': slug}, timeout=20)
    r.raise_for_status()
    evs = r.json()
    if not evs:
        raise SystemExit(f'no PM event for slug {slug}')
    return evs[0]


def token_map(ev: dict) -> dict[str, dict]:
    """YES token id -> {question, slug}. One binary market per 1X2 outcome."""
    out = {}
    for m in ev.get('markets', []):
        toks = m.get('clobTokenIds')
        if isinstance(toks, str):
            toks = json.loads(toks)
        if not toks:
            continue
        out[toks[0]] = {'q': m.get('question'), 'slug': m.get('slug'), 'side': 'YES'}
    return out


async def poll_af(w: Writer, fixture: int, interval: float, stop: asyncio.Event) -> None:
    key = os.getenv('FOOTBALL_API_KEY')
    headers = {'x-apisports-key': key}
    misses = 0
    while not stop.is_set():
        try:
            r = await asyncio.to_thread(
                requests.get, AF_URL, params={'fixture': fixture},
                headers=headers, timeout=15,
            )
            if r.status_code != 200:
                w.row('note', src='af', http=r.status_code, body=r.text[:200])
            else:
                data = r.json().get('response', [])
                if not data:
                    misses += 1
                    w.row('note', src='af', empty=True, misses=misses)
                else:
                    it = data[0]
                    odds = [m for m in (it.get('odds') or []) if m.get('name') in AF_MARKETS]
                    w.row(
                        'af',
                        update=it.get('update'),
                        status=it.get('fixture', {}).get('status', {}),
                        state=it.get('status', {}),
                        goals={'h': it.get('teams', {}).get('home', {}).get('goals'),
                               'a': it.get('teams', {}).get('away', {}).get('goals')},
                        odds=odds,
                    )
        except Exception as e:  # never let the poller die
            w.row('note', src='af', error=repr(e)[:200])
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def poll_pm_rest(w: Writer, tokens: list[str], interval: float, stop: asyncio.Event) -> None:
    """Heartbeat: a dead websocket must not read as a quiet market."""
    while not stop.is_set():
        for tok in tokens:
            try:
                r = await asyncio.to_thread(
                    requests.get, f'{CLOB}/book', params={'token_id': tok}, timeout=15)
                if r.status_code == 200:
                    b = r.json()
                    bids, asks = b.get('bids') or [], b.get('asks') or []
                    best_bid = max((float(x['price']) for x in bids), default=None)
                    best_ask = min((float(x['price']) for x in asks), default=None)
                    w.row('pm_rest', token=tok, bid=best_bid, ask=best_ask,
                          ts=b.get('timestamp'))
                else:
                    w.row('note', src='pm_rest', http=r.status_code, token=tok)
            except Exception as e:
                w.row('note', src='pm_rest', error=repr(e)[:200])
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def ws_pm(w: Writer, tokens: list[str], stop: asyncio.Event) -> None:
    sub = json.dumps({'assets_ids': tokens, 'type': 'market',
                      'custom_feature_enabled': True})
    url_i = 0
    while not stop.is_set():
        url = WS_URLS[url_i % len(WS_URLS)]
        url_i += 1
        try:
            async with websockets.connect(url, open_timeout=15, ping_interval=None) as ws:
                await ws.send(sub)
                w.row('note', src='pm_ws', connected=url, n_tokens=len(tokens))

                async def keepalive():
                    while not stop.is_set():
                        try:
                            await ws.send('PING')
                        except Exception:
                            return
                        await asyncio.sleep(10)

                ka = asyncio.create_task(keepalive())
                try:
                    while not stop.is_set():
                        msg = await asyncio.wait_for(ws.recv(), timeout=120)
                        if msg == 'PONG':
                            continue
                        try:
                            payload = json.loads(msg)
                        except Exception:
                            w.row('pm_ws', raw=str(msg)[:200])
                            continue
                        events = payload if isinstance(payload, list) else [payload]
                        for ev in events:
                            w.row('pm_ws', ev=ev)
                finally:
                    ka.cancel()
        except Exception as e:
            w.row('note', src='pm_ws', error=repr(e)[:200], url=url)
            await asyncio.sleep(3)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--slug', required=True)
    ap.add_argument('--fixture', type=int, required=True)
    ap.add_argument('--interval', type=float, default=10.0)
    ap.add_argument('--rest-interval', type=float, default=30.0)
    ap.add_argument('--minutes', type=float, default=75.0)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    path = a.out or os.path.join(os.path.dirname(__file__), '..', 'reports', 'lag',
                                 f'{a.slug}.jsonl')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w = Writer(path)

    ev = pm_event(a.slug)
    tmap = token_map(ev)
    tokens = list(tmap)
    w.row('meta', slug=a.slug, fixture=a.fixture, title=ev.get('title'),
          startDate=ev.get('startDate'), tokens=tmap, interval=a.interval,
          note='af feed has NO bookmaker attribution — anonymous aggregate')
    print(f'[lag] {ev.get("title")} -> {path}')
    for t, m in tmap.items():
        print(f'  {m["q"]}  {t[:12]}…')

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(poll_af(w, a.fixture, a.interval, stop)),
        asyncio.create_task(ws_pm(w, tokens, stop)),
        asyncio.create_task(poll_pm_rest(w, tokens, a.rest_interval, stop)),
    ]
    try:
        await asyncio.sleep(a.minutes * 60)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        w.row('note', src='main', finished=True)
        print('[lag] done')


if __name__ == '__main__':
    asyncio.run(main())
