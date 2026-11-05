"""
Polymarket Data API v2 reads other than a market's trades (those are
`pm_trades.trades`): the cursor-paged wallet routes and the wallet's value.

Data API v1 retired on 2026-11-02: every v1 route answers 410 Gone ("Data API
v1 has been retired"). What changed for the routes read here, measured
2026-11-05 against the live API:

- every body is an envelope: `{"data": ..., "pagination": {...}}`. A
  documented miss is `data: null` or `[]`, never an error;
- paging is by `pagination.next_cursor` until it is null. `offset` is a 400
  (so v1's 5,000-row `/activity` and 10,000-row `/trades` ceilings are gone),
  and the same filters are re-sent on every page: on the feeds the cursor
  carries only its seek anchor, and changing a filter re-anchors the walk;
- `limit` runs 0..1000 on `/v2/activity` (anything above is a 400);
- rows are snake_case: `token_id` (v1 `asset`), `condition_id`,
  `outcome_index` (999 = could not be labelled), `usdc_size`,
  `transaction_hash`, `event_slug`;
- `/v2/positions` with no `status` serves what the wallet still holds, as v1's
  `/positions` did (229 of 229 rows REDEEMABLE on the old trading wallet);
- `/v2/activity` REDEEM rows carry the `token_id` and come one per outcome
  redeemed (the losing leg with `usdc_size` 0); MERGE, SPLIT and CONVERSION rows
  carry `token_id` "" and `outcome_index` 999, one per condition;
- `/v2/value` answers `{"data": {"proxy_wallet", "value"}}`, not v1's list.

A failed read is None, never an empty list: a caller that reads "no rows" from
a refused request books a wallet with no history, or a position as closed.
"""

from __future__ import annotations

import time

import requests

BASE = "https://data-api.polymarket.com/v2"
RETRY = (429, 500, 502, 503, 504)
MAX_PAGES = 5000


def page(url: str, params: dict, session=None, timeout: float = 30, tries: int = 4,
         backoff: float = 0.5) -> dict | None:
    """One v2 response body, retrying throttles and server errors. None when it
    never answered 200 with a JSON object."""
    get = (session or requests).get
    for k in range(tries):
        try:
            r = get(url, params=params, timeout=timeout)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code == 200:
            try:
                body = r.json()
            except ValueError:
                return None
            return body if isinstance(body, dict) else None
        if r is not None and r.status_code not in RETRY:
            return None                      # a 400 or a 410 does not get better by asking again
        if k < tries - 1 and backoff:
            time.sleep(backoff * 2 ** k)
    return None


def walk(route: str, params: dict, session=None, limit: int = 500, max_rows: int | None = None,
         timeout: float = 30, tries: int = 4, backoff: float = 0.5,
         log=None) -> tuple[list[dict], bool] | None:
    """Every row of a cursor-paged v2 route, in the order served, as
    (rows, complete). `complete` is False only when `max_rows` stopped the walk
    before the cursor ran out, and the caller must say so. None when any page
    failed: a partial walk is not handed back as if it were the whole."""
    url = f"{BASE}/{route.lstrip('/')}"
    out: list[dict] = []
    cursor, seen = None, set()
    for n in range(MAX_PAGES):
        q = dict(params, limit=limit)
        if cursor:
            q["cursor"] = cursor
        body = page(url, q, session=session, timeout=timeout, tries=tries, backoff=backoff)
        if body is None:
            return None
        data = body.get("data")
        if data is None:
            data = []                        # the documented miss
        if not isinstance(data, list):
            return None
        out += [x for x in data if isinstance(x, dict)]
        cursor = (body.get("pagination") or {}).get("next_cursor")
        if log:
            log(f"{route} page {n + 1}: {len(data)} rows, total {len(out)}")
        if not cursor:
            return out, True
        if cursor in seen:
            return None                      # a cursor that repeats would walk forever
        seen.add(cursor)
        if max_rows is not None and len(out) >= max_rows:
            return out, False
    return out, False


def value(user: str, session=None, timeout: float = 30) -> float | None:
    """The wallet's current position value in USDC, or None when unavailable."""
    body = page(f"{BASE}/value", {"user": user}, session=session, timeout=timeout)
    d = (body or {}).get("data")
    if not isinstance(d, dict) or d.get("value") is None:
        return None
    try:
        return float(d["value"])
    except (TypeError, ValueError):
        return None
