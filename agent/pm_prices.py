"""
Polymarket price history, from data-api `/v2/prices-history`.

The CLOB's `/prices-history` shuts down on 2026-10-30 00:00 UTC
(@PolymarketDevs, 2026-10-25). Every caller goes through here so the
differences are handled once:

- one token per request (`token_id`, never the market), oldest first, paged
  by `next_cursor`;
- `end` is EXCLUSIVE where the old `endTs` was inclusive, so `history()` adds
  a second;
- a bucket's `price` is the price AT its `timestamp` (the bucket start):
  measured 2026-10-26 on a 30-minute series, it equalled the bucket's first
  1-minute point 10/10 times and its last only 6/10. So "the last point at or
  before kick-off" carries no lookahead, whatever the width;
- retention is the catch: 1-minute points for >= 7 days, 5-minute >= 60,
  30-minute >= 90, then only 3h / 12h buckets. `price_as_of()` returns the
  point's own timestamp, and a caller that needs a fresh price must check it.
  The old endpoint's minute history up to the shutdown is in
  `ingest/data/price_paths*` (Stage M and `stage_m_archive.py`).
"""

from __future__ import annotations

import requests

PRICES_HISTORY = "https://data-api.polymarket.com/v2/prices-history"


def _points(body) -> list[tuple[int, float]]:
    out = []
    for pt in (body or {}).get("data") or []:
        t, p = pt.get("timestamp"), pt.get("price")
        if t is None or p is None:
            continue
        try:
            out.append((int(t), float(p)))
        except (TypeError, ValueError):
            continue
    return out


def history(token_id: str, start: int | None = None, end: int | None = None,
            bucket_s: int | None = None, interval: str | None = None,
            timeout: float = 20, session=None) -> list[tuple[int, float]]:
    """[(unix_ts, price)] oldest first, `end` inclusive. Pass a window
    (`start`, optional `end`) or an `interval` ('max', 'all', '1m', '1w',
    '1d', '6h', '1h'), not both. `bucket_s` 60-86400, server's choice if None.
    Returns [] on any failure: callers treat a missing path as missing."""
    get = (session or requests).get
    params: dict = {"token_id": token_id}
    if start is not None:
        params["start"] = int(start)
        if end is not None:
            params["end"] = int(end) + 1
    elif interval:
        params["interval"] = interval
    if bucket_s:
        params["bucket_seconds"] = int(bucket_s)
    out: list[tuple[int, float]] = []
    cursor = None
    for _ in range(100):
        q = {"token_id": token_id, "cursor": cursor} if cursor else params
        try:
            r = get(PRICES_HISTORY, params=q, timeout=timeout)
            if not r.ok:
                return out
            body = r.json()
        except (requests.RequestException, ValueError):
            return out
        out += _points(body)
        cursor = ((body or {}).get("pagination") or {}).get("next_cursor")
        if not cursor:
            break
    # The window's terminal point is stamped at the window edge: asked for
    # end=KO (sent as KO+1), it came back at KO+1. Clip it to stay inclusive.
    if start is not None and end is not None:
        out = [x for x in out if x[0] <= end]
    out.sort()
    return out


def price_as_of(token_id: str, ts: int, timeout: float = 20,
                session=None) -> tuple[int, float] | None:
    """The latest observation at or before `ts`, as (its own timestamp,
    price). On an old market that timestamp can be hours before `ts`."""
    get = (session or requests).get
    try:
        r = get(PRICES_HISTORY, params={"token_id": token_id, "as_of": int(ts)}, timeout=timeout)
        if not r.ok:
            return None
        pts = _points(r.json())
    except (requests.RequestException, ValueError):
        return None
    pts = [x for x in pts if x[0] <= ts]
    return max(pts) if pts else None
