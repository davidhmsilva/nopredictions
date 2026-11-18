"""
Polymarket trades on a market, from data-api `/v2/trades`.

Data API v1 retired on 2026-11-02: `GET /trades` answers 410 Gone
("Data API v1 has been retired"). The last v1 read nba_maker made was at
2026-11-02 22:24Z. Every reader of a market's trades goes through here so the
differences are handled once:

- the market is `condition` (v1's `market` is now a 400), and one request
  may name up to 20 condition ids; this helper sends one;
- `start` and `end` are IGNORED on the market shape: it serves a fixed
  three-year window, newest first. So the window is applied here: the walk
  stops at the first page that reaches back past `start`, and rows outside
  [start, end] are dropped;
- pages follow `pagination.next_cursor` until it is null (no `offset`, so
  v1's 10,000-row ceiling is gone), re-sending the same filters each page;
- `taker_only` defaults to true, each fill served once on its taker side,
  as v1's `takerOnly=true` did;
- rows are snake_case and the token is `token_id` (v1's `asset`);
- `filter_amount` defaults to 0.01 shares; 0 is sent, which the API treats
  the same, so nothing is dropped on our side.

Measured 2026-11-03 on nba-okc-cle-2026-11-02 (spread): 456 trades in one
page, newest first, back to the market's first print the day before tip-off.
"""

from __future__ import annotations

import time

import requests

TRADES = "https://data-api.polymarket.com/v2/trades"
PAGE = 1000            # the API's maximum first-page size
MAX_PAGES = 200


def _row(x: dict) -> dict | None:
    try:
        return {"timestamp": int(x["timestamp"]), "price": float(x["price"]),
                "size": float(x.get("size") or 0), "side": x.get("side"),
                "token_id": str(x["token_id"]), "condition_id": x.get("condition_id"),
                "outcome_index": x.get("outcome_index"), "proxy_wallet": x.get("proxy_wallet"),
                "transaction_hash": x.get("transaction_hash")}
    except (KeyError, TypeError, ValueError):
        return None


def trades(condition_id: str, start: int | None = None, end: int | None = None,
           taker_only: bool = True, timeout: float = 20, session=None,
           pause: float = 0.2) -> list[dict] | None:
    """Every trade on the market with `start` <= timestamp <= `end` (both
    inclusive, epoch seconds, either may be None), oldest first. None when the
    API did not answer, or answered with something that is not a page: a
    caller must not read a failed request as "no trades"."""
    get = (session or requests).get
    params = {"condition": condition_id, "taker_only": "true" if taker_only else "false",
              "filter_amount": 0, "limit": PAGE}
    out: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        q = dict(params, cursor=cursor) if cursor else params
        try:
            r = get(TRADES, params=q, timeout=timeout)
            r.raise_for_status()
            body = r.json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            return None
        rows = [y for y in (_row(x) for x in body["data"]) if y is not None]
        out += rows
        cursor = (body.get("pagination") or {}).get("next_cursor")
        if not cursor or (start is not None and rows and min(y["timestamp"] for y in rows) < start):
            break
        if pause:
            time.sleep(pause)
    else:
        return None                          # never reached `start`: an incomplete window
    out = [y for y in out if (start is None or y["timestamp"] >= start)
           and (end is None or y["timestamp"] <= end)]
    out.sort(key=lambda y: y["timestamp"])
    return out
