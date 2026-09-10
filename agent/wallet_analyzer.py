"""
Polymarket wallet analyser — what a wallet actually does, and whether it works.

Reconstructs the entire trading life of a wallet from Polymarket's public
activity feed, matches it FIFO into round trips, and reports both the numbers
and a written reading of the strategy: what it buys, when it buys it, where the
money comes from, and how the behaviour changed over time.

Written after the GSX- study (reports/wallet_gsx_2026-09-02.md), which was done
by hand and survived only as markdown. This is that method, codified.

    python wallet_analyzer.py 0xec5723df1ef786d95b05b2941c89b45dcb560fa7
    python wallet_analyzer.py <addr> --report          # write reports/wallet_<name>_<date>.md
    python wallet_analyzer.py <addr> --json out.json   # machine-readable profile
    python wallet_analyzer.py <addr> --since 2026-07-01
    python wallet_analyzer.py <addr> --verify-site https://nopredictions.com

THREE TRAPS THIS FILE EXISTS TO AVOID
-------------------------------------
1. `/activity?offset=` refuses anything past **5000** — a naive pager returns
   5,000 rows and looks complete. GSX- has 16,157. We page by time cursor and
   assert the walk terminated on a short page, never on a cap.

2. REDEEM rows carry **no `asset`** — only conditionId + outcomeIndex. Attribute
   them by outcome index against the market's `clobTokenIds` or the redemption
   lands on the wrong token and the P&L splits between two outcomes.

3. Activity types we do not model (MERGE / SPLIT / CONVERSION) silently break
   the reconstruction for wallets that exit that way — and per
   [[wallet-swisstony]] some big ones do. Every unmodelled row is counted, the
   count is reported, and the leaderboard reconciliation is the check that says
   whether it mattered.

WHY GAMMA AND NOT THE CLOB
--------------------------
`clob/markets/<condition_id>` is one round trip per market; on a 1,214-market
wallet that is ~30s and 712 rate-limit refusals. Gamma takes 100 condition_ids
per call — the same 1,214 markets in **2.2s**. Verified against the CLOB on
1,114 markets: gameStartTime identical on every one, winner identical on
1,108/1,108. ⚠️ The Gamma trap is `closed`: the default query returns ZERO rows
for a settled market. Both states have to be swept.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import requests

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
LB_API = "https://lb-api.polymarket.com"

PAGE = 500                # activity rows per request (server max)
GAMMA_BATCH = 100         # condition_ids per Gamma call (200 → HTTP 422)
MAX_OFFSET = 5000         # the server's hard cap; we never rely on it
# A market maker can run to hundreds of thousands of fills. Stopping is fine;
# stopping QUIETLY is not — `complete` goes false and the narrative leads with it.
MAX_ROWS = 250_000

# Minutes after the listed kick-off. A football match runs ~105 minutes with
# stoppage, so 110-130 is the window where the result is known and the board
# has not resolved — the window that carried half of GSX-'s profit.
WIN_IN_MATCH = (0, 110)
WIN_WHISTLE = (110, 130)

ENTRY_BANDS = [
    (0.00, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.40), (0.40, 0.60),
    (0.60, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 1.01),
]

MOVE_BANDS = [
    ("loss < -0.05", -9.0, -0.05),
    ("flat -0.05..+0.02", -0.05, 0.02),
    ("+0.02..+0.10", 0.02, 0.10),
    ("+0.10..+0.30", 0.10, 0.30),
    ("> +0.30", 0.30, 9.0),
]


# ─── tiny deterministic PRNG ────────────────────────────────────────────────
# The bootstrap has to give the same interval in Python and in the site's
# TypeScript, or --verify-site reports a disagreement that is only the RNG.
# mulberry32: 32-bit, trivially portable, good enough for resampling.

class Mulberry32:
    def __init__(self, seed: int = 0x9E3779B9):
        self.s = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.s = (self.s + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.s
        t = (t ^ (t >> 15)) * (t | 1) & 0xFFFFFFFF
        t ^= (t + ((t ^ (t >> 7)) * (t | 61) & 0xFFFFFFFF)) & 0xFFFFFFFF
        t &= 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0


# ─── data model ─────────────────────────────────────────────────────────────

@dataclass
class Lot:
    """One round trip: shares bought at a price, and what became of them."""
    asset: str
    condition_id: str
    event_slug: str
    title: str
    outcome: str
    shares: float
    entry_price: float
    entry_ts: int
    exit_price: float
    exit_ts: Optional[int]
    exit_kind: str            # sell | redeem | resolved | open | unknown
    entry_minute: Optional[float] = None   # minutes after listed kick-off

    @property
    def cost(self) -> float:
        return self.shares * self.entry_price

    @property
    def proceeds(self) -> float:
        return self.shares * self.exit_price

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost

    @property
    def hold_s(self) -> Optional[int]:
        return None if self.exit_ts is None else max(0, self.exit_ts - self.entry_ts)


# ─── fetch layer ────────────────────────────────────────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "nopredictions-wallet-analyzer/1"
    return s


def _get(s: requests.Session, url: str, params: Any = None, tries: int = 4) -> Any:
    for i in range(tries):
        try:
            r = s.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 502, 503, 504):
                time.sleep(0.4 * (i + 1))
                continue
            r.raise_for_status()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(0.4 * (i + 1))
    return None


def _row_key(r: dict) -> tuple:
    return (r.get("timestamp"), r.get("transactionHash"), r.get("asset"), r.get("type"),
            r.get("side"), r.get("size"), r.get("usdcSize"), r.get("price"),
            r.get("conditionId"), r.get("outcomeIndex"))


def fetch_activity(wallet: str, since: Optional[int] = None, until: Optional[int] = None,
                   session: Optional[requests.Session] = None,
                   log=lambda m: None) -> tuple[list[dict], bool]:
    """Every activity row for `wallet`, newest first, walked by time cursor.

    Returns (rows, complete). `complete` is False only when the walk stopped on
    a guard rather than on a short page — the caller must say so out loud,
    because a truncated history reads exactly like a smaller wallet.
    """
    s = session or _session()
    seen: dict[tuple, dict] = {}
    cursor = until
    complete = False
    for page_no in range(MAX_ROWS // PAGE + 1):
        params: dict[str, Any] = {"user": wallet, "limit": PAGE}
        if cursor is not None:
            params["end"] = cursor
        if since is not None:
            params["start"] = since
        batch = _get(s, f"{DATA_API}/activity", params) or []
        if not batch:
            complete = True
            break
        for row in batch:
            seen.setdefault(_row_key(row), row)
        stamps = [b["timestamp"] for b in batch]
        lo, hi = min(stamps), max(stamps)
        log(f"page {page_no + 1}: {len(batch)} rows, {hi} → {lo}, total {len(seen)}")
        if len(batch) < PAGE:
            complete = True
            break
        if lo == hi:
            # A single second saturated the page. Drain it by offset before
            # stepping past, or the cursor can never advance.
            for off in range(PAGE, MAX_OFFSET + 1, PAGE):
                extra = _get(s, f"{DATA_API}/activity",
                             {"user": wallet, "limit": PAGE, "start": lo, "end": lo, "offset": off}) or []
                for row in extra:
                    seen.setdefault(_row_key(row), row)
                if len(extra) < PAGE:
                    break
            cursor = lo - 1
        else:
            cursor = lo
        if since is not None and cursor < since:
            complete = True
            break
        if len(seen) >= MAX_ROWS:
            break
    rows = sorted(seen.values(), key=lambda r: (r["timestamp"], r.get("transactionHash") or ""))
    return rows, complete


def fetch_markets(condition_ids: Iterable[str],
                  session: Optional[requests.Session] = None,
                  log=lambda m: None) -> dict[str, dict]:
    """Gamma metadata for every condition, both closed states.

    ⚠️ Without `closed=true` Gamma returns nothing at all for a settled market —
    the silent-empty trap that has cost this project a day more than once.
    """
    s = session or _session()
    cids = sorted({c for c in condition_ids if c})
    out: dict[str, dict] = {}
    for i in range(0, len(cids), GAMMA_BATCH):
        batch = cids[i:i + GAMMA_BATCH]
        qs = "&".join(f"condition_ids={c}" for c in batch)
        for closed in ("true", "false"):
            data = _get(s, f"{GAMMA_API}/markets?{qs}&closed={closed}&limit=500") or []
            for m in data:
                out[m["conditionId"]] = m
        log(f"markets {min(i + GAMMA_BATCH, len(cids))}/{len(cids)}")
    return out


def fetch_lb_profit(wallet: str, session: Optional[requests.Session] = None) -> Optional[float]:
    """Polymarket's own all-time profit for the wallet — the reconciliation anchor.

    ⚠️ Only `window=all` reconciles. The windowed figures are broken for at
    least one wallet we checked (30d volume < 7d volume), so they are not used.
    """
    s = session or _session()
    try:
        data = _get(s, f"{LB_API}/profit", {"window": "all", "limit": 1, "address": wallet})
        if isinstance(data, list) and data:
            return float(data[0].get("amount"))
    except Exception:
        pass
    return None


SOCCER_TAG = "100350"


def fetch_sports(session: Optional[requests.Session] = None) -> dict[str, dict]:
    """Gamma's league list, keyed by the code event tickers lead with.

    The ticker head is the only competition id a market carries, and a
    hand-kept table of them goes stale: king1605 read 37% "unknown sport" on a
    book that is all football (rus, swe, aut, kor…), and `col` — once mapped to
    Colombia — is the Conference League. `/sports` names all ~465 leagues and
    tags the soccer ones. Series ids are NOT used: they change by season, and 6
    of king1605's Russian markets carry one `/sports` does not list.
    """
    s = session or _session()
    out: dict[str, dict] = {}
    try:
        for row in _get(s, f"{GAMMA_API}/sports") or []:
            code = str(row.get("sport") or "").lower()
            if code:
                tags = str(row.get("tags") or "").split(",")
                out[code] = {"name": row.get("name") or code.upper(), "football": SOCCER_TAG in tags}
    except Exception:
        pass
    return out


def fetch_current_value(wallet: str, session: Optional[requests.Session] = None) -> Optional[float]:
    s = session or _session()
    try:
        data = _get(s, f"{DATA_API}/value", {"user": wallet})
        if isinstance(data, list) and data:
            return float(data[0].get("value"))
    except Exception:
        pass
    return None


# ─── market metadata helpers ────────────────────────────────────────────────

def _json_list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except Exception:
            return []
    return []


def _kickoff_ts(m: dict) -> Optional[int]:
    raw = (m or {}).get("gameStartTime") or (m or {}).get("startDate") or ""
    if not raw:
        return None
    txt = str(raw).strip().replace(" ", "T")
    if txt.endswith("+00"):
        txt = txt[:-3] + "+00:00"
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(txt).timestamp())
    except Exception:
        return None


def _resolution(m: dict) -> Optional[dict[str, float]]:
    """outcome → settlement value, or None while the market is unresolved."""
    if not m or not m.get("closed"):
        return None
    prices = [float(p) for p in _json_list(m.get("outcomePrices")) if p not in (None, "")]
    outcomes = [str(o) for o in _json_list(m.get("outcomes"))]
    if not prices or len(prices) != len(outcomes):
        return None
    if not any(p > 0.99 for p in prices):     # 50-50 void, or not actually settled
        return {o: p for o, p in zip(outcomes, prices)}
    return {o: (1.0 if p > 0.99 else 0.0) for o, p in zip(outcomes, prices)}


def _mark(m: dict, outcome: str) -> Optional[float]:
    """Last traded price for an open market — a mark, never a fill."""
    prices = [float(p) for p in _json_list((m or {}).get("outcomePrices")) if p not in (None, "")]
    outcomes = [str(o) for o in _json_list((m or {}).get("outcomes"))]
    for o, p in zip(outcomes, prices):
        if o == outcome:
            return p
    return None


# Polymarket's event tickers lead with a competition code. There is no endpoint
# that expands them, so the common ones are named here and anything unknown
# falls back to the raw code rather than being silently pooled into "other".
COMPETITIONS = {
    "fifwc": "FIFA World Cup", "fif": "Internationals", "wcq": "WC qualifiers",
    "epl": "Premier League", "esp": "La Liga", "ita": "Serie A", "bun": "Bundesliga",
    "fra": "Ligue 1", "ned": "Eredivisie", "por": "Primeira Liga", "efl": "Championship",
    "ucl": "Champions League", "uel": "Europa League", "uecl": "Conference League",
    "bra": "Brasileirão", "bra2": "Brasileirão B", "cdb": "Copa do Brasil",
    "arg": "Argentina Primera", "mls": "MLS", "lmx": "Liga MX", "col": "Conference League",
    "chi": "Chile", "lib": "Libertadores", "sud": "Sudamericana",
    "lc": "Leagues Cup", "lec": "Leagues Cup", "col1": "Colombia Primera A",
    "csl": "Chinese Super League", "egy": "Egypt", "tur": "Süper Lig",
    "sco": "Scottish Premiership", "bel": "Belgian Pro League", "nba": "NBA",
    "nfl": "NFL", "mlb": "MLB", "nhl": "NHL",
}

# The 110-130' whistle window is a FOOTBALL fact. 110 minutes after a tennis
# match starts is the middle of it, not the end, and a wallet that trades ATP
# would be handed a "post-whistle settlement buyer" verdict off nothing.
NON_FOOTBALL = {"NBA", "NFL", "MLB", "NHL", "ATP", "WTA", "ITF", "CS", "LOL",
                "DOTA", "VAL", "UFC", "F1", "NCAAF", "NCAAB", "GOLF", "PGA"}
FOOTBALL = {v for v in COMPETITIONS.values() if v not in NON_FOOTBALL}


def _is_football(label: str) -> Optional[bool]:
    """True / False / None — "we do not know" is a distinct answer, and the
    football-specific windows are withheld for it rather than assumed."""
    if label in NON_FOOTBALL:
        return False
    if label in FOOTBALL:
        return True
    return None


def _ticker_head(m: dict) -> str:
    ev = (m or {}).get("events") or []
    ticker = (ev[0].get("ticker") if ev else "") or (m or {}).get("slug") or ""
    return str(ticker).split("-")[0].lower()


def _competition(m: dict, sports: Optional[dict] = None) -> str:
    head = _ticker_head(m)
    if not head:
        return "unknown"
    if head in COMPETITIONS:
        return COMPETITIONS[head]
    if sports and head in sports:
        return sports[head]["name"]
    return head.upper()


def _football(m: dict, sports: Optional[dict] = None) -> Optional[bool]:
    """Gamma's own league list decides first; the hand-kept table only answers
    for codes that list does not carry."""
    head = _ticker_head(m)
    if sports and head in sports:
        return sports[head]["football"]
    return _is_football(_competition(m, sports))


# ─── the reconstruction ─────────────────────────────────────────────────────

def build_lots(rows: list[dict], markets: dict[str, dict]) -> tuple[list[Lot], dict]:
    """FIFO-match every fill into round trips.

    Buys queue per token. Sells and redemptions consume the queue oldest-first.
    Whatever is left at the end is valued at the market's settlement if it
    resolved, at its last trade if it is still open, and flagged either way —
    an unsold lot is a mark, not money.
    """
    queues: dict[str, deque[list]] = defaultdict(deque)   # asset → [shares, price, ts, cid, ...]
    lots: list[Lot] = []
    meta: dict[str, dict] = {}
    unhandled: Counter = Counter()
    rebates = rewards = fees = 0.0
    sell_proceeds = redeem_proceeds = merge_proceeds = deployed = 0.0
    buys = sells = redeems = merges = 0

    # (conditionId, outcomeIndex) → token id, so a REDEEM row (which carries no
    # asset) lands on the token it actually redeemed.
    token_of: dict[tuple[str, int], str] = {}
    for cid, m in markets.items():
        for idx, tok in enumerate(_json_list(m.get("clobTokenIds"))):
            token_of[(cid, idx)] = str(tok)

    def remember(asset: str, row: dict) -> None:
        if asset not in meta:
            meta[asset] = {
                "condition_id": row.get("conditionId") or "",
                "event_slug": row.get("eventSlug") or "",
                "title": row.get("title") or "",
                "outcome": row.get("outcome") or "",
            }

    for row in rows:
        kind = row.get("type")
        usd = float(row.get("usdcSize") or 0.0)
        size = float(row.get("size") or 0.0)
        ts = int(row.get("timestamp") or 0)

        if kind == "TRADE":
            asset = row.get("asset") or ""
            if not asset or size <= 0:
                continue
            remember(asset, row)
            # ⚠️ NOT row["price"] — that is the execution price BEFORE the fee.
            # The money moved is usdcSize, fee included, and P&L is measured on
            # the money. Their difference IS the fee: per fill it sits at 0
            # (maker) or at a rate × p·(1−p) per share (taker, mostly 0.05) and
            # is never negative — measured on king1605's 729 fills and GSX-'s
            # 15,810, 2026-09-10.
            price = usd / size
            px = float(row.get("price") or 0.0)
            if px > 0:
                fees += (usd - px * size) if row.get("side") == "BUY" else (px * size - usd)
            if row.get("side") == "BUY":
                buys += 1
                deployed += usd
                queues[asset].append([size, price, ts])
            else:
                sells += 1
                sell_proceeds += usd
                _consume(queues[asset], size, price, ts, "sell", asset, meta, lots)

        elif kind == "REDEEM":
            cid = row.get("conditionId") or ""
            idx = row.get("outcomeIndex")
            asset = token_of.get((cid, int(idx))) if idx is not None else None
            if not asset:
                # No token map (market metadata missing) — fall back to the only
                # token we ever traded on this condition, and give up if unclear.
                cands = [a for a, mm in meta.items() if mm["condition_id"] == cid]
                asset = cands[0] if len(cands) == 1 else None
            if not asset:
                unhandled["REDEEM (unmapped)"] += 1
                continue
            if asset not in meta:
                meta[asset] = {"condition_id": cid, "event_slug": row.get("eventSlug") or "",
                               "title": row.get("title") or "", "outcome": row.get("outcome") or ""}
            redeems += 1
            redeem_proceeds += usd
            price = (usd / size) if size else 1.0
            _consume(queues[asset], size, price, ts, "redeem", asset, meta, lots)

        elif kind == "MERGE":
            # A merge hands back N shares of EVERY outcome and receives N USDC.
            # It is a real exit, and a wallet that uses it (RN1: 2,845 merges)
            # otherwise shows those positions rotting to zero — we measured
            # −$14.2M of "expired worthless" that was nothing of the sort.
            #
            # The $1 a merged bundle returns is split across the legs in
            # proportion to what each leg COST. Total P&L is 1 − Σentry however
            # it is split; only the per-band attribution depends on this
            # convention, and cost-proportional is the one that leaves a
            # break-even bundle break-even on every leg.
            cid = row.get("conditionId") or ""
            legs = [token_of.get((cid, i)) for i in
                    range(len(_json_list((markets.get(cid) or {}).get("clobTokenIds"))))]
            legs = [a for a in legs if a]
            if not legs or size <= 0:
                unhandled["MERGE (no token map)"] += 1
                continue
            peeks = [_peek_cost(queues[a], size) for a in legs]
            total = sum(peeks)
            merges += 1
            merge_proceeds += usd
            for asset, peek in zip(legs, peeks):
                if asset not in meta:
                    meta[asset] = {"condition_id": cid, "event_slug": row.get("eventSlug") or "",
                                   "title": row.get("title") or "", "outcome": ""}
                share = (peek / total) if total else (1.0 / len(legs))
                _consume(queues[asset], size, (usd * share) / size, ts, "merge", asset, meta, lots)

        elif kind in ("MAKER_REBATE", "TAKER_REBATE"):
            rebates += usd
        elif kind == "REWARD":
            rewards += usd
        else:
            unhandled[str(kind)] += 1

    # Whatever never left.
    open_value = 0.0
    for asset, q in queues.items():
        if not q:
            continue
        mm = meta.get(asset, {})
        m = markets.get(mm.get("condition_id", ""), {})
        res = _resolution(m)
        outcome = mm.get("outcome", "")
        if res is not None:
            value, kindtag = res.get(outcome, 0.0), "resolved"
        else:
            mk = _mark(m, outcome)
            value, kindtag = (mk if mk is not None else 0.0), "open"
        for shares, price, ts in q:
            open_value += shares * value
            lots.append(Lot(asset=asset, condition_id=mm.get("condition_id", ""),
                            event_slug=mm.get("event_slug", ""), title=mm.get("title", ""),
                            outcome=outcome, shares=shares, entry_price=price, entry_ts=ts,
                            exit_price=value, exit_ts=None, exit_kind=kindtag))

    # Minutes relative to the listed kick-off.
    for lot in lots:
        ko = _kickoff_ts(markets.get(lot.condition_id, {}))
        if ko:
            lot.entry_minute = (lot.entry_ts - ko) / 60.0

    flows = {
        "buys": buys, "sells": sells, "redeems": redeems, "merges": merges,
        "merge_proceeds": merge_proceeds,
        "deployed": deployed, "sell_proceeds": sell_proceeds,
        "redeem_proceeds": redeem_proceeds, "open_value": open_value,
        "rebates": rebates, "rewards": rewards, "fees": fees,
        "unhandled": dict(unhandled),
    }
    return lots, flows


def _peek_cost(q: deque, shares: float) -> float:
    """What the first `shares` in the queue cost, without consuming them."""
    left, total = shares, 0.0
    for lot_shares, lot_price, _ in q:
        take = min(left, lot_shares)
        total += take * lot_price
        left -= take
        if left <= 1e-9:
            break
    return total


def _consume(q: deque, shares: float, price: float, ts: int, kind: str,
             asset: str, meta: dict, lots: list[Lot]) -> None:
    """Retire `shares` from the FIFO queue at `price`."""
    left = shares
    mm = meta.get(asset, {})
    while left > 1e-9 and q:
        lot_shares, lot_price, lot_ts = q[0]
        take = min(left, lot_shares)
        lots.append(Lot(asset=asset, condition_id=mm.get("condition_id", ""),
                        event_slug=mm.get("event_slug", ""), title=mm.get("title", ""),
                        outcome=mm.get("outcome", ""), shares=take, entry_price=lot_price,
                        entry_ts=lot_ts, exit_price=price, exit_ts=ts, exit_kind=kind))
        left -= take
        if take >= lot_shares - 1e-9:
            q.popleft()
        else:
            q[0][0] = lot_shares - take
    if left > 1e-6:
        # Sold more than we ever saw bought. On GSX- all 71 such markets are
        # `negRisk`: the neg-risk adapter mints shares by conversion and those
        # rows are NOT in the activity feed (`type=CONVERSION` returns empty).
        #
        # 🔑 Booked FLAT — entry price = exit price — and never at zero cost.
        # Zero cost turns every share that arrived off-feed into pure profit,
        # which is an error that can only ever run one way. That is the shape
        # of the bug that paid a losing Over 1.5 at 4.35 (db/038). The volume
        # of it is reported instead, and the leaderboard gap is the check.
        lots.append(Lot(asset=asset, condition_id=mm.get("condition_id", ""),
                        event_slug=mm.get("event_slug", ""), title=mm.get("title", ""),
                        outcome=mm.get("outcome", ""), shares=left, entry_price=price,
                        entry_ts=ts, exit_price=price, exit_ts=ts, exit_kind="unmatched"))


# ─── metrics ────────────────────────────────────────────────────────────────

def _pct(x: float, of: float) -> float:
    return 0.0 if not of else 100.0 * x / of


def _median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    i = min(len(ys) - 1, max(0, int(round(q * (len(ys) - 1)))))
    return ys[i]


def _bucket(lots: list[Lot], key, label_of=lambda k: str(k)) -> list[dict]:
    agg: dict[Any, dict] = defaultdict(lambda: {"lots": 0, "cost": 0.0, "pnl": 0.0})
    for lot in lots:
        a = agg[key(lot)]
        a["lots"] += 1
        a["cost"] += lot.cost
        a["pnl"] += lot.pnl
    total = sum(a["pnl"] for a in agg.values())
    out = []
    for k, a in agg.items():
        out.append({"label": label_of(k), "lots": a["lots"], "cost": a["cost"], "pnl": a["pnl"],
                    "return_pct": _pct(a["pnl"], a["cost"]), "share_of_pnl": _pct(a["pnl"], total)})
    return sorted(out, key=lambda r: -r["cost"])


def _timing_window(minute: Optional[float]) -> str:
    if minute is None:
        return "unknown"
    if minute < WIN_IN_MATCH[0]:
        return "prematch"
    if minute < WIN_IN_MATCH[1]:
        return "in_match"
    if minute < WIN_WHISTLE[1]:
        return "whistle"
    return "settle"


def _exposure(lots: list[Lot]) -> dict:
    """Peak and median open cost basis, and the cumulative cash floor.

    The cash floor answers a question the P&L cannot: how much money did this
    wallet ever have to put in? A floor near zero means it funded itself out of
    its own winnings from the first week.
    """
    events: list[tuple[int, float, float]] = []   # ts, d(open cost), d(cash)
    for lot in lots:
        events.append((lot.entry_ts, lot.cost, -lot.cost))
        if lot.exit_ts is not None:
            events.append((lot.exit_ts, -lot.cost, lot.proceeds))
    events.sort(key=lambda e: e[0])
    open_cost = cash = 0.0
    peak = 0.0
    floor = 0.0
    series: list[float] = []
    for _, d_open, d_cash in events:
        open_cost += d_open
        cash += d_cash
        peak = max(peak, open_cost)
        floor = min(floor, cash)
        series.append(open_cost)
    return {"peak_cost_basis": peak, "median_cost_basis": _median(series),
            "cash_floor": floor, "final_cash": cash}


def _bootstrap(lots: list[Lot], seed: int = 0x9E3779B9, draws: int = 4000) -> dict:
    """Yield CI, resampled over EVENTS, not lots.

    Lots inside one fixture are the same bet taken repeatedly — treating them as
    independent is how a ±20pp interval gets reported as ±3pp.
    """
    by_event: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for lot in lots:
        e = by_event[lot.event_slug or lot.condition_id or "?"]
        e[0] += lot.cost
        e[1] += lot.pnl
    keys = sorted(by_event)
    if len(keys) < 5:
        return {"events": len(keys), "yield_pct": 0.0, "ci_lo": None, "ci_hi": None, "p_le_zero": None}
    costs = [by_event[k][0] for k in keys]
    pnls = [by_event[k][1] for k in keys]
    n = len(keys)
    point = _pct(sum(pnls), sum(costs))
    rng = Mulberry32(seed)
    ys: list[float] = []
    neg = 0
    for _ in range(draws):
        c = p = 0.0
        for _ in range(n):
            i = int(rng.next() * n)
            i = n - 1 if i >= n else i
            c += costs[i]
            p += pnls[i]
        y = _pct(p, c)
        ys.append(y)
        if y <= 0:
            neg += 1
    ys.sort()
    return {"events": n, "yield_pct": point,
            "ci_lo": ys[int(0.025 * len(ys))], "ci_hi": ys[int(0.975 * len(ys))],
            "p_le_zero": neg / draws}


# How far PM's leaderboard may sit from our gross reconstruction and still count
# as agreeing. king1605 sits 0.35% off once fees are added back, for a reason we
# have not found; GSX- sits inside its bracket.
RECON_TOL_PCT = 0.01


def analyse(wallet: str, rows: list[dict], markets: dict[str, dict],
            complete: bool = True, lb_profit: Optional[float] = None,
            current_value: Optional[float] = None,
            sports: Optional[dict] = None) -> dict:
    all_lots, flows = build_lots(rows, markets)
    if not all_lots:
        raise SystemExit(f"no football/market activity reconstructed for {wallet}")

    # Unmatched shares arrived off-feed (neg-risk conversions). They are booked
    # flat, so they carry zero P&L but WOULD carry cost — and cost they never
    # actually paid makes every share-of-capital line sum past 100%. They are
    # held out of every aggregation and reported on their own instead.
    unmatched = [l for l in all_lots if l.exit_kind == "unmatched"]
    lots = [l for l in all_lots if l.exit_kind != "unmatched"]

    ident = next((r for r in reversed(rows) if r.get("name") or r.get("pseudonym")), {})
    deployed = flows["deployed"]          # cash actually spent on BUY fills
    pnl = sum(l.pnl for l in lots)
    pnl_high = pnl + sum(l.proceeds for l in unmatched)

    closed = [l for l in lots if l.exit_ts is not None and l.exit_kind != "unmatched"]
    marks = [l for l in lots if l.exit_ts is None]
    holds = [l.hold_s / 60.0 for l in closed if l.hold_s is not None]

    ts_all = [r["timestamp"] for r in rows]
    first_ts, last_ts = min(ts_all), max(ts_all)
    days_active = len({datetime.fromtimestamp(t, timezone.utc).date() for t in ts_all})

    # ── daily P&L ──
    daily: dict[str, float] = defaultdict(float)
    for lot in lots:
        when = lot.exit_ts if lot.exit_ts is not None else lot.entry_ts
        daily[datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%d")] += lot.pnl
    day_vals = [daily[d] for d in sorted(daily)]
    streak = worst_streak = 0
    for v in day_vals:
        streak = streak + 1 if v < 0 else 0
        worst_streak = max(worst_streak, streak)

    # ── monthly evolution ──
    months: dict[str, dict] = defaultdict(
        lambda: {"deployed": 0.0, "pnl": 0.0, "lots": 0, "events": set(),
                 "prematch_cost": 0.0, "entry_w": 0.0, "hold": []})
    for lot in lots:
        mth = datetime.fromtimestamp(lot.entry_ts, timezone.utc).strftime("%Y-%m")
        m = months[mth]
        m["deployed"] += lot.cost
        m["pnl"] += lot.pnl
        m["lots"] += 1
        m["events"].add(lot.event_slug or lot.condition_id)
        if _timing_window(lot.entry_minute) == "prematch":
            m["prematch_cost"] += lot.cost
        m["entry_w"] += lot.entry_price * lot.cost
        if lot.hold_s is not None:
            m["hold"].append(lot.hold_s / 60.0)
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    month_rows = []
    for mth in sorted(months):
        m = months[mth]
        month_rows.append({
            "partial": mth == this_month,
            "month": mth, "deployed": m["deployed"], "pnl": m["pnl"],
            "yield_pct": _pct(m["pnl"], m["deployed"]), "lots": m["lots"],
            "events": len(m["events"]),
            "prematch_share": _pct(m["prematch_cost"], m["deployed"]),
            "mean_entry": (m["entry_w"] / m["deployed"]) if m["deployed"] else 0.0,
            "median_hold_min": _median(m["hold"]),
        })

    # ── concentration ──
    by_event: dict[str, float] = defaultdict(float)
    for lot in lots:
        by_event[lot.event_slug or lot.condition_id] += lot.pnl
    ranked = sorted(by_event.values(), reverse=True)
    top_lots = sorted((l.pnl for l in lots), reverse=True)
    drop200 = pnl - sum(top_lots[:200])

    # ── the sweep leg: cheap in, repriced out ──
    sweeps = [l for l in closed
              if l.entry_price <= 0.15 and l.exit_price >= 3 * max(l.entry_price, 1e-9)]
    sweep_examples = sorted(sweeps, key=lambda l: -l.pnl)[:6]

    # ── in-match price-move decomposition ──
    move_rows = []
    in_match = [l for l in closed if _timing_window(l.entry_minute) == "in_match"]
    for label, lo, hi in MOVE_BANDS:
        sel = [l for l in in_match if lo <= (l.exit_price - l.entry_price) < hi]
        move_rows.append({"label": label, "lots": len(sel),
                          "cost": sum(l.cost for l in sel), "pnl": sum(l.pnl for l in sel)})

    timing = {}
    for win in ("prematch", "in_match", "whistle", "settle", "unknown"):
        sel = [l for l in lots if _timing_window(l.entry_minute) == win]
        timing[win] = {"lots": len(sel), "cost": sum(l.cost for l in sel),
                       "pnl": sum(l.pnl for l in sel),
                       "return_pct": _pct(sum(l.pnl for l in sel), sum(l.cost for l in sel)),
                       "share_of_cost": _pct(sum(l.cost for l in sel), deployed)}

    entry_bands = []
    for lo, hi in ENTRY_BANDS:
        sel = [l for l in lots if lo <= l.entry_price < hi]
        entry_bands.append({"label": f"{lo:.2f}–{hi:.2f}", "lo": lo, "hi": hi, "lots": len(sel),
                            "cost": sum(l.cost for l in sel), "pnl": sum(l.pnl for l in sel),
                            "return_pct": _pct(sum(l.pnl for l in sel), sum(l.cost for l in sel)),
                            "share_of_pnl": _pct(sum(l.pnl for l in sel), pnl)})

    buy_tickets = [float(r.get("usdcSize") or 0.0) for r in rows
                   if r.get("type") == "TRADE" and r.get("side") == "BUY"
                   and float(r.get("usdcSize") or 0.0) > 0]
    football_cost = unknown_sport_cost = 0.0
    for lot in lots:
        verdict = _football(markets.get(lot.condition_id, {}), sports)
        if verdict is True:
            football_cost += lot.cost
        elif verdict is None:
            unknown_sport_cost += lot.cost

    exposure = _exposure(lots)
    buy_usd = flows["deployed"]
    buy_shares = sum(l.shares for l in lots)
    sell_lots = [l for l in closed if l.exit_kind == "sell"]

    # Polymarket's leaderboard profit is GROSS of trading fees and leaves rebates
    # out. Measured, not assumed: lb − (pnl + fees) is −$145 on king1605 ($5,876
    # of fees) and lands inside the unmatched bracket on GSX-; adding rebates
    # pushes BOTH outside. Compared net, every fee-paying wallet read as
    # "unreconciled" off nothing.
    fees = flows["fees"]
    recon_tol = max(1.0, RECON_TOL_PCT * abs(lb_profit)) if lb_profit is not None else 1.0

    profile = {
        "wallet": wallet.lower(),
        "name": ident.get("name") or "",
        "pseudonym": ident.get("pseudonym") or "",
        "profile_image": ident.get("profileImage") or "",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage": {
            "rows": len(rows), "complete": complete,
            "first_ts": first_ts, "last_ts": last_ts,
            "days_span": max(1, round((last_ts - first_ts) / 86400)),
            "days_active": days_active,
            "markets": len({l.condition_id for l in lots}),
            "events": len({l.event_slug or l.condition_id for l in lots}),
            "tokens": len({l.asset for l in lots}),
            "metadata_missing": len({l.condition_id for l in lots if l.condition_id not in markets}),
            "unhandled": flows["unhandled"],
            "unmatched_lots": len(unmatched),
            "unmatched_proceeds": sum(l.proceeds for l in unmatched),
        },
        "totals": {
            "fills": flows["buys"] + flows["sells"], "buys": flows["buys"],
            "sells": flows["sells"], "redeems": flows["redeems"],
            "deployed": deployed, "sell_proceeds": flows["sell_proceeds"],
            "redeem_proceeds": flows["redeem_proceeds"], "open_value": flows["open_value"],
            "pnl": pnl, "pnl_high": pnl_high,
            "yield_pct": _pct(pnl, deployed), "yield_high_pct": _pct(pnl_high, deployed),
            "realized_pnl": sum(l.pnl for l in closed),
            "marked_pnl": sum(l.pnl for l in marks),
            "rebates": flows["rebates"], "rewards": flows["rewards"], "fees": fees,
            # A ticket is what the wallet actually sent to the book. FIFO
            # splits one buy across several exits, so lot costs are fragments
            # of tickets and their median runs far below the real one.
            "median_ticket": _median(buy_tickets),
            "p90_ticket": _quantile(buy_tickets, 0.90),
            "max_ticket": max(buy_tickets, default=0.0),
            "buy_vwap": (buy_usd / buy_shares) if buy_shares else 0.0,
            "sell_vwap": (sum(l.proceeds for l in sell_lots) / sum(l.shares for l in sell_lots))
                         if sell_lots else 0.0,
            "fills_per_active_day": (flows["buys"] + flows["sells"]) / max(1, days_active),
            "current_value": current_value,
        },
        "reconciliation": {
            "lb_profit": lb_profit,
            "basis": "gross of fees, rebates excluded",
            "reconstructed": pnl + fees,
            "reconstructed_high": pnl_high + fees,
            "tolerance": recon_tol,
            "inside_bracket": (lb_profit is not None and
                               pnl + fees - recon_tol <= lb_profit <= pnl_high + fees + recon_tol),
        },
        "exposure": {**exposure,
                     "turnover": (deployed / exposure["peak_cost_basis"])
                     if exposure["peak_cost_basis"] else 0.0},
        "hold": {
            "median_min": _median(holds), "p90_min": _quantile(holds, 0.90),
            "under_10min_pct": _pct(sum(1 for h in holds if h <= 10), len(holds)),
            "over_1day_pct": _pct(sum(1 for h in holds if h > 1440), len(holds)),
        },
        "exits": {
            "sell_pct": _pct(len([l for l in lots if l.exit_kind == "sell"]), len(lots)),
            "redeem_pct": _pct(len([l for l in lots if l.exit_kind == "redeem"]), len(lots)),
            "merge_pct": _pct(len([l for l in lots if l.exit_kind == "merge"]), len(lots)),
            "resolved_pct": _pct(len([l for l in lots if l.exit_kind == "resolved"]), len(lots)),
            "open_pct": _pct(len([l for l in lots if l.exit_kind == "open"]), len(lots)),
            "expired_worthless_pnl": sum(l.pnl for l in lots
                                         if l.exit_kind == "resolved" and l.exit_price == 0.0),
        },
        "timing": timing,
        "entry_bands": entry_bands,
        "moves": move_rows,
        "months": month_rows,
        "days": {
            "n": len(day_vals),
            "winning": sum(1 for v in day_vals if v > 0),
            "losing": sum(1 for v in day_vals if v < 0),
            "best": max(day_vals, default=0.0), "worst": min(day_vals, default=0.0),
            "worst_losing_streak": worst_streak,
        },
        "universe": _bucket(lots, lambda l: _competition(markets.get(l.condition_id, {}), sports))[:16],
        "market_types": _bucket(
            lots, lambda l: (markets.get(l.condition_id, {}) or {}).get("sportsMarketType") or "other")[:10],
        "concentration": {
            "top1_pct": _pct(sum(ranked[:1]), pnl), "top5_pct": _pct(sum(ranked[:5]), pnl),
            "top10_pct": _pct(sum(ranked[:10]), pnl),
            "profitable_events_pct": _pct(sum(1 for v in by_event.values() if v > 0), len(by_event)),
            "drop_top200_pnl": drop200, "drop_top200_yield_pct": _pct(drop200, deployed),
        },
        "sweeps": {
            "lots": len(sweeps), "events": len({l.event_slug for l in sweeps}),
            "cost": sum(l.cost for l in sweeps), "pnl": sum(l.pnl for l in sweeps),
            "return_pct": _pct(sum(l.pnl for l in sweeps), sum(l.cost for l in sweeps)),
            "share_of_pnl": _pct(sum(l.pnl for l in sweeps), pnl),
            "share_of_cost": _pct(sum(l.cost for l in sweeps), deployed),
            "median_ticket": _median([l.cost for l in sweeps]),
            "median_hold_min": _median([l.hold_s / 60.0 for l in sweeps if l.hold_s is not None]),
            "examples": [{"title": l.title, "outcome": l.outcome, "shares": l.shares,
                          "entry": l.entry_price, "exit": l.exit_price, "pnl": l.pnl,
                          "hold_min": (l.hold_s / 60.0) if l.hold_s is not None else None,
                          "kind": l.exit_kind} for l in sweep_examples],
        },
        "bootstrap": _bootstrap(lots),
        "sport": {
            "football_pct": _pct(football_cost, deployed),
            "unknown_pct": _pct(unknown_sport_cost, deployed),
        },
    }
    # How much of this report can be believed, in one field. Everything that
    # reads a number out loud checks it first.
    rec = profile["reconciliation"]
    profile["trust"] = (
        "partial" if not complete else
        "unreconciled" if (rec["lb_profit"] is not None and not rec["inside_bracket"]) else
        "ok")
    profile["archetypes"] = classify(profile)
    profile["narrative"] = narrate(profile)
    return profile


# ─── reading the wallet ─────────────────────────────────────────────────────
#
# Everything below is DERIVED — every sentence is a threshold on a number that
# appears in the profile, and no sentence is written without the number that
# licensed it. The TypeScript port in site/app/lib/wallet.ts implements the same
# rules; `--verify-site` is what stops the two drifting.

def _fmt_money(x: float) -> str:
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1000:
        return f"{sign}${a:,.0f}"
    return f"{sign}${a:,.2f}"


def _fmt_pct(x: Optional[float], dp: int = 1) -> str:
    return "—" if x is None else f"{x:+.{dp}f}%"


def _fmt_dur(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.1f} min"
    if minutes < 1440:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / 1440:.1f} days"


def classify(p: dict) -> list[dict]:
    """Which archetypes this wallet matches, and the numbers that say so.

    Archetypes are not exclusive — the interesting wallets are two businesses
    stapled together, and reporting only the dominant one hides the half that
    actually makes the money.
    """
    t, tim, hold = p["totals"], p["timing"], p["hold"]
    dep = max(t["deployed"], 1e-9)
    out: list[dict] = []

    def add(key, label, conf, evidence):
        out.append({"key": key, "label": label, "confidence": conf, "evidence": evidence})

    prematch = tim["prematch"]["share_of_cost"]
    live = tim["in_match"]["share_of_cost"] + tim["whistle"]["share_of_cost"] + tim["settle"]["share_of_cost"]

    sw = p["sweeps"]
    if sw["lots"] >= 20 and sw["share_of_pnl"] >= 25:
        add("sweeper", "Stale-order sweeper",
            "high" if sw["share_of_pnl"] >= 40 else "medium",
            [f"{sw['lots']:,} lots entered at ≤0.15 and exited at ≥3× carry "
             + (f"MORE than all of the profit ({sw['share_of_pnl']:.0f}%) — the rest of the book "
                f"loses money — on {sw['share_of_cost']:.1f}% of the capital"
                if sw["share_of_pnl"] > 100 else
                f"{sw['share_of_pnl']:.0f}% of all profit on {sw['share_of_cost']:.1f}% of the capital"),
             f"median sweep ticket {_fmt_money(sw['median_ticket'])}, median hold "
             f"{_fmt_dur(sw['median_hold_min'])}, across {sw['events']} events"])

    if live >= 80 and hold["median_min"] <= 60:
        add("inplay_scalper", "In-play short-horizon trader", "high",
            [f"{live:.1f}% of capital goes in after kick-off, {prematch:.1f}% before",
             f"median hold {_fmt_dur(hold['median_min'])}, "
             f"{hold['under_10min_pct']:.0f}% of round trips close inside 10 minutes"])
    elif prematch >= 60:
        add("prematch", "Pre-match position taker", "high",
            [f"{prematch:.1f}% of capital is committed before kick-off",
             f"median hold {_fmt_dur(hold['median_min'])}"])

    football = p.get("sport", {}).get("football_pct", 100.0)
    if football >= 50 and tim["whistle"]["share_of_cost"] + tim["settle"]["share_of_cost"] >= 15:
        share = tim["whistle"]["pnl"] + tim["settle"]["pnl"]
        add("post_whistle", "Post-whistle settlement buyer",
            "high" if _pct(share, p["totals"]["pnl"]) >= 30 else "medium",
            [f"{tim['whistle']['share_of_cost'] + tim['settle']['share_of_cost']:.1f}% of capital "
             f"is deployed after minute 110 — when the result is already public",
             f"that capital returns {tim['whistle']['return_pct']:+.1f}% (whistle) / "
             f"{tim['settle']['return_pct']:+.1f}% (post-settlement)"])

    bands = {b["label"]: b for b in p["entry_bands"]}
    cheap = sum(b["cost"] for b in p["entry_bands"] if b["hi"] <= 0.15)
    rich = sum(b["cost"] for b in p["entry_bands"] if b["lo"] >= 0.90)
    if _pct(cheap, dep) >= 35:
        add("longshot", "Longshot buyer", "high",
            [f"{_pct(cheap, dep):.0f}% of capital enters below 0.15"])
    if _pct(rich, dep) >= 35:
        add("favourite_grinder", "Near-certainty grinder", "high",
            [f"{_pct(rich, dep):.0f}% of capital enters above 0.90 — thin margins, high turnover",
             f"capital recycled {p['exposure']['turnover']:.1f}× against a peak book of "
             f"{_fmt_money(p['exposure']['peak_cost_basis'])}"])

    if p["totals"]["rebates"] > 0 and p["totals"]["rebates"] >= 0.02 * abs(p["totals"]["pnl"] or 1):
        add("maker", "Earns liquidity rebates", "medium",
            [f"{_fmt_money(p['totals']['rebates'])} of maker/taker rebates — "
             f"{_pct(p['totals']['rebates'], abs(p['totals']['pnl']) or 1):.0f}% the size of trading P&L"])

    if p["concentration"]["top5_pct"] >= 60:
        add("concentrated", "Concentrated punter", "high",
            [f"the top 5 events carry {p['concentration']['top5_pct']:.0f}% of all profit — "
             f"this is a handful of bets, not a process"])

    if p["coverage"]["events"] >= 200 and t["fills_per_active_day"] >= 30:
        add("systematic", "Systematic / automated", "high",
            [f"{t['fills']:,} fills across {p['coverage']['events']:,} events, "
             f"{t['fills_per_active_day']:.0f} fills per active day",
             f"median ticket {_fmt_money(t['median_ticket'])} — size is uniform, which is what a script looks like"])

    if not out:
        add("unclassified", "No clear archetype", "low",
            ["none of the archetype thresholds fired — read the tables directly"])
    return out


def narrate(p: dict) -> dict:
    """The written reading: strategy, money, evolution, and what is not known."""
    t, tim, cov = p["totals"], p["timing"], p["coverage"]
    hold, exp, conc, boot = p["hold"], p["exposure"], p["concentration"], p["bootstrap"]
    name = p["name"] or p["wallet"][:10]
    dep = max(t["deployed"], 1e-9)
    sections: list[dict] = []

    # ── headline ──
    live = tim["in_match"]["share_of_cost"] + tim["whistle"]["share_of_cost"] + tim["settle"]["share_of_cost"]
    warn = {
        "partial": "⚠️ PARTIAL HISTORY — the activity walk did not reach the start of this "
                   "account, so every figure below describes only the window we read, and the "
                   "FIFO matching is missing the positions that were opened before it. ",
        "unreconciled": "⚠️ THIS RECONSTRUCTION DOES NOT RECONCILE with Polymarket's own profit "
                        "figure for the wallet, so the numbers below are wrong by an unknown "
                        "amount — most likely it exits through an activity type the feed does not "
                        "publish, such as a merge. Read the shape, not the totals. ",
    }.get(p.get("trust", "ok"), "")
    headline = (
        warn
        + f"{name} deployed {_fmt_money(t['deployed'])} across {cov['events']:,} events in "
        f"{cov['days_span']} days and finished {_fmt_money(t['pnl'])} — a yield of "
        f"{t['yield_pct']:+.2f}% on money at risk. "
        f"{live:.0f}% of that capital went in after kick-off, the median position was held "
        f"{_fmt_dur(hold['median_min'])}, and the median ticket was {_fmt_money(t['median_ticket'])}."
    )

    # ── what it does ──
    para = []
    arche = ", ".join(a["label"] for a in p["archetypes"])
    para.append(f"**Archetype: {arche}.**")
    for a in p["archetypes"]:
        para.append(f"— *{a['label']}* ({a['confidence']} confidence): "
                    + "; ".join(a["evidence"]) + ".")
    para.append(
        f"It trades {cov['markets']:,} markets over {cov['events']:,} fixtures — "
        f"{t['fills']:,} fills, {t['fills_per_active_day']:.0f} per active day over "
        f"{cov['days_active']} days with activity. It buys at an average of "
        f"{t['buy_vwap']:.3f} and sells at {t['sell_vwap']:.3f}."
    )
    top_uni = p["universe"][:4]
    if top_uni:
        para.append("Where it plays: " + ", ".join(
            f"{u['label']} ({_pct(u['cost'], dep):.0f}% of capital, {u['return_pct']:+.0f}%)"
            for u in top_uni) + ".")
    types = [m for m in p["market_types"] if m["label"] != "other"][:4]
    if types:
        para.append("Market types: " + ", ".join(
            f"{m['label']} {_pct(m['cost'], dep):.0f}%" for m in types) + ".")
    sections.append({"title": "What it does", "paragraphs": para})

    # ── where the money is ──
    para = []
    windows = [("before kick-off", tim["prematch"]), ("in play", tim["in_match"]),
               ("in the 20 min after the whistle", tim["whistle"]),
               ("after settlement time", tim["settle"])]
    best = max((w for w in windows if w[1]["cost"] > 0.02 * dep),
               key=lambda w: w[1]["pnl"], default=None)
    if best:
        para.append(
            f"**The money is made {best[0]}**: {_fmt_money(best[1]['cost'])} deployed there returned "
            f"{_fmt_money(best[1]['pnl'])} ({best[1]['return_pct']:+.1f}%), which is "
            f"{_pct(best[1]['pnl'], t['pnl']):.0f}% of all profit on "
            f"{best[1]['share_of_cost']:.0f}% of the capital.")
    bands = sorted((b for b in p["entry_bands"] if b["cost"] > 0.01 * dep),
                   key=lambda b: -b["return_pct"])
    if len(bands) >= 2:
        hi, lo = bands[0], bands[-1]
        para.append(
            f"Return falls with the entry price: {hi['return_pct']:+.0f}% in the {hi['label']} band "
            f"against {lo['return_pct']:+.0f}% in {lo['label']}. "
            + ("That is a wallet paid for taking prices nobody else wanted, not for being right more often."
               if hi["hi"] <= 0.40 else
               "The profitable end is the expensive end — this is a wallet paid for conviction, not for cheapness."))
    if p["moves"]:
        # The flat bucket is the one whose price did not move — NOT whichever
        # bucket happens to hold the most capital, which on some wallets is the
        # tail and produces a sentence comparing a bucket to itself.
        flat = next((m for m in p["moves"] if m["label"].startswith("flat")), None)
        tail = p["moves"][-1]
        if flat and flat is not tail and flat["cost"] > 0 and tail["lots"]:
            para.append(
                f"In-play, the modal trade goes nowhere: the {flat['label']} bucket is "
                f"{flat['lots']:,} lots and {_fmt_money(flat['cost'])} of capital for "
                f"{_fmt_money(flat['pnl'])}. The {tail['label']} bucket is {tail['lots']:,} lots and "
                f"{_fmt_money(tail['pnl'])}. "
                "The tail pays for the bleed — any copy of this has to be sized for that, "
                "because most positions lose the spread.")
    para.append(
        f"Exits: {p['exits']['sell_pct']:.0f}% sold back into the book, "
        # A wallet that never merges should not be told it merged 0% of the time.
        + (f"{p['exits']['merge_pct']:.0f}% merged back into USDC, "
           if p["exits"]["merge_pct"] >= 0.5 else "")
        + f"{p['exits']['redeem_pct']:.0f}% redeemed at settlement, "
        f"{p['exits']['resolved_pct']:.0f}% simply left to resolve "
        f"({_fmt_money(p['exits']['expired_worthless_pnl'])} of that expired worthless).")
    sections.append({"title": "Where the money comes from", "paragraphs": para})

    # ── evolution ──
    para = []
    # The month still being lived is a fraction of a month. Comparing it to a
    # full one reads as a collapse in turnover that never happened.
    months = [m for m in p["months"] if not m.get("partial")]
    partial = [m for m in p["months"] if m.get("partial")]
    if len(months) >= 2:
        first, last = months[0], months[-1]
        scale = "grew" if last["deployed"] > 1.3 * first["deployed"] else (
            "shrank" if last["deployed"] < 0.7 * first["deployed"] else "held roughly steady")
        half = len(months) // 2
        early = _pct(sum(m["pnl"] for m in months[:half]), sum(m["deployed"] for m in months[:half]) or 1)
        late = _pct(sum(m["pnl"] for m in months[half:]), sum(m["deployed"] for m in months[half:]) or 1)
        trend = ("improving" if late > early + 2 else
                 "decaying" if late < early - 2 else "flat")
        verdict = {
            "decaying": "A strategy that fades over its own life is usually one that ran out of "
                        "the thing it was picking up, or was copied.",
            "improving": ("Turnover fell while the yield rose, which reads as the wallet becoming "
                          "more selective rather than more skilful."
                          if scale == "shrank" else
                          "Yield rising while turnover holds or grows is the signature of a "
                          "repeatable process rather than a lucky run."),
            "flat": "Yield roughly constant across the life of the wallet.",
        }[trend]
        para.append(
            f"Across {len(months)} complete months the book {scale} from "
            f"{_fmt_money(first['deployed'])} to {_fmt_money(last['deployed'])} of monthly turnover, "
            f"and the yield is **{trend}** — {early:+.1f}% over the first {half} month(s) against "
            f"{late:+.1f}% over the last {len(months) - half}. " + verdict)
        if partial:
            para.append(
                f"{partial[0]['month']} is still in progress ({_fmt_money(partial[0]['deployed'])} "
                f"deployed, {partial[0]['yield_pct']:+.1f}%) and is excluded from the trend.")
        # regime change: the largest month-over-month shift in what it buys
        shifts = []
        for a, b in zip(months, months[1:]):
            shifts.append((abs(b["prematch_share"] - a["prematch_share"]), "timing", a, b))
            shifts.append((abs(b["mean_entry"] - a["mean_entry"]) * 100, "price", a, b))
        shifts.sort(reverse=True, key=lambda s: s[0])
        size, what, a, b = shifts[0]
        if size >= 15:
            if what == "timing":
                para.append(
                    f"**Regime change in {b['month']}**: the share of capital committed before "
                    f"kick-off moved {a['prematch_share']:.0f}% → {b['prematch_share']:.0f}%. "
                    "That is a different strategy, not a different month — split any evaluation there.")
            else:
                para.append(
                    f"**Regime change in {b['month']}**: the average entry price moved "
                    f"{a['mean_entry']:.2f} → {b['mean_entry']:.2f}. It started buying a different "
                    "kind of position; the earlier record does not describe the current one.")
        best_m = max(months, key=lambda m: m["yield_pct"])
        worst_m = min(months, key=lambda m: m["yield_pct"])
        para.append(
            f"Best month {best_m['month']} ({_fmt_money(best_m['pnl'])}, "
            f"{best_m['yield_pct']:+.1f}%), worst {worst_m['month']} ({_fmt_money(worst_m['pnl'])}, "
            f"{worst_m['yield_pct']:+.1f}%).")
    d = p["days"]
    para.append(
        f"Day to day it wins {d['winning']} of {d['n']} sessions — best {_fmt_money(d['best'])}, "
        f"worst {_fmt_money(d['worst'])}, longest losing streak {d['worst_losing_streak']} days. "
        f"Peak open book {_fmt_money(exp['peak_cost_basis'])}; the cumulative cash floor is "
        f"{_fmt_money(exp['cash_floor'])}, "
        + ("so it never needed much more than its first stake — it funded itself out of its own winnings."
           if exp["cash_floor"] > -0.25 * exp["peak_cost_basis"] else
           "so it had to carry a real bankroll, not just recycle winnings."))
    sections.append({"title": "How it evolved", "paragraphs": para})

    # ── is it real ──
    para = []
    if boot["ci_lo"] is not None:
        survives = boot["ci_lo"] > 0
        para.append(
            f"Bootstrapped over {boot['events']:,} events (resampled by event, not by lot, because "
            f"lots inside one fixture are the same bet taken repeatedly): yield "
            f"{boot['yield_pct']:+.2f}%, 95% CI [{boot['ci_lo']:+.2f}%, {boot['ci_hi']:+.2f}%], "
            f"p(≤0) = {boot['p_le_zero']:.4f}. "
            + ("The interval clears zero — the edge in this wallet's own record is real."
               if survives else
               "**The interval contains zero.** Whatever this wallet's headline profit, its record "
               "does not distinguish it from a wallet that got lucky at this size."))
    para.append(
        f"Concentration: top event {conc['top1_pct']:.0f}% of profit, top 5 {conc['top5_pct']:.0f}%, "
        f"top 10 {conc['top10_pct']:.0f}%; {conc['profitable_events_pct']:.0f}% of events profitable. "
        + (f"Drop the 200 best individual lots and it still makes "
           f"{_fmt_money(conc['drop_top200_pnl'])} ({conc['drop_top200_yield_pct']:+.2f}%). "
           if conc["drop_top200_pnl"] >= 0 else
           f"Drop the 200 best individual lots and the rest of the book LOSES "
           f"{_fmt_money(abs(conc['drop_top200_pnl']))} ({conc['drop_top200_yield_pct']:+.2f}%) — "
           f"the entire result is those 200 trades. ")
        + ("The result does not depend on a handful of bets." if conc["top5_pct"] < 35 else
           "**A large share of the result is a handful of bets** — treat the headline as one draw."))
    rec = p["reconciliation"]
    if rec["lb_profit"] is not None:
        if cov["unmatched_lots"]:
            para.append(
                f"Reconciliation: Polymarket's own all-time profit for this wallet is "
                f"{_fmt_money(rec['lb_profit'])}, counted before fees; on the same basis (P&L plus "
                f"{_fmt_money(t['fees'])} of fees paid) our reconstruction brackets it at "
                f"{_fmt_money(rec['reconstructed'])} … {_fmt_money(rec['reconstructed_high'])} "
                + ("(it falls inside — the reconstruction is trustworthy)."
                   if rec["inside_bracket"] else
                   "(**it falls outside — do not trust these numbers**; something in the "
                   "reconstruction is wrong, most likely an activity type we do not model)."))
        else:
            gap = rec["lb_profit"] - rec["reconstructed"]
            para.append(
                f"Reconciliation: Polymarket says {_fmt_money(rec['lb_profit'])} all-time, counted "
                f"before fees; on the same basis (P&L plus {_fmt_money(t['fees'])} of fees paid) we "
                f"reconstruct {_fmt_money(rec['reconstructed'])} — a gap of {_fmt_money(gap)} "
                f"({_pct(abs(gap), abs(rec['lb_profit']) or 1):.1f}%). "
                + ("Close enough to trust." if rec["inside_bracket"]
                   else "**That gap is large enough to matter — read the numbers as approximate.**"))
    sections.append({"title": "Is the edge real?", "paragraphs": para})

    # ── caveats ──
    flags = []
    if not cov["complete"]:
        flags.append("⚠️ The activity walk did not finish — this is a PARTIAL history and every "
                     "total below is a lower bound.")
    if cov["unmatched_lots"]:
        flags.append(
            f"⚠️ {cov['unmatched_lots']} lots sold shares that never appear as a purchase "
            f"({_fmt_money(cov['unmatched_proceeds'])} of proceeds) — neg-risk conversions, which "
            "Polymarket's activity feed does not publish. They are booked FLAT, so they add no "
            "profit; the true figure is inside the bracket above, not at either end.")
    if cov["unhandled"]:
        flags.append(f"⚠️ Activity types not modelled: {cov['unhandled']}. "
                     "Any wallet that exits through them is mis-measured here.")
    if cov["metadata_missing"]:
        flags.append(f"⚠️ {cov['metadata_missing']} markets have no metadata — their lots carry no "
                     "kick-off time and fall in the 'unknown' timing bucket.")
    if t["marked_pnl"] and abs(t["marked_pnl"]) > 0.05 * abs(t["pnl"] or 1):
        flags.append(
            f"⚠️ {_fmt_money(t['marked_pnl'])} of the P&L is unsold positions marked at settlement "
            "or at last trade — a mark, not money.")
    sport = p.get("sport", {})
    if sport.get("football_pct", 100.0) < 50:
        flags.append(
            f"⚠️ Only {sport['football_pct']:.0f}% of this wallet's capital is in competitions we "
            f"recognise as football ({sport['unknown_pct']:.0f}% is unrecognised). The 110-130' "
            "whistle window is a football fact and means nothing on the rest — read the timing "
            "table for football wallets only.")
    flags.append(
        "⚠️ Selection bias on the wallet itself. You are reading it because someone pointed at it. "
        "The statistics describe the edge in its own record; they cannot tell you how many "
        "identically-shaped wallets blew up unseen.")
    flags.append(
        "⚠️ `lb-api` 'volume' is SHARES, not dollars, and its windowed figures do not reconcile. "
        "Only `window=all` is used here.")
    if p["totals"]["median_ticket"] < 25 and any(a["key"] == "sweeper" for a in p["archetypes"]):
        flags.append(
            f"⚠️ Capacity: the median ticket is {_fmt_money(t['median_ticket'])} and the largest is "
            f"{_fmt_money(t['max_ticket'])}. This strategy is bounded by what other people leave "
            "resting on the book — it does not scale by adding money.")
    sections.append({"title": "What is not established", "paragraphs": flags})

    return {"headline": headline, "sections": sections}


# ─── output ─────────────────────────────────────────────────────────────────

def render_markdown(p: dict) -> str:
    t, cov, n = p["totals"], p["coverage"], p["narrative"]
    first = datetime.fromtimestamp(cov["first_ts"], timezone.utc).date()
    last = datetime.fromtimestamp(cov["last_ts"], timezone.utc).date()
    L: list[str] = []
    a = L.append
    a(f"# Wallet `{p['wallet'][:10]}…{p['wallet'][-6:]}`"
      + (f" = **{p['name']}**" if p["name"] else ""))
    a("")
    a(f"Analysed {p['generated_at'][:10]}. {cov['rows']:,} activity rows = "
      + ("the entire life of the account" if cov["complete"] else "a PARTIAL history")
      + f" ({first} → {last}, {cov['days_active']} active days).")
    a("")
    a("## The one-line version")
    a("")
    a(n["headline"])
    a("")
    a("## Shape")
    a("")
    a("| | |")
    a("|---|---|")
    a(f"| fills | {t['fills']:,} ({t['buys']:,} BUY / {t['sells']:,} SELL), "
      f"{t['fills_per_active_day']:.0f}/day |")
    a(f"| markets / events / tokens | {cov['markets']:,} / {cov['events']:,} / {cov['tokens']:,} |")
    a(f"| deployed | {_fmt_money(t['deployed'])} |")
    a(f"| P&L | **{_fmt_money(t['pnl'])}** ({t['yield_pct']:+.2f}%) |")
    a(f"| fees paid | {_fmt_money(t['fees'])} ({_pct(t['fees'], t['deployed']):.2f}% of deployed) |")
    a(f"| median ticket | {_fmt_money(t['median_ticket'])} (p90 {_fmt_money(t['p90_ticket'])}, "
      f"max {_fmt_money(t['max_ticket'])}) |")
    a(f"| BUY vwap / SELL vwap | {t['buy_vwap']:.3f} / {t['sell_vwap']:.3f} |")
    a(f"| median hold | **{_fmt_dur(p['hold']['median_min'])}** "
      f"({p['hold']['under_10min_pct']:.0f}% ≤10 min) |")
    a(f"| peak open cost basis | {_fmt_money(p['exposure']['peak_cost_basis'])} |")
    a(f"| cumulative cash floor | {_fmt_money(p['exposure']['cash_floor'])} |")
    a(f"| losing days | {p['days']['losing']} of {p['days']['n']} · worst "
      f"{_fmt_money(p['days']['worst'])} · best {_fmt_money(p['days']['best'])} |")
    a("")
    a("## By entry price")
    a("")
    a("| entry price | lots | cost | P&L | return | share of P&L |")
    a("|---|---|---|---|---|---|")
    for b in p["entry_bands"]:
        if b["lots"]:
            a(f"| {b['label']} | {b['lots']:,} | {_fmt_money(b['cost'])} | {_fmt_money(b['pnl'])} "
              f"| {b['return_pct']:+.0f}% | {b['share_of_pnl']:.1f}% |")
    a("")
    a("## By entry time (minutes after listed kick-off)")
    a("")
    a("| window | lots | cost | P&L | return |")
    a("|---|---|---|---|---|")
    for key, label in (("prematch", "pre-match (<0)"), ("in_match", "in-match (0–110)"),
                       ("whistle", "whistle (110–130)"), ("settle", "settle (130+)"),
                       ("unknown", "no kick-off time")):
        w = p["timing"][key]
        if w["lots"]:
            a(f"| {label} | {w['lots']:,} | {_fmt_money(w['cost'])} | {_fmt_money(w['pnl'])} "
              f"| {w['return_pct']:+.1f}% |")
    a("")
    a("## Month by month")
    a("")
    a("| month | lots | events | deployed | P&L | yield | pre-match $ | mean entry | median hold |")
    a("|---|---|---|---|---|---|---|---|---|")
    for m in p["months"]:
        a(f"| {m['month']} | {m['lots']:,} | {m['events']:,} | {_fmt_money(m['deployed'])} "
          f"| {_fmt_money(m['pnl'])} | {m['yield_pct']:+.1f}% | {m['prematch_share']:.0f}% "
          f"| {m['mean_entry']:.3f} | {_fmt_dur(m['median_hold_min'])} |")
    a("")
    for sec in n["sections"]:
        a(f"## {sec['title']}")
        a("")
        for para in sec["paragraphs"]:
            a(para)
            a("")
    if p["sweeps"]["examples"]:
        a("## Sweep examples (entry ≤0.15, exit ≥3×)")
        a("")
        a("```")
        for e in p["sweeps"]["examples"]:
            hold = f"{e['hold_min']:.1f}m" if e["hold_min"] is not None else "held"
            a(f"{_fmt_money(e['pnl']):>10}  {e['shares']:>8.0f} sh @{e['entry']:.3f} -> "
              f"{e['exit']:.2f} in {hold:>7}  {e['title'][:52]}")
        a("```")
        a("")
    return "\n".join(L)


def load(wallet: str, since: Optional[int] = None, verbose: bool = False) -> dict:
    log = (lambda m: print(f"  {m}", file=sys.stderr)) if verbose else (lambda m: None)
    s = _session()
    print(f"fetching activity for {wallet} …", file=sys.stderr)
    rows, complete = fetch_activity(wallet, since=since, session=s, log=log)
    if not rows:
        raise SystemExit(f"no activity for {wallet}")
    print(f"  {len(rows):,} rows"
          + ("" if complete else "  ⚠️ PARTIAL — the walk hit a guard, not the end"), file=sys.stderr)
    cids = {r.get("conditionId") for r in rows if r.get("conditionId")}
    print(f"fetching metadata for {len(cids):,} markets …", file=sys.stderr)
    markets = fetch_markets(cids, session=s, log=log)
    print(f"  {len(markets):,} resolved", file=sys.stderr)
    return analyse(wallet, rows, markets, complete=complete,
                   lb_profit=fetch_lb_profit(wallet, s),
                   current_value=fetch_current_value(wallet, s),
                   sports=fetch_sports(s))


def verify_site(profile: dict, base: str) -> int:
    """Cross-check the site's TypeScript port against this file's numbers.

    Two implementations of a FIFO reconstruction WILL drift, and a wallet page
    that quietly disagrees with the research it came from is worse than no page.
    """
    url = base.rstrip("/") + f"/api/wallet?address={profile['wallet']}"
    print(f"\nverifying against {url} …", file=sys.stderr)
    try:
        remote = requests.get(url, timeout=180).json()
    except Exception as e:
        print(f"  FAILED to reach the site: {e}", file=sys.stderr)
        return 2
    if remote.get("error"):
        print(f"  site returned an error: {remote['error']}", file=sys.stderr)
        return 2
    if not profile["coverage"]["complete"] or not (remote.get("coverage") or {}).get("complete", True):
        print("  NOTE both sides stopped early on this wallet (it is larger than either budget), "
              "so a row-count difference here is a difference in budget, not in method.",
              file=sys.stderr)

    checks = [
        ("rows", ("coverage", "rows"), 0),
        ("events", ("coverage", "events"), 0),
        ("unmatched_lots", ("coverage", "unmatched_lots"), 0),
        ("trust", ("trust",), 0),
        ("deployed", ("totals", "deployed"), 0.01),
        ("pnl", ("totals", "pnl"), 0.01),
        ("pnl_high", ("totals", "pnl_high"), 0.01),
        ("fees", ("totals", "fees"), 0.01),
        ("yield_pct", ("totals", "yield_pct"), 0.001),
        ("median_ticket", ("totals", "median_ticket"), 0.01),
        ("median_hold_min", ("hold", "median_min"), 0.001),
        ("peak_cost_basis", ("exposure", "peak_cost_basis"), 0.01),
        ("cash_floor", ("exposure", "cash_floor"), 0.01),
        ("whistle pnl", ("timing", "whistle", "pnl"), 0.01),
        ("sweeps pnl", ("sweeps", "pnl"), 0.01),
        ("top5 share", ("concentration", "top5_pct"), 0.001),
        ("bootstrap ci_lo", ("bootstrap", "ci_lo"), 0.001),
        ("bootstrap ci_hi", ("bootstrap", "ci_hi"), 0.001),
    ]
    bad = 0
    for label, path, tol in checks:
        mine = profile
        theirs = remote
        for k in path:
            mine = (mine or {}).get(k)
            theirs = (theirs or {}).get(k)
        if mine is None or theirs is None or isinstance(mine, str):
            ok = mine == theirs
        else:
            ok = abs(float(mine) - float(theirs)) <= max(tol, tol * abs(float(mine)))
        mark = "ok " if ok else "MISMATCH"
        print(f"  {mark} {label}: python={mine} site={theirs}", file=sys.stderr)
        if not ok:
            bad += 1

    # The prose is the part a reader actually acts on, and it is generated by
    # two separate rule sets. Numbers agreeing while the sentences disagree is
    # the failure this catches.
    mine_arch = [a["key"] for a in profile["archetypes"]]
    their_arch = [a.get("key") for a in (remote.get("archetypes") or [])]
    ok = mine_arch == their_arch
    print(f"  {'ok ' if ok else 'MISMATCH'} archetypes: python={mine_arch} site={their_arch}",
          file=sys.stderr)
    bad += 0 if ok else 1

    mine_n = profile["narrative"]
    their_n = remote.get("narrative") or {}
    ok = mine_n["headline"] == their_n.get("headline")
    print(f"  {'ok ' if ok else 'MISMATCH'} narrative headline", file=sys.stderr)
    if not ok:
        print(f"      python: {mine_n['headline']}", file=sys.stderr)
        print(f"      site  : {their_n.get('headline')}", file=sys.stderr)
    bad += 0 if ok else 1

    for a, b in zip(mine_n["sections"], their_n.get("sections") or []):
        for i, (pa, pb) in enumerate(zip(a["paragraphs"], b.get("paragraphs") or [])):
            if pa != pb:
                print(f"  MISMATCH {a['title']} ¶{i + 1}", file=sys.stderr)
                print(f"      python: {pa[:160]}", file=sys.stderr)
                print(f"      site  : {pb[:160]}", file=sys.stderr)
                bad += 1
        if len(a["paragraphs"]) != len(b.get("paragraphs") or []):
            print(f"  MISMATCH {a['title']}: paragraph count "
                  f"{len(a['paragraphs'])} vs {len(b.get('paragraphs') or [])}", file=sys.stderr)
            bad += 1
    print(("\nthe two implementations agree." if not bad else
           f"\n{bad} field(s) disagree — the site's port has drifted from this file."),
          file=sys.stderr)
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyse a Polymarket wallet.")
    ap.add_argument("wallet", help="0x… proxy wallet address")
    ap.add_argument("--since", help="only activity from this date (YYYY-MM-DD)")
    ap.add_argument("--json", metavar="PATH", help="write the full profile as JSON")
    ap.add_argument("--report", action="store_true",
                    help="write reports/wallet_<name>_<date>.md")
    ap.add_argument("--verify-site", metavar="BASE_URL",
                    help="compare against the site's /api/wallet for the same address")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    wallet = args.wallet.strip().lower()
    if not (wallet.startswith("0x") and len(wallet) == 42):
        ap.error("wallet must be a 0x-prefixed 40-hex-character address")

    since = None
    if args.since:
        since = int(datetime.strptime(args.since, "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())

    profile = load(wallet, since=since, verbose=args.verbose)
    md = render_markdown(profile)
    print()
    print(md)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(profile, fh, indent=2)
        print(f"\nwrote {args.json}", file=sys.stderr)

    if args.report:
        here = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.join(here, "..", "reports")
        os.makedirs(out_dir, exist_ok=True)
        tag = (profile["name"] or wallet[:10]).lower().strip("-").replace(" ", "_") or wallet[:10]
        path = os.path.join(out_dir, f"wallet_{tag}_{profile['generated_at'][:10]}.md")
        with open(path, "w") as fh:
            fh.write(md + "\n")
        print(f"wrote {os.path.relpath(path)}", file=sys.stderr)

    if args.verify_site:
        return verify_site(profile, args.verify_site)
    return 0


if __name__ == "__main__":
    sys.exit(main())
