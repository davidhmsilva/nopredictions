"""
nba_maker.py — NBA Every Game, maker bids. PAPER, no orders.

The maker arm of nba_agent, by the operator's decision (2026-10-25). Taking
the ask on the first 36 FORCED NBA bets cost -4.2%; the same tokens bought at
the bid, with no fee, would have been -1.9%. But those bids would have been
placed 36 minutes before tip-off on deep alternate spreads, and only 8 of 36
traded at the bid before the game (2 traded through it). So this arm:

  1. Bids EARLY: on the first cycle a game is inside 24h of tip-off and its
     book passes the gates, never inside MIN_POST_MIN.
  2. Bids on the markets that trade: the full-game MONEYLINE and the MAIN
     SPREAD (the spread whose mid sits nearest 0.50, inside 0.40-0.60; ties go
     to the deeper market).
  3. Bids BOTH sides of each. Most nights there is no sharp line to pick a
     side with, and a pair needs none: the fill rate and the adverse selection
     are measured per side, with no model in the way.
  4. Holds a FIXED price: one tick above the best bid when the spread is at
     least three ticks (so the two sides never meet), else at the best bid.

A resting bid FILLS only when a trade prints strictly BELOW it, read as its
Yes-equivalent price: a trade on the other token at q is this token at 1 - q.
A print exactly at the bid, or the ask coming down to it, depends on the
queue, so it is only recorded (`touched_at`). A fill writes a 1u paper trade at
the bid, no fee. Unfilled bids expire at tip-off and stay in nba_maker_orders.
Every order, filled or not, gets the close (the CLOB's price history at the
last point at or before tip-off) and Polymarket's resolution, so filled vs
expired measures adverse selection directly.

No Odds API credits are spent. When nba_agent already holds a sharp snapshot
for the game, its fair value is stored beside the bid for later analysis only.

It runs inside nba_agent (`--once`, `--settle`, `--report`), under its lock.

    python nba_agent.py --once --dry-run   # also logs the bids this arm would post
    python nba_maker.py --report
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import psycopg2.extras
import requests

import db_txn
import nba_agent as na

log = logging.getLogger("nba_maker")

DATA_API = "https://data-api.polymarket.com"

STRATEGY_NAME = "NBA Every Game — maker bids"
OBS_VERSION = 1

HORIZON_MIN = 24 * 60         # bids are posted from 24h out
MIN_POST_MIN = 15             # a game first seen this close to tip-off gets no bid
MAIN_MID = (0.40, 0.60)       # the main spread's mid must sit in here
MAX_SPREAD = na.MAX_SPREAD    # 3c, on the token we bid, as nba_agent gates its board
MIN_LIQUIDITY_USD = na.MIN_LIQUIDITY_USD
MIN_BID, MAX_BID = na.MIN_ASK, na.MAX_ASK
STAKE_U = 1.0
TRADES_PAGE = 500


# ── what is bid on ───────────────────────────────────────────────────────────

def _legs(m: dict) -> list[dict] | None:
    """The market's two tokens with their Gamma quote, or None when it is not
    a clean two-outcome book. Outcome 0 trades at bestBid/bestAsk; outcome 1
    is its mirror."""
    outs, toks = na._jl(m.get("outcomes")), na._jl(m.get("clobTokenIds"))
    bb, ba = na._f(m.get("bestBid")), na._f(m.get("bestAsk"))
    if len(outs) != 2 or len(toks) != 2 or bb is None or ba is None or ba <= bb:
        return None
    return [{"label": outs[0], "token_id": str(toks[0]), "bid": bb, "ask": ba},
            {"label": outs[1], "token_id": str(toks[1]), "bid": 1 - ba, "ask": 1 - bb}]


def _open(m: dict) -> bool:
    return not (m.get("closed") or m.get("active") is False or m.get("acceptingOrders") is False)


def pick_markets(game: na.Game) -> list[dict]:
    """The moneyline and the main spread, each as {market_type, line_of, legs,
    ...}, sides resolved to canonical clubs. Anything ambiguous is dropped."""
    ml, spreads = None, []
    for m in game.markets:
        fam = str(m.get("sportsMarketType") or "").lower()
        if fam not in ("moneyline", "spreads") or not _open(m):
            continue
        legs = _legs(m)
        if legs is None:
            continue
        sides = [game.team(lg["label"]) for lg in legs]
        if set(sides) != {game.home, game.away}:
            continue
        for lg, s in zip(legs, sides):
            lg["side"] = s
        q = m.get("question") or ""
        base = {"question": q, "condition_id": m.get("conditionId") or "", "legs": legs,
                "liquidity": na._f(m.get("liquidityNum")) or 0.0,
                "tick": na._f(m.get("orderPriceMinTickSize")) or 0.01}
        if fam == "moneyline":
            if ml is not None:
                return []                     # two moneylines on one game: not ours to guess
            ml = {**base, "market_type": "moneyline", "lines": [None, None]}
            continue
        sm = na.SPREAD_Q.match(q)
        ln = na._f(m.get("line"))
        if not sm or ln is None or abs(float(sm.group(2)) - ln) > 1e-9:
            continue
        if game.team(sm.group(1)) != legs[0]["side"]:
            continue                          # the question's team must be outcome 0
        mid = 0.5 * (legs[0]["bid"] + legs[0]["ask"])
        if MAIN_MID[0] <= mid <= MAIN_MID[1]:
            spreads.append({**base, "market_type": "spreads", "lines": [ln, -ln], "mid": mid})
    out = [ml] if ml else []
    if spreads:
        out.append(min(spreads, key=lambda s: (abs(s["mid"] - 0.5), -s["liquidity"])))
    return out


def book_of(raw: dict | None) -> dict | None:
    """Best bid/ask and top-five notional from a CLOB book, or None when a side is empty."""
    if not raw:
        return None
    bids = sorted(raw.get("bids") or [], key=lambda x: -(na._f(x.get("price")) or 0))
    asks = sorted(raw.get("asks") or [], key=lambda x: (na._f(x.get("price")) or 1))
    if not bids or not asks:
        return None
    notional = lambda side: sum((na._f(x["price"]) or 0) * (na._f(x["size"]) or 0) for x in side[:5])  # noqa: E731
    bid, ask = na._f(bids[0]["price"]), na._f(asks[0]["price"])
    if bid is None or ask is None or ask <= bid:
        return None
    return {"bid": bid, "ask": ask, "bid_depth": notional(bids), "ask_depth": notional(asks)}


def bid_price(book: dict, tick: float) -> float | None:
    """Our fixed paper bid. One tick above the best bid only when the spread
    is at least three ticks: both sides of a market are bid, and at two ticks
    the improved pair would sit at the same Yes-equivalent price."""
    spread = book["ask"] - book["bid"]
    if spread > MAX_SPREAD + 1e-9:
        return None
    p = book["bid"] + tick if spread >= 3 * tick - 1e-9 else book["bid"]
    p = round(round(p / tick) * tick, 6)
    if not (MIN_BID <= p <= MAX_BID) or p >= book["ask"] - 1e-9:
        return None
    return p


def fill_from(trades: list[dict], token_id: str, price: float, since_ts: float,
              until_ts: float) -> tuple[float | None, float | None, float | None]:
    """(fill time, fill price, first touch time) for a resting bid at `price`,
    from trades in (since_ts, until_ts]. Strictly below the bid is a fill: the
    book traded through our level. Exactly at it is only a touch."""
    fill_t = fill_p = touch_t = None
    for t in sorted(trades, key=lambda x: na._f(x.get("timestamp")) or 0):
        ts, px = na._f(t.get("timestamp")), na._f(t.get("price"))
        if ts is None or px is None or ts <= since_ts or ts > until_ts:
            continue
        yes_px = px if str(t.get("asset")) == token_id else round(1.0 - px, 6)
        if yes_px < price - 1e-9:
            fill_t, fill_p = ts, yes_px
            break
        if abs(yes_px - price) < 1e-9 and touch_t is None:
            touch_t = ts
    return fill_t, fill_p, touch_t


def close_from(history: list[dict], ko_ts: float) -> tuple[float | None, float | None]:
    """(price, time) of the last price-history point at or before tip-off."""
    pts = [h for h in history if na._f(h.get("t")) is not None and na._f(h.get("p")) is not None
           and na._f(h["t"]) <= ko_ts]
    if not pts:
        return None, None
    last = max(pts, key=lambda h: na._f(h["t"]))
    return na._f(last["p"]), na._f(last["t"])


def maker_yield(payout: float, bid: float) -> float:
    """Return on 1u of cash bought at the bid, no fee."""
    return payout / bid - 1.0


# ── the network ──────────────────────────────────────────────────────────────

def fetch_books(tokens: list[str]) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for i in range(0, len(tokens), 400):
        chunk = tokens[i:i + 400]
        try:
            r = requests.post(f"{na.CLOB}/books", json=[{"token_id": t} for t in chunk], timeout=20)
            r.raise_for_status()
            for b in r.json() or []:
                out[str(b.get("asset_id"))] = book_of(b)
        except Exception as exc:                                     # noqa: BLE001
            log.warning(f"books: {exc}")
    return out


def fetch_trades(condition_id: str, start: int, end: int) -> list[dict] | None:
    """Every taker fill on the market in [start, end], paged backwards by time
    (`offset` stops at 10,000). None when the API did not answer: the order
    keeps its checkpoint and is read again next cycle."""
    out, hi = [], end
    for _ in range(40):
        try:
            r = requests.get(f"{DATA_API}/trades", params={"market": condition_id, "takerOnly": "true",
                                                            "start": start, "end": hi,
                                                            "limit": TRADES_PAGE}, timeout=20)
            r.raise_for_status()
            batch = r.json()
        except Exception as exc:                                     # noqa: BLE001
            log.warning(f"trades {condition_id[:10]}: {exc}")
            return None
        if not isinstance(batch, list) or not batch:
            break
        out += batch
        if len(batch) < TRADES_PAGE:
            break
        hi = int(min(na._f(x.get("timestamp")) or start for x in batch)) - 1
        if hi < start:
            break
        time.sleep(0.2)
    return out


def fetch_history(token_id: str, start: int, end: int) -> list[dict] | None:
    try:
        r = requests.get(f"{na.CLOB}/prices-history", params={"market": token_id, "startTs": start,
                                                             "endTs": end, "fidelity": 1}, timeout=20)
        r.raise_for_status()
        return r.json().get("history") or []
    except Exception as exc:                                         # noqa: BLE001
        log.warning(f"prices-history {token_id[:10]}: {exc}")
        return None


def clob_verdict(condition_id: str) -> dict[str, float] | None:
    """{token_id: payout} once the market has resolved, else None."""
    try:
        d = requests.get(f"{na.CLOB}/markets/{condition_id}", timeout=10).json()
    except Exception:                                                # noqa: BLE001
        return None
    toks = d.get("tokens") or []
    if any(t.get("winner") for t in toks):
        return {str(t["token_id"]): (1.0 if t.get("winner") else 0.0) for t in toks}
    if d.get("closed") and toks and all(abs((na._f(t.get("price")) or 0) - 0.5) < 0.01 for t in toks):
        return {str(t["token_id"]): 0.5 for t in toks}             # resolved 50-50
    return None


# ── the database ─────────────────────────────────────────────────────────────

def ensure_strategy(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
        row = cur.fetchone()
        if row:
            return row[0]
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO research_hypotheses (title, description, rationale, source, created_by)
                VALUES (%s, %s, %s, 'agent', 'agent') RETURNING id""", (
                "H-NBA-MAKER — resting bids on both sides of the NBA moneyline and main spread",
                "Fixed paper bids posted from 24h before tip-off on both sides of every NBA "
                "game's moneyline and main spread, filled only when a trade prints below the "
                "bid, return more than zero at the bid with no fee, and filled bids do not "
                "close or resolve worse than the bids that expire unfilled.",
                "The first 36 FORCED NBA bets cost -4.2% at the ask and would have cost -1.9% "
                "at the bid, but bids placed 36 min out on alternate spreads mostly never "
                "traded (8 of 36 touched, 2 traded through). The NFL board measured +0.60% "
                "at the bid against Pinnacle on exact lines. Adverse selection went -4.7% "
                "against soccer maker bids, so filled vs expired is the primary measurement: "
                "close - bid on tens to hundreds of fills, yield much later."))
            hyp = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO strategies (hypothesis_id, name, rules, source, run_status, theory,
                                        owner_id)
                VALUES (%s, %s, %s::jsonb, 'agent', 'running', %s,
                        (SELECT owner_id FROM strategies WHERE name = %s))
                RETURNING id""", (
                hyp, STRATEGY_NAME, json.dumps({
                    "venue": "polymarket", "sport": "nba", "phase": "paper-only",
                    "self_settling": True, "obs_version": OBS_VERSION, "kind": "maker",
                    "markets": ["moneyline", "main spread"], "sides": "both",
                    "horizon_min": HORIZON_MIN, "min_post_min": MIN_POST_MIN,
                    "fill_rule": "trade strictly below the bid", "stake_u": STAKE_U,
                }), "Rest fixed bids on both sides of every NBA game's moneyline and main spread "
                    "from 24h out, and measure what fills against what does not.", na.OWNER_FROM))
            sid = cur.fetchone()[0]
    log.info(f"created strategy '{STRATEGY_NAME}' id={sid}")
    return sid


def posted(conn, sid: int) -> set[tuple[str, str]]:
    """(game_slug, market_type) already bid on."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT game_slug, market_type FROM nba_maker_orders WHERE strategy_id = %s",
                    (sid,))
        return {(s, t) for s, t in cur.fetchall()}


