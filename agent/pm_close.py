"""
Polymarket's own price at kick-off for a bet already placed: `pm_closing_price`
and `pm_clv`, for the every-game agents (nfl_agent, nba_agent).

Until 2026-11-03 the agents wrote this close in the same UPDATE as the sharp
one, and only when a sharp snapshot under 8 minutes old was at hand and the
bought market still passed the betting gates (spread, liquidity), in the 12
minutes before kick-off: about two tries. 7 of 60 NBA and 16 of 119 NFL bets
never got one, six of the NBA misses on the evening of the 10-30 database
stall. Polymarket's price needs none of that, so it is read on its own, twice:

1. **live**, every cycle inside the close window, from the CLOB book of the
   bought token: the mid of the best bid and ask. The last read before
   kick-off wins (`pm_clv_source = 'clob_book'`).
2. **from history** at settlement when the live read never happened: the last
   1-minute point at or before kick-off from data-api `/v2/prices-history`
   (`'prices_history_1m'`), at most 5 minutes old. 1-minute points are kept for
   at least 7 days. Past that, measured 2026-11-03 on 13 NFL bets from
   09-13 → 10-25, the series came back as 0 or 1 at every bucket up to kick-off:
   the result, not a price. So 0 and 1 are refused, and a bet older than 7 days
   is left without a close rather than given that one.

pm_clv = close / entry − 1, on the bought token (same as the live write).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import psycopg2.extras

import pm_prices

log = logging.getLogger(__name__)

MAX_STALE_S = 300             # a history point more than this before kick-off is not the close
HISTORY_DAYS = 7              # 1-minute retention; older series can come back as the result
SETTLE_LAG_MIN = 10           # kick-off at least this long ago before history is asked


def mid_of(book: dict | None) -> float | None:
    """The mid of a two-sided book, or None. A one-sided book has no mid."""
    if not book or book.get("bid") is None or book.get("ask") is None:
        return None
    bid, ask = float(book["bid"]), float(book["ask"])
    if not 0.0 < bid <= ask < 1.0:
        return None
    return 0.5 * (bid + ask)


def record_live(conn, trade_id: int, entry: float, book: dict | None) -> float | None:
    """Write the live close from the bought token's CLOB book. Overwrites an
    earlier live read: the call is made only before kick-off, so the last one
    is the nearest. Returns the mid written, or None."""
    mid = mid_of(book)
    if mid is None:
        return None
    with conn.cursor() as cur:
        cur.execute("""UPDATE paper_trades SET pm_closing_price = %s, pm_closing_at = now(),
                              pm_clv = %s, pm_clv_source = 'clob_book',
                              pm_closing_bid = %s, pm_closing_ask = %s
                        WHERE id = %s""",
                    (round(mid, 4), round(mid / entry - 1, 5), book["bid"], book["ask"], trade_id))
    return mid


def close_from_history(token_id: str, kickoff_ts: int, session=None) -> tuple[int, float] | None:
    """(point's unix ts, price) for the last 1-minute point at or before
    kick-off, or None when there is none, it is stale, or it is 0 / 1."""
    pts = [p for p in pm_prices.history(token_id, start=kickoff_ts - 1800, end=kickoff_ts,
                                        bucket_s=60, session=session) if p[0] <= kickoff_ts]
    if not pts:
        return None
    t, p = pts[-1]
    if kickoff_ts - t > MAX_STALE_S or not 0.0 < p < 1.0:
        return None
    return t, p


def fill_from_history(conn, strategy_name: str, dry_run: bool = False) -> int:
    """Give every bet of the strategy that kicked off in the last 7 days and
    has no Polymarket close one from price history. Network first, writes
    after: no transaction is held across HTTP (db_txn)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pt.id, pt.pm_token_id, pt.entry_price, pm.resolution_time AS kickoff
              FROM paper_trades pt
              JOIN strategies s ON s.id = pt.strategy_id
              JOIN pm_markets pm ON pm.id = pt.market_id
             WHERE s.name = %s AND pt.pm_closing_price IS NULL
               AND pt.pm_token_id IS NOT NULL AND pt.entry_price IS NOT NULL
               AND pm.resolution_time < now() - make_interval(mins => %s)
               AND pm.resolution_time > now() - make_interval(days => %s)""",
                    (strategy_name, SETTLE_LAG_MIN, HISTORY_DAYS))
        todo = [dict(r) for r in cur.fetchall()]
    found = []
    for t in todo:
        hit = close_from_history(t["pm_token_id"], int(t["kickoff"].timestamp()))
        if hit is None:
            log.info(f"  no history close for pt#{t['id']} (kick-off {t['kickoff']:%m-%d %H:%M}Z)")
            continue
        found.append((t, hit))
    if dry_run:
        for t, (ts, p) in found:
            log.info(f"  DRY close pt#{t['id']}: {p:.3f} at "
                     f"{datetime.fromtimestamp(ts, timezone.utc):%m-%d %H:%M}Z")
        return len(found)
    for t, (ts, p) in found:
        entry = float(t["entry_price"])
        with conn.cursor() as cur:
            cur.execute("""UPDATE paper_trades SET pm_closing_price = %s, pm_closing_at = %s,
                                  pm_clv = %s, pm_clv_source = 'prices_history_1m'
                            WHERE id = %s AND pm_closing_price IS NULL""",
                        (round(p, 4), datetime.fromtimestamp(ts, timezone.utc),
                         round(p / entry - 1, 5), t["id"]))
        log.info(f"  history close pt#{t['id']}: PM {p:.3f} vs entry {entry:.3f} → "
                 f"pm_clv {p / entry - 1:+.2%}")
    return len(found)
