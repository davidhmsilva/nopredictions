"""Where each market OPENED on the venue, and its last quote before kick-off.

DATA ONLY — no orders, no signals. One row per market in `venue_market_quotes`
(db/071), so every agent's entry can later be read against the venue's own open
and close: "did we buy cheaper than the venue closed?" is CLV measured on the
price we can actually trade.

Why the venue and not Pinnacle: there is no Pinnacle opening line we can read,
and Polymarket's mid sits on Pinnacle's line near the close (+0.10pp
CI[−0.01,+0.20]). The venue's close is therefore the benchmark; its open is
where any room would be. Pinnacle stays the fair value at the signal for the
agents that have it.

Sources, each a different claim:

| | where | quote | kick-off |
|---|---|---|---|
| Polymarket football | Gamma `/events/keyset`, 21 days ahead | `bestBid`/`bestAsk` (outcome 0), lags the CLOB | `startTime` |
| Kalshi football | the publisher's `kalshi-soccer.json` | real bid/ask + ask depth | **none**: `settlesAt` − 3h, estimated |
| US sports, both venues | the publisher's `sport-<k>.json` | CLOB / Kalshi, ask depth | ESPN |

🔑 **The open is the first quote with both sides and a spread <= 0.10**, not the
first quote seen. A freshly listed book is empty or holds one parked order
(Kalshi's 0.02/0.98 placeholders; PM's ask-only ladders), and that is not a
price anyone could trade at. `first_*` keeps the raw first sighting anyway.

⚠️ **Rows already listed when the recorder starts are not openings.**
Polymarket lists football ~13 days ahead (median 318h, 2026-10-10), so compare
`open_at` with `listed_at` before calling anything an opening.

⚠️ **Kalshi publishes no football kick-off.** `settlesAt` sits at kick-off
+2h to +4.5h (+3h on 55 of 67 pairs), so the stored kick-off is settles − 3h
and quotes stop at settles − 4.5h: never an in-play quote recorded as a close,
at the cost of a Kalshi football "last" taken up to 2.5h early.

    python venue_open_recorder.py --once --dry-run
    python venue_open_recorder.py --once          # timer, every 10 min
    python venue_open_recorder.py --report
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))

log = logging.getLogger("venue_open")

GAMMA = "https://gamma-api.polymarket.com"
BOARDS_DIR = Path(os.getenv("BOARDS_DIR", "/srv/boards"))
US_SPORTS = ("nfl", "cfb", "nba", "nhl", "mlb", "wnba")

HORIZON_DAYS = 21              # PM lists ~13 days ahead; the tail goes past 21
MAX_KEYSET_PAGES = 200         # ~82 on 2026-10-10
OPEN_MAX_SPREAD = 0.10         # wider is the "none" grade in lib/venues.ts: not a price
STALE_BOARD_S = 600            # the publisher writes every 60s
KALSHI_KO_OFFSET_H = 3.0       # settlesAt − kick-off, typical
KALSHI_KO_OFFSET_MAX_H = 4.5   # ... and the longest seen: quotes stop here

# Families recorded, from `sportsMarketType` — never the question text, which
# matches "O/U 2.5 Corners" as readily as goals.
PM_FAMILIES = {"moneyline": "moneyline", "totals": "totals",
               "spreads": "spreads", "both_teams_to_score": "btts"}
_SIBLING = re.compile(r"\s+-\s+(.+)$")
_VS = re.compile(r"^(.+?)\s+vs\.?\s+(.+)$", re.I)
_DRAW = re.compile(r"\bdraw\b", re.I)


# ── small helpers ────────────────────────────────────────────────────────────

def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _ts(v) -> Optional[datetime]:
    if not v:
        return None
    s = str(v).strip().replace(" ", "T").replace("Z", "+00:00")
    s = re.sub(r"(\.\d+)", "", s)            # Gamma mixes 5- and 6-digit fractions
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _jl(v) -> list:
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def clean_quote(bid, ask) -> tuple[Optional[float], Optional[float]]:
    """An empty side is None, not 0 or 1: a missing bid read as 0 is how PM's
    history manufactures prices that never traded."""
    b, a = _f(bid), _f(ask)
    if b is not None and b <= 0:
        b = None
    if a is not None and a >= 1:
        a = None
    return b, a


def is_open_quote(bid: Optional[float], ask: Optional[float]) -> bool:
    """Both sides, not crossed, and no wider than OPEN_MAX_SPREAD."""
    return (bid is not None and ask is not None and ask >= bid
            and ask - bid <= OPEN_MAX_SPREAD + 1e-9)


def _row(**kw) -> dict:
    kw["bid"], kw["ask"] = clean_quote(kw.get("bid"), kw.get("ask"))
    return kw


# ── Polymarket football (Gamma) ──────────────────────────────────────────────

def fetch_pm_soccer(now: datetime) -> list[dict]:
    """Every open soccer event kicking off in the next HORIZON_DAYS.

    `/events` refuses an offset past 2,100 — about 30 hours of football once
    every sibling event is counted — so this pages by keyset cursor. Bounded by
    `end_date_*` as plain DATES (a fixture's `endDate` IS its kick-off)."""
    params = {"tag_slug": "soccer", "closed": "false", "limit": 100,
              "order": "startTime", "ascending": "true",
              "end_date_min": now.strftime("%Y-%m-%d"),
              "end_date_max": (now + timedelta(days=HORIZON_DAYS)).strftime("%Y-%m-%d")}
    out: list[dict] = []
    cursor = None
    for page in range(MAX_KEYSET_PAGES):
        q = dict(params)
        if cursor:
            q["after_cursor"] = cursor
        try:
            r = requests.get(f"{GAMMA}/events/keyset", params=q, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as exc:                        # noqa: BLE001
            log.warning(f"gamma keyset page {page}: {exc}")
            break
        evs = j.get("events") or []
        out.extend(e for e in evs if isinstance(e, dict))
        cursor = j.get("next_cursor")
        if not cursor or not evs:
            break
    return out


def pm_soccer_rows(events: Iterable[dict], now: datetime) -> list[dict]:
    """One row per market (its outcome-0 token) on the main fixture event and
    its "- More Markets" sibling, in the four families that agents trade.

    `bestBid`/`bestAsk` on the listing are outcome 0's. Sides are the venue's
    own labels; `home`/`away` are PM's title order, which is a label, never a
    side (CLAUDE.md, the Lab runner)."""
    rows: list[dict] = []
    for e in events:
        title = e.get("title") or ""
        sib = _SIBLING.search(title)
        if sib and sib.group(1).strip().lower() != "more markets":
            continue
        base = _SIBLING.sub("", title).strip()
        vs = _VS.match(base)
        ko = _ts(e.get("startTime"))
        if not vs or ko is None or ko <= now or e.get("live") or e.get("ended"):
            continue
        comp = next((t.get("label") for t in e.get("tags") or []
                     if str(t.get("slug") or "") not in ("soccer", "sports", "games")), None)
        for m in e.get("markets") or []:
            fam = PM_FAMILIES.get(str(m.get("sportsMarketType") or "").lower())
            if fam is None or m.get("closed") or m.get("active") is False:
                continue
            toks, outs = _jl(m.get("clobTokenIds")), _jl(m.get("outcomes"))
            if not toks or not outs or not m.get("conditionId"):
                continue
            outcome = str(outs[0])
            if fam == "moneyline":
                q = m.get("question") or ""
                outcome = "Draw" if _DRAW.search(q) else (m.get("groupItemTitle") or q)
            rows.append(_row(
                venue="polymarket", market_key=str(toks[0]), sport="soccer",
                competition=comp, event_key=e.get("slug") or str(e.get("id")),
                event_title=base, home=vs.group(1).strip(), away=vs.group(2).strip(),
                family=fam, line=_f(m.get("line")), outcome=outcome,
                kickoff=ko, kickoff_source="gamma",
                listed_at=_ts(m.get("acceptingOrdersTimestamp") or m.get("createdAt")),
                bid=m.get("bestBid"), ask=m.get("bestAsk"),
                depth_usd=_f(m.get("liquidityNum")), depth_kind="liquidity", source="gamma"))
    return rows


# ── the publisher's board files (Kalshi football, US sports) ────────────────

def load_board(name: str, now: datetime) -> Optional[dict]:
    p = BOARDS_DIR / f"{name}.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        log.info(f"board {name}: not read ({exc.__class__.__name__})")
        return None
    gen = _ts(d.get("generatedAt"))
    if gen is None or (now - gen).total_seconds() > STALE_BOARD_S:
        log.warning(f"board {name}: stale (generatedAt {d.get('generatedAt')}) — skipped")
        return None
    return d


def _q(quote: Optional[dict]) -> dict:
    quote = quote or {}
    return {"bid": quote.get("bid"), "ask": quote.get("ask"),
            "depth_usd": _f(quote.get("askDepthUsd"))}


def kalshi_soccer_rows(board: dict, now: datetime) -> list[dict]:
    rows: list[dict] = []
    for fx in board.get("fixtures") or []:
        settles = _ts(fx.get("settlesAt"))
        if settles is None:
            continue
        if now >= settles - timedelta(hours=KALSHI_KO_OFFSET_MAX_H):
            continue                       # might already be in play
        ko = settles - timedelta(hours=KALSHI_KO_OFFSET_H)
        common = dict(venue="kalshi", sport="soccer", competition=fx.get("competition"),
                      event_key=fx.get("eventTicker"),
                      event_title=f"{fx.get('home')} vs {fx.get('away')}",
                      home=fx.get("home"), away=fx.get("away"), kickoff=ko,
                      kickoff_source="kalshi_settle_est", listed_at=None,
                      depth_kind="ask_usd", source="kalshi")
        for side, leg in (fx.get("legs") or {}).items():
            if not leg or not leg.get("ticker"):
                continue
            rows.append(_row(**common, market_key=leg["ticker"], family="moneyline",
                             line=None, outcome="Draw" if side == "draw" else (leg.get("label") or side),
                             **_q(leg.get("quote"))))
        for line, leg in (fx.get("totals") or {}).items():
            if not leg or not leg.get("ticker"):
                continue
            rows.append(_row(**common, market_key=leg["ticker"], family="totals",
                             line=_f(line), outcome="Over", **_q(leg.get("quote"))))
    return rows


def sport_board_rows(sport: str, board: dict, now: datetime) -> list[dict]:
    """Moneylines on both venues and Polymarket's main total.

    Kalshi's quotes on these boards carry no ticker, so its key is the ESPN
    game id + side; Polymarket's is the token id where the board gives one."""
    rows: list[dict] = []
    for g in board.get("games") or []:
        ko = _ts(g.get("start"))
        if ko is None or ko <= now or g.get("state") not in (None, "pre"):
            continue
        gid = str(g.get("id"))
        home, away = (g.get("home") or {}).get("name"), (g.get("away") or {}).get("name")
        common = dict(sport=sport, competition=sport.upper(), event_key=gid,
                      event_title=f"{away} @ {home}", home=home, away=away,
                      kickoff=ko, kickoff_source="espn", listed_at=None, depth_kind="ask_usd")
        toks = g.get("pmTokens") or {}
        for v in g.get("venues") or []:
            venue = v.get("venue")
            if venue not in ("polymarket", "kalshi"):
                continue
            for side, quote in (v.get("quotes") or {}).items():
                if side not in ("home", "away") or not quote:
                    continue
                key = (str(toks[side]) if venue == "polymarket" and toks.get(side)
                       else f"{sport}:{gid}:ml:{side}")
                rows.append(_row(**common, venue=venue, market_key=key, family="moneyline",
                                 line=None, outcome=side, source=v.get("source") or venue,
                                 **_q(quote)))
        tot = g.get("total")
        if tot and tot.get("line") is not None:
            for side in ("over", "under"):
                if not tot.get(side):
                    continue
                rows.append(_row(**common, venue="polymarket",
                                 market_key=f"{sport}:{gid}:total:{tot['line']}:{side}",
                                 family="totals", line=_f(tot["line"]), outcome=side,
                                 source="clob", **_q(tot[side])))
    return rows


# ── the database ─────────────────────────────────────────────────────────────

COLS = ("venue", "market_key", "sport", "competition", "event_key", "event_title",
        "home", "away", "family", "line", "outcome", "kickoff", "kickoff_source",
        "listed_at", "first_seen_at", "first_bid", "first_ask",
        "open_at", "open_bid", "open_ask", "open_depth_usd", "open_source",
        "last_at", "last_bid", "last_ask", "last_depth_usd", "last_source", "depth_kind")

# On conflict: the open is set once and never moved; the last quote moves only
# when the price changed (depth alone is noise), so an unchanged market writes
# no new tuple; a postponed fixture takes its new kick-off.
UPSERT = f"""
INSERT INTO venue_market_quotes AS t ({", ".join(COLS)}) VALUES %s
ON CONFLICT (venue, market_key) DO UPDATE SET
    open_at        = COALESCE(t.open_at, EXCLUDED.open_at),
    open_bid       = CASE WHEN t.open_at IS NULL THEN EXCLUDED.open_bid       ELSE t.open_bid END,
    open_ask       = CASE WHEN t.open_at IS NULL THEN EXCLUDED.open_ask       ELSE t.open_ask END,
    open_depth_usd = CASE WHEN t.open_at IS NULL THEN EXCLUDED.open_depth_usd ELSE t.open_depth_usd END,
    open_source    = CASE WHEN t.open_at IS NULL THEN EXCLUDED.open_source    ELSE t.open_source END,
    last_at        = EXCLUDED.last_at,
    last_bid       = EXCLUDED.last_bid,
    last_ask       = EXCLUDED.last_ask,
    last_depth_usd = EXCLUDED.last_depth_usd,
    last_source    = EXCLUDED.last_source,
    kickoff        = EXCLUDED.kickoff,
    listed_at      = COALESCE(t.listed_at, EXCLUDED.listed_at),
    n_changes      = t.n_changes + 1
WHERE (t.open_at IS NULL AND EXCLUDED.open_at IS NOT NULL)
   OR t.last_bid IS DISTINCT FROM EXCLUDED.last_bid
   OR t.last_ask IS DISTINCT FROM EXCLUDED.last_ask
   OR t.kickoff  IS DISTINCT FROM EXCLUDED.kickoff
"""


def to_record(r: dict, now: datetime) -> tuple:
    opened = is_open_quote(r["bid"], r["ask"])
    full = {**r,
            "first_seen_at": now, "first_bid": r["bid"], "first_ask": r["ask"],
            "open_at": now if opened else None,
            "open_bid": r["bid"] if opened else None,
            "open_ask": r["ask"] if opened else None,
            "open_depth_usd": r.get("depth_usd") if opened else None,
            "open_source": r["source"] if opened else None,
            "last_at": now, "last_bid": r["bid"], "last_ask": r["ask"],
            "last_depth_usd": r.get("depth_usd"), "last_source": r["source"]}
    return tuple(full.get(c) for c in COLS)


def dedupe(rows: list[dict]) -> list[dict]:
    """One row per (venue, market_key) — an ON CONFLICT batch may not touch the
    same key twice. The last one wins; a key repeated on one board is the same
    market listed under two events, so its quote is the same."""
    seen: dict = {}
    for r in rows:
        seen[(r["venue"], r["market_key"])] = r
    return list(seen.values())


def write(rows: list[dict], now: datetime) -> int:
    import psycopg2.extras
    import db_txn
    from db_pool import ingest_url

    conn = db_txn.connect(ingest_url())
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, UPSERT, [to_record(r, now) for r in rows],
                                           page_size=1000)
        return len(rows)
    finally:
        conn.close()


# ── the cycle ────────────────────────────────────────────────────────────────

def collect(now: datetime) -> list[dict]:
    rows: list[dict] = []
    t = time.time()
    evs = fetch_pm_soccer(now)
    pm = pm_soccer_rows(evs, now)
    log.info(f"polymarket soccer: {len(evs)} events -> {len(pm)} markets ({time.time() - t:.1f}s)")
    rows += pm
    k = load_board("kalshi-soccer", now)
    if k:
        ks = kalshi_soccer_rows(k, now)
        log.info(f"kalshi soccer: {len(k.get('fixtures') or [])} fixtures -> {len(ks)} markets")
        rows += ks
    for s in US_SPORTS:
        b = load_board(f"sport-{s}", now)
        if b:
            sr = sport_board_rows(s, b, now)
            log.info(f"{s}: {len(b.get('games') or [])} games -> {len(sr)} markets")
            rows += sr
    return dedupe(rows)


def run_once(dry_run: bool = False) -> int:
    now = datetime.now(timezone.utc)
    rows = collect(now)
    opened = sum(1 for r in rows if is_open_quote(r["bid"], r["ask"]))
    log.info(f"{len(rows)} markets, {opened} with an open-grade quote now")
    if dry_run:
        for r in rows[:5]:
            log.info(f"  {r['venue']} {r['sport']} {r['event_title']} | {r['family']} "
                     f"{r['line']} {r['outcome']} | {r['bid']}/{r['ask']}")
        return len(rows)
    return write(rows, now)


REPORT = """
SELECT venue, sport,
       count(*)                                                     AS markets,
       count(open_at)                                               AS opened,
       count(*) FILTER (WHERE listed_at IS NOT NULL
                          AND first_seen_at - listed_at < interval '1 hour') AS seen_from_listing,
       round(avg(extract(epoch FROM kickoff - open_at) / 3600)::numeric, 1) AS open_lead_h,
       round(avg(abs((last_bid + last_ask) / 2 - (open_bid + open_ask) / 2)) FILTER
             (WHERE open_at IS NOT NULL AND last_bid IS NOT NULL AND last_ask IS NOT NULL
                AND kickoff < now()) * 100, 2)                      AS mean_abs_move_pp
  FROM venue_market_quotes
 GROUP BY 1, 2 ORDER BY 1, 2
"""


def report() -> None:
    import db_txn
    from db_pool import ingest_url
    conn = db_txn.connect(ingest_url())
    try:
        with conn.cursor() as cur:
            cur.execute(REPORT)
            cols = [d[0] for d in cur.description]
            print(" | ".join(cols))
            for row in cur.fetchall():
                print(" | ".join("" if v is None else str(v) for v in row))
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.report:
        report()
        return
    n = run_once(dry_run=a.dry_run)
    log.info(f"done: {n} markets {'seen' if a.dry_run else 'written'}")


if __name__ == "__main__":
    main()