def write_trade(conn, sid: int, o: dict, filled_at: datetime, fill_price: float) -> int:
    p = float(o["bid_price"])
    mins = (o["kickoff"] - filled_at).total_seconds() / 60
    label = f"{o['side']} ML" if o["market_type"] == "moneyline" else f"{o['side']} {float(o['line']):+g}"
    meta = {"game_slug": o["game_slug"], "home": o["home_team"], "away": o["away_team"],
            "token_id": o["token_id"], "side": o["side"], "family": o["market_type"],
            "line": None if o["line"] is None else float(o["line"]), "sport": "nba"}
    src = {"kind": "maker", "order_id": o["id"], "fill_rule": "traded_through",
           "fill_price": fill_price, "posted_minutes_to_ko": float(o["minutes_to_ko"]),
           "minutes_to_ko": round(mins, 1), "fair": None if o["fair"] is None else float(o["fair"]),
           "fair_source": o["fair_source"], "obs_version": OBS_VERSION}
    reasoning = (
        f"NBA MAKER BID — {o['title']}, tip-off {o['kickoff']:%Y-%m-%d %H:%M}Z. {label} "
        f"[{o['market_type']}]: bid {p:.3f} ({1 / p:.2f}) posted {float(o['minutes_to_ko']):.0f} min out "
        f"against a book of {float(o['pm_bid']):.3f}/{float(o['pm_ask']):.3f}; filled when a trade "
        f"printed at {fill_price:.3f}, {mins:.0f} min before tip-off. {STAKE_U}u, no fee (maker). PAPER.")
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pm_markets (platform, external_id, title, market_type, category,
                                        resolution_time, status, raw_metadata, ingested_at)
                VALUES ('polymarket', %s, %s, %s, 'nba', %s, 'active', %s::jsonb, now())
                ON CONFLICT (platform, external_id) DO UPDATE
                   SET title = EXCLUDED.title, resolution_time = EXCLUDED.resolution_time,
                       raw_metadata = COALESCE(pm_markets.raw_metadata, '{}'::jsonb) || EXCLUDED.raw_metadata
                RETURNING id""", (o["condition_id"], o["question"], f"nba_{o['market_type']}",
                                  o["kickoff"], json.dumps(meta)))
            mid = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO paper_trades (strategy_id, market_id, outcome, entry_price, entry_odds,
                    stake_units, model_probability, sharp_consensus_sources, expected_edge,
                    confidence, reasoning, pm_token_id, pm_live, placed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'maker', %s, %s, false, %s)
                RETURNING id""", (sid, mid, f"{label} — {o['title']}", p, round(1 / p, 4), STAKE_U,
                                  None if o["fair"] is None else float(o["fair"]), json.dumps(src),
                                  None if o["fair"] is None else round(float(o["fair"]) / p - 1, 5),
                                  reasoning, o["token_id"], filled_at))
            tid = cur.fetchone()[0]
            cur.execute("""UPDATE nba_maker_orders SET status = 'filled', status_at = now(),
                                  filled_at = %s, fill_price = %s, paper_trade_id = %s
                            WHERE id = %s""", (filled_at, fill_price, tid, o["id"]))
    return tid


