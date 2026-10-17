"""
The CLOB book for Pre-Match Drift: what buying NO costs and what selling it pays.

Pre-Match Drift holds the NO side of a longshot. Until db/076 it priced both
legs off the YES side and got both backwards: it bought at 1 − YES ask (the NO
BID) and sold at 1 − YES bid (the NO ASK), so every round trip banked the spread
as profit. This module is the one place the executable price is read:

  buying NO  pays the NO ask   (= 1 − YES bid on a mirrored book)
  selling NO receives the NO bid (= 1 − YES ask)

Both tokens are fetched in one POST /books. The NO token's own book is used
when its id is known; for a position that only stored the YES token, the NO
book is the YES book mirrored, which is how Polymarket's matching engine quotes
the pair (checked on Middlesbrough v Cardiff draw, 2026-10-16: YES 0.22/0.35,
NO 0.65/0.78).

⚠️ /book lists bids ascending and asks descending, so the best level is the
LAST one. Never read index 0; this module sorts.
"""

from __future__ import annotations

import requests

CLOB = "https://clob.polymarket.com"

Levels = list[tuple[float, float]]   # (price, shares), best first


def _levels(raw, best_high: bool) -> Levels:
    out = []
    for lv in raw or []:
        try:
            p, s = float(lv["price"]), float(lv["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < p < 1 and s > 0:
            out.append((p, s))
    return sorted(out, key=lambda x: -x[0] if best_high else x[0])


def fetch_books(token_ids: list[str], timeout: float = 15) -> dict[str, dict]:
    """{token_id: {"bids": best-first, "asks": best-first}} for every token the
    CLOB answered. A token missing from the dict was not read — never treat
    that as an empty book."""
    out: dict[str, dict] = {}
    toks = [t for t in dict.fromkeys(token_ids) if t]
    for i in range(0, len(toks), 400):
        chunk = toks[i:i + 400]
        r = requests.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk], timeout=timeout)
        r.raise_for_status()
        for b in r.json() or []:
            out[str(b.get("asset_id"))] = {
                "bids": _levels(b.get("bids"), best_high=True),
                "asks": _levels(b.get("asks"), best_high=False),
            }
    return out


def mirror(book: dict) -> dict:
    """The complementary token's book: its bids are 1 − this token's asks."""
    return {
        "bids": [(round(1 - p, 4), s) for p, s in book["asks"]],
        "asks": [(round(1 - p, 4), s) for p, s in book["bids"]],
    }


def no_book(books: dict[str, dict], yes_token: str, no_token: str | None) -> dict | None:
    """The NO token's book: its own if read, else the YES book mirrored."""
    if no_token and no_token in books:
        return books[no_token]
    if yes_token in books:
        return mirror(books[yes_token])
    return None


def top(book: dict | None) -> dict:
    """Best bid / ask and the size resting at each. None where a side is empty."""
    bids = (book or {}).get("bids") or []
    asks = (book or {}).get("asks") or []
    return {
        "bid": bids[0][0] if bids else None, "bid_size": bids[0][1] if bids else None,
        "ask": asks[0][0] if asks else None, "ask_size": asks[0][1] if asks else None,
    }


def buy_fill(asks: Levels, usd: float) -> tuple[float, float] | None:
    """Spend `usd` walking the ask ladder: (average price paid, shares bought),
    or None if the ladder cannot absorb the whole stake."""
    left, shares = usd, 0.0
    for p, s in asks:
        take = min(left, p * s)
        shares += take / p
        left -= take
        if left <= 1e-9:
            return round(usd / shares, 4), round(shares, 4)
    return None


def sell_fill(bids: Levels, shares: float) -> float | None:
    """Sell `shares` walking the bid ladder: average price received, or None if
    the ladder cannot absorb them all."""
    left, cash = shares, 0.0
    for p, s in bids:
        take = min(left, s)
        cash += take * p
        left -= take
        if left <= 1e-9:
            return round(cash / shares, 4)
    return None