# ── the cycle ────────────────────────────────────────────────────────────────

def _fair_of(game: na.Game, cache: dict | None) -> dict[str, tuple[float, str, datetime]]:
    """{token_id: (fair_raw, source, snapshot_at)} from nba_agent's own pricing,
    when a sharp snapshot is already in hand. Spends nothing."""
    sh = na.sharp_for(game, cache) if cache else None
    if sh is None:
        return {}
    return {c.token_id: (c.fair_raw, c.source, sh.snapshot_at)
            for c in na.candidates(game, sh) if c.source != "pm_mid"}


def plan_bids(games: list[na.Game], done: set[tuple[str, str]], now: datetime) -> list[tuple]:
    """(game, market) pairs due a bid this cycle: inside the horizon, not too
    late, not bid before, with a deep enough market."""
    out = []
    for g in games:
        mins = (g.kickoff - now).total_seconds() / 60
        if not (MIN_POST_MIN < mins <= HORIZON_MIN):
            continue
        for m in pick_markets(g):
            if (g.slug, m["market_type"]) in done or m["liquidity"] < MIN_LIQUIDITY_USD:
                continue
            out.append((g, m, mins))
    return out


def post_bids(conn, sid: int | None, games: list[na.Game], cache: dict | None, now: datetime,
              dry_run: bool) -> int:
    done = posted(conn, sid) if sid is not None and sid > 0 else set()
    due = plan_bids(games, done, now)
    if not due:
        return 0
    books = fetch_books([lg["token_id"] for _, m, _ in due for lg in m["legs"]])
    n = 0
    for g, m, mins in due:
        rows = []
        for lg, line in zip(m["legs"], m["lines"]):
            b = books.get(lg["token_id"])
            price = bid_price(b, m["tick"]) if b else None
            if price is None:
                break
            rows.append((lg, line, b, price))
        if len(rows) != 2:                    # both sides or neither: a pair is the design
            log.info(f"  maker {g.title:<28} {m['market_type']:<9} book fails the gates — next cycle")
            continue
        fair = _fair_of(g, cache)
        for lg, line, b, price in rows:
            f = fair.get(lg["token_id"])
            label = f"{lg['side']} ML" if line is None else f"{lg['side']} {line:+g}"
            log.info(f"  maker {g.title:<28} {mins:6.0f}m  BID {label:<30} {price:.3f} "
                     f"(book {b['bid']:.3f}/{b['ask']:.3f})"
                     + (f" fair {f[0]:.3f} {f[1]}" if f else ""))
            if dry_run:
                n += 1
                continue
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO nba_maker_orders (obs_version, strategy_id, game_slug, title, kickoff,
                        minutes_to_ko, home_team, away_team, market_type, line, side, condition_id,
                        token_id, question, tick, pm_bid, pm_ask, bid_depth_usd, ask_depth_usd,
                        liquidity_usd, bid_price, fair, fair_source, odds_snapshot_at,
                        trades_checked_until)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT (strategy_id, token_id) DO NOTHING""", (
                    OBS_VERSION, sid, g.slug, g.title, g.kickoff, round(mins, 1), g.home, g.away,
                    m["market_type"], line, lg["side"], m["condition_id"], lg["token_id"], m["question"],
                    m["tick"], b["bid"], b["ask"], round(b["bid_depth"], 2), round(b["ask_depth"], 2),
                    round(m["liquidity"], 2), price, None if not f else round(f[0], 5),
                    None if not f else f[1], None if not f else f[2]))
                n += cur.rowcount
    return n


def manage_open(conn, sid: int, now: datetime) -> None:
    """Fills, touches and expiry for resting bids; the close for every order
    whose game has started. All network reads happen before any write."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM nba_maker_orders WHERE strategy_id = %s
                          AND (status = 'resting' OR (close_price IS NULL
                               AND kickoff > now() - interval '3 days'))""", (sid,))
        orders = [dict(r) for r in cur.fetchall()]
    if not orders:
        return
    now_ts = now.timestamp()
    resting = [o for o in orders if o["status"] == "resting"]
    by_cid: dict[str, list[dict]] = {}
    for o in resting:
        by_cid.setdefault(o["condition_id"], []).append(o)
    for cid, os_ in by_cid.items():
        start = int(min(o["trades_checked_until"] or o["created_at"] for o in os_).timestamp())
        end = int(min(now_ts, max(o["kickoff"].timestamp() for o in os_)))
        trades = fetch_trades(cid, start, end) if end > start else []
        if trades is None:
            continue
        for o in os_:
            ko_ts = o["kickoff"].timestamp()
            since = (o["trades_checked_until"] or o["created_at"]).timestamp()
            until = min(now_ts, ko_ts)
            t, px, touch = fill_from(trades, o["token_id"], float(o["bid_price"]), since, until)
            if touch and not o["touched_at"]:
                with conn.cursor() as cur:
                    cur.execute("UPDATE nba_maker_orders SET touched_at = to_timestamp(%s) WHERE id = %s",
                                (touch, o["id"]))
            if t is not None:
                tid = write_trade(conn, sid, o, datetime.fromtimestamp(t, timezone.utc), px)
                log.info(f"  maker FILLED #{o['id']} {o['side']} {o['market_type']} @ "
                         f"{float(o['bid_price']):.3f} (print {px:.3f}) → pt#{tid}")
                continue
            with conn.cursor() as cur:
                if now_ts >= ko_ts:
                    cur.execute("""UPDATE nba_maker_orders SET status = 'expired', status_at = now(),
                                          trades_checked_until = %s WHERE id = %s AND status = 'resting'""",
                                (o["kickoff"], o["id"]))
                else:
                    cur.execute("UPDATE nba_maker_orders SET trades_checked_until = to_timestamp(%s) "
                                "WHERE id = %s", (until, o["id"]))

    # the close, filled or not, from the price history (any cycle cadence works)
    for o in orders:
        ko_ts = o["kickoff"].timestamp()
        if o["close_price"] is not None or now_ts < ko_ts + 60:
            continue
        hist = fetch_history(o["token_id"], int(ko_ts - 6 * 3600), int(ko_ts + 60))
        if hist is None:
            continue
        px, at = close_from(hist, ko_ts)
        if px is None:
            continue
        with db_txn.atomic(conn):
            with conn.cursor() as cur:
                cur.execute("""UPDATE nba_maker_orders SET close_price = %s, close_at = to_timestamp(%s)
                                WHERE id = %s""", (px, at, o["id"]))
                cur.execute("""UPDATE paper_trades pt SET pm_closing_price = %s, pm_closing_at = to_timestamp(%s),
                                      pm_clv = %s / o.bid_price - 1
                                 FROM nba_maker_orders o
                                WHERE o.id = %s AND pt.id = o.paper_trade_id""", (px, at, px, o["id"]))


def cycle(conn, games: list[na.Game], cache: dict | None, now: datetime, dry_run: bool = False) -> None:
    """One maker cycle, called from nba_agent.run_once with the games it has
    already put in scope (preseason excluded)."""
    sid = None
    if dry_run:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
            r = cur.fetchone()
            sid = r[0] if r else -1
    else:
        sid = ensure_strategy(conn)
        manage_open(conn, sid, now)
    n = post_bids(conn, sid, games, cache, now, dry_run)
    log.info(f"maker: {n} bids would be posted (dry run, nothing written)" if dry_run
             else f"maker: {n} bids posted")


# ── settlement and report ────────────────────────────────────────────────────

def settle(conn=None) -> int:
    conn = conn or na._conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT o.id, o.condition_id, o.token_id, o.paper_trade_id, o.bid_price,
                              pt.stake_units, pt.result
                         FROM nba_maker_orders o
                         JOIN strategies s ON s.id = o.strategy_id
                         LEFT JOIN paper_trades pt ON pt.id = o.paper_trade_id
                        WHERE s.name = %s AND o.payout IS NULL AND o.status <> 'resting'
                          AND o.kickoff < now() - interval '3 hours'""", (STRATEGY_NAME,))
        pending = [dict(r) for r in cur.fetchall()]
    verdicts = {cid: clob_verdict(cid) for cid in {p["condition_id"] for p in pending}}  # network, no txn
    n = 0
    for p in pending:
        v = verdicts.get(p["condition_id"])
        if not v or p["token_id"] not in v:
            continue
        pay = v[p["token_id"]]
        with db_txn.atomic(conn):
            with conn.cursor() as cur:
                cur.execute("UPDATE nba_maker_orders SET payout = %s WHERE id = %s", (pay, p["id"]))
                if p["paper_trade_id"] and p["result"] is None:
                    result = "won" if pay == 1.0 else "lost" if pay == 0.0 else "void"
                    payout = float(p["stake_units"]) * pay / float(p["bid_price"])
                    cur.execute("""UPDATE paper_trades SET result = %s, payout_units = %s, resolved_at = now()
                                    WHERE id = %s AND result IS NULL""", (result, round(payout, 4), p["paper_trade_id"]))
        n += 1
    log.info(f"maker: settled {n} of {len(pending)} orders")
    return n


def report(conn=None) -> None:
    conn = conn or na._conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT o.status, o.market_type, count(*) AS n, avg(o.bid_price) AS bid,
                              avg(o.minutes_to_ko) / 60 AS posted_h,
                              count(o.close_price) AS closed,
                              avg(o.close_price - o.bid_price) * 100 AS close_minus_bid_pp,
                              count(o.payout) AS settled,
                              avg(o.payout - o.bid_price) * 100 AS result_minus_bid_pp,
                              avg(o.payout / o.bid_price - 1) * 100 AS yield_pct,
                              count(o.touched_at) AS touched
                         FROM nba_maker_orders o JOIN strategies s ON s.id = o.strategy_id
                        WHERE s.name = %s GROUP BY 1, 2 ORDER BY 2, 1""", (STRATEGY_NAME,))
        rows = cur.fetchall()
    if not rows:
        print(f"{STRATEGY_NAME}: no orders yet")
        return
    nan = float("nan")
    print(f"\n{STRATEGY_NAME} — orders by status (pp of price; yield on 1u at the bid, no fee)")
    print(f"  {'market':<10}{'status':<9}{'n':>5}{'touched':>8}{'bid':>7}{'posted':>8}"
          f"{'close−bid':>10}{'settled':>8}{'result−bid':>11}{'yield':>8}")
    for r in rows:
        def v(k):
            return nan if r[k] is None else float(r[k])
        print(f"  {r['market_type']:<10}{r['status']:<9}{r['n']:>5}{r['touched']:>8}{v('bid'):>7.3f}"
              f"{v('posted_h'):>7.1f}h{v('close_minus_bid_pp'):>+10.2f}{r['settled']:>8}"
              f"{v('result_minus_bid_pp'):>+11.2f}{v('yield_pct'):>+7.1f}%")
    tot = {}
    for r in rows:
        tot[r["status"]] = tot.get(r["status"], 0) + r["n"]
    done = tot.get("filled", 0) + tot.get("expired", 0)
    if done:
        print(f"  fill rate {tot.get('filled', 0)}/{done} of the bids whose game has started.")
    print("  filled vs expired is the adverse-selection gap: bids that fill only when the price is "
          "going against them close and resolve below the bids that never fill. Close − bid on "
          "tens to hundreds of fills first; yield needs thousands.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [nba-maker] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", force=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--settle", action="store_true")
    a = ap.parse_args()
    if a.settle:
        settle()
    if a.report or not a.settle:
        report()


if __name__ == "__main__":
    main()
