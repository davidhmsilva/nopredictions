#!/usr/bin/env python3
"""
Price Reader — the daemon (paper). The rules are in price_reader.py; the daily
review that changes which of them trade is price_reader_review.py.

One cycle, every CYCLE_S seconds:
  1. Read every sports event Polymarket lists that starts in the next 3 hours or
     started in the last 8 (Gamma, ~400 events, ~12,000 open markets, ~3 s).
  2. Remember each token's mid (the move arms need the last 10-60 minutes), and
     read Polymarket's public trade tape since the last cycle (data-api
     /v2/trades, every taker fill on the venue, newest first, a few seconds
     late when the first page is read fresh: see fetch_tape),
     keeping the fills on those tokens: who bought which side, and how much.
  3. Ask every arm, active or shadow, which token it would buy in each event,
     on the listing price. One position per arm per event, a daily cap per arm.
  4. Read the real CLOB books of those tokens and of every flip position due to
     sell, in batches (POST /books, 500 books in ~0.5 s).
  5. Sell what is due, write what passes the book, and every SETTLE_EVERY_S
     settle what has a winner and fill the start price of pre-match positions.

No connection is held across a network call (db_txn): the database is opened
for the reads, closed, the network is asked, and it is opened again to write.

    python price_reader_agent.py --seed            # create the arms (idempotent)
    python price_reader_agent.py --once --dry-run  # one cycle, nothing written
    python price_reader_agent.py                   # the daemon
    python price_reader_agent.py --settle          # settle + closes only
    python price_reader_agent.py --report          # each arm's record
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_txn  # noqa: E402
import price_reader as pr  # noqa: E402

log = logging.getLogger("price_reader")

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TAPE = "https://data-api.polymarket.com/v2/trades"
DATABASE_URL = os.getenv("DATABASE_URL")

CYCLE_S = 60
SETTLE_EVERY_S = 600
MAX_BOOKS_PER_CYCLE = 1000
BOOK_CHUNK = 400
RETRY_AFTER_SKIP_MIN = 10        # a token whose book failed the gate is not re-read sooner
SETTLE_BATCH = 200               # markets asked for a winner per settle pass
TAPE_PAGE = 1000                 # fills per page (the API's maximum)
TAPE_PAGES = 10                  # pages per cycle: ~1,000 fills is ~85 s of the venue
TAPE_WARMUP_MIN = 30             # after a start, read the tape this far back…
TAPE_WARMUP_PAGES = 40           # …in at most this many pages
VOID_AFTER_D = 3                 # a closed market with no winner this long after the start is void
LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".price_reader.lock")


def _conn():
    """The transaction pooler: this daemon connects per cycle and lets go, so it
    never holds one of the session pooler's 15 slots the other daemons need."""
    return db_txn.connect(DATABASE_URL.replace(":5432/", ":6543/"))


# ── the network ──────────────────────────────────────────────────────────────

def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_events(session: requests.Session, now: datetime) -> list[dict]:
    """Every open sports event with a start in [now − 8h, now + 3h]. Gamma's
    `endDate` equals the start on a game (gamma_api_traps), so the window is put
    on it. Pages of 100; a page that fails is retried in pages of 20 (one heavy
    event can 522 a page of 100). Offsets past 2,000 are refused by Gamma."""
    base = {"tag_slug": "sports", "closed": "false",
            "end_date_min": _iso(now - timedelta(hours=pr.POST_HORIZON_H)),
            "end_date_max": _iso(now + timedelta(minutes=pr.PRE_HORIZON_MIN))}
    out: list[dict] = []
    off = 0
    while off < 2000:
        page = _events_page(session, base, off, 100)
        if page is None:
            page = []
            for sub in range(off, off + 100, 20):
                part = _events_page(session, base, sub, 20)
                if part is None:
                    raise RuntimeError(f"Gamma events failed at offset {sub}")
                page += part
                if len(part) < 20:
                    break
        out += page
        if len(page) < 100:
            break
        off += 100
    else:
        log.warning("[price_reader] 2,000 events in the window: the rest were not read")
    return out


def _events_page(session, base: dict, offset: int, limit: int) -> list | None:
    try:
        r = session.get(f"{GAMMA}/events", params={**base, "limit": limit, "offset": offset}, timeout=30)
        if r.status_code != 200:
            return None
        body = r.json()
        return body if isinstance(body, list) else None
    except (requests.RequestException, ValueError):
        return None


def read_books(session: requests.Session, tokens: list[str]) -> dict[str, dict]:
    """{token: parsed book} for every token the CLOB answered for. A token
    missing from the answer has no book (it is not quoted)."""
    out: dict[str, dict] = {}
    for i in range(0, len(tokens), BOOK_CHUNK):
        chunk = tokens[i:i + BOOK_CHUNK]
        try:
            r = session.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk], timeout=30)
            if r.status_code != 200:
                log.warning(f"[price_reader] /books {r.status_code} for {len(chunk)} tokens")
                continue
            for b in r.json() or []:
                tok = str(b.get("asset_id") or "")
                if tok:
                    out[tok] = pr.parse_book(b)
        except (requests.RequestException, ValueError) as exc:
            log.warning(f"[price_reader] /books failed: {exc}")
    return out


def fetch_tape(session: requests.Session, since_ts: int, max_pages: int) -> tuple[list[dict], bool]:
    """(fills, complete): every taker fill on Polymarket back to `since_ts`,
    newest first, following `next_cursor`. `complete` is False when the pages
    ran out before reaching back that far (a gap: the flow is undercounted)
    or a page failed."""
    out: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        params = {"taker_only": "true", "limit": TAPE_PAGE}
        if cursor:
            params["cursor"] = cursor
        else:
            # The first page's plain URL is a CDN copy kept 5 minutes
            # (cache-control max-age=300): read through it, the newest fill is
            # 2-7 minutes old and arrives in 5-minute lumps. A URL of its own
            # each cycle is read at the source, a few seconds old (2026-11-07).
            # Still one request a cycle; the cursor pages are new URLs anyway.
            params["_"] = str(int(time.time()))
        try:
            r = session.get(TAPE, params=params, timeout=30)
            if r.status_code != 200:
                return out, False
            body = r.json()
        except (requests.RequestException, ValueError):
            return out, False
        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return out, False
        out += rows
        cursor = (body.get("pagination") or {}).get("next_cursor")
        oldest = min((int(x.get("timestamp") or 0) for x in rows), default=0)
        if not rows or not cursor or oldest < since_ts:
            return out, True
    return out, False


def clob_market(session: requests.Session, condition_id: str) -> dict | None:
    """{'closed': bool, 'winners': {token: won} or None} from the CLOB, None
    when it could not be read."""
    try:
        d = session.get(f"{CLOB}/markets/{condition_id}", timeout=15).json()
    except (requests.RequestException, ValueError):
        return None
    toks = d.get("tokens") or []
    winners = None
    if toks and any(t.get("winner") for t in toks):
        winners = {str(t.get("token_id")): bool(t.get("winner")) for t in toks}
    return {"closed": bool(d.get("closed")), "winners": winners}


# ── the database ─────────────────────────────────────────────────────────────

def strategy_id(conn) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (pr.STRATEGY_NAME,))
        row = cur.fetchone()
    return row[0] if row else None


def ensure_arms(conn) -> int:
    """Insert every arm of the code that the table does not hold yet. Never
    changes an existing arm: its mode is the review's, its params are fixed."""
    added = 0
    with conn.cursor() as cur:
        for a in pr.seed_arms():
            cur.execute("""INSERT INTO price_reader_arms (key, base_key, label, params, mode, note)
                           VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                           ON CONFLICT (key) DO NOTHING""",
                        (a["key"], a["base_key"], a["label"], json.dumps(a["params"]), a["mode"],
                         "Started trading on day one." if a["mode"] == "active"
                         else "Watched from day one; books only on evidence."))
            added += cur.rowcount
    return added


def load_arms(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, key, base_key, label, params, mode, daily_cap, excluded_sports
                         FROM price_reader_arms WHERE mode IN ('active', 'shadow')
                        ORDER BY (mode = 'active') DESC, id""")
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if isinstance(r["params"], str):
            r["params"] = json.loads(r["params"])
    return rows


def already(conn) -> tuple[set, Counter, set]:
    """(arm, event) pairs already entered in the window, entries per arm today
    (UTC), and the tokens an open BOOKED position already holds — so two arms
    never book the same token twice on the tab (the second is shadow)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT arm_id, event_id FROM price_reader_positions
                        WHERE event_start > now() - interval '30 hours'""")
        pairs = {(a, e) for a, e in cur.fetchall()}
        cur.execute("""SELECT arm_id, count(*) FROM price_reader_positions
                        WHERE entered_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'
                        GROUP BY arm_id""")
        today = Counter(dict(cur.fetchall()))
        cur.execute("""SELECT token_id FROM price_reader_positions
                        WHERE status = 'open' AND booked""")
        held = {t for (t,) in cur.fetchall()}
    return pairs, today, held


def due_flips(conn, now: datetime) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, arm_key, booked, paper_trade_id, token_id, shares, stake,
                              exit_due_at, event_start
                         FROM price_reader_positions
                        WHERE status = 'open' AND exit_rule = 'flip' AND exit_due_at <= %s""", (now,))
        return [dict(r) for r in cur.fetchall()]


def write_position(conn, sid: int | None, arm: dict, c: pr.Candidate, book: dict,
                   booked: bool, now: datetime) -> int:
    q, p = c.quote, arm["params"]
    ask = float(book["best_ask"])
    shares = pr.shares_for(pr.STAKE_UNITS, ask)
    due = pr.exit_due(p, now)
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            ptid = None
            if booked and sid is not None:
                meta = {"event_title": q.event_title, "event_slug": q.event_slug, "sport": q.sport,
                        "league": q.league, "token_id": q.token_id, "outcome": q.outcome,
                        "agent": "price_reader", "arm": arm["key"]}
                cur.execute("""
                    INSERT INTO pm_markets (platform, external_id, title, market_type,
                                            resolution_time, status, raw_metadata, ingested_at)
                    VALUES ('polymarket', %s, %s, %s, %s, 'active', %s::jsonb, now())
                    ON CONFLICT (platform, external_id) DO UPDATE
                       SET title = COALESCE(pm_markets.title, EXCLUDED.title)
                    RETURNING id""",
                    (q.condition_id, q.question or q.event_title, q.family, q.event_start,
                     json.dumps(meta)))
                market_id = cur.fetchone()[0]
                when = (f"{-c.phase.minutes_from_start:.0f} min before the start" if c.phase.phase == "pre"
                        else f"{c.phase.minutes_from_start:.0f} min after the start" if c.phase.phase == "live"
                        else "after the final whistle")
                exit_txt = (f"Sold at the bid {p.get('hold_min')} minutes later." if p.get("exit") == "flip"
                            else "Held to the end.")
                seen = c.why.split(", ", 1)[1] if ", " in c.why and c.why.startswith(q.label) else ""
                reasoning = (
                    f"Price Reader · {arm['label']}. {q.label}, {q.event_title} ({q.sport}), {when}. "
                    + (f"What the market did: {seen}. " if seen else "")
                    + f"Bought at the CLOB ask {ask:.3f} (bid {book['best_bid']:.3f}, ask depth "
                    f"${book['ask_depth_usd']:.0f}). "
                    f"Odds {pr.net_odds(ask):.3f} net of the {pr.FEE_RATE:.0%}·p(1−p) taker fee. "
                    f"{exit_txt} Only the price was read. 1u, PAPER.")
                cur.execute("""
                    INSERT INTO paper_trades (strategy_id, market_id, outcome, entry_price, entry_odds,
                                              stake_units, reasoning, confidence, pm_token_id, pm_live,
                                              placed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'paper', %s, false, now()) RETURNING id""",
                    (sid, market_id, f"{q.label} — {q.event_title}", ask, round(pr.net_odds(ask), 4),
                     pr.STAKE_UNITS, reasoning, q.token_id))
                ptid = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO price_reader_positions
                  (arm_id, arm_key, booked, paper_trade_id, event_id, event_slug, event_title,
                   event_start, sport, league, phase, minutes_from_start, condition_id, token_id,
                   market_type, family, question, outcome, label, gamma_ask, entry_ask, entry_bid,
                   ask_depth_usd, move_pp, shares, fee_rate, stake, exit_rule, exit_due_at, entered_at, signal)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (arm_id, event_id) DO NOTHING
                RETURNING id""",
                (arm["id"], arm["key"], ptid is not None, ptid, q.event_id, q.event_slug, q.event_title,
                 q.event_start, q.sport, q.league, c.phase.phase, round(c.phase.minutes_from_start, 1),
                 q.condition_id, q.token_id, q.market_type, q.family, q.question, q.outcome, q.label,
                 q.ask, ask, book.get("best_bid"), book.get("ask_depth_usd"),
                 round(c.move_pp * 100, 2) if c.move_pp is not None else None,
                 round(shares, 6), pr.FEE_RATE, pr.STAKE_UNITS, p.get("exit", "hold"), due, now,
                 json.dumps(c.signal) if c.signal is not None else None))
            row = cur.fetchone()
            if row is None:                       # entered by a concurrent run: undo the trade
                raise _AlreadyIn()
            return row[0]


class _AlreadyIn(Exception):
    pass


def close_position(conn, pos: dict, *, status: str, payout: float, reason: str,
                   bid: float | None = None) -> None:
    """Close a position and, when booked, its paper trade. A paper trade is
    'won' when it paid more than its stake, 'lost' otherwise, 'void' when the
    market resolved without a winner (its P&L stays out of every total)."""
    stake = float(pos["stake"])
    pl = payout - stake
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""UPDATE price_reader_positions
                              SET status = %s, exit_at = now(), exit_bid = %s, exit_reason = %s,
                                  payout = %s, pl = %s
                            WHERE id = %s AND status = 'open'""",
                        (status, bid, reason, round(payout, 6), round(pl, 6), pos["id"]))
            if pos.get("paper_trade_id") and cur.rowcount:
                result = "void" if status == "void" else ("won" if payout > stake else "lost")
                cur.execute("""UPDATE paper_trades SET result = %s, payout_units = %s,
                                      resolved_at = now(), closing_price = COALESCE(%s, closing_price)
                                WHERE id = %s AND result IS NULL""",
                            (result, round(payout, 6), bid, pos["paper_trade_id"]))


# ── the cycle ────────────────────────────────────────────────────────────────

class State:
    def __init__(self):
        self.memory = pr.PriceMemory()
        self.flow = pr.FlowMemory()
        self.skip_until: dict = {}          # (arm_id, event_id) → when to look again
        self.last_settle: float = 0.0
        self.session = requests.Session()


def cycle(state: State, dry_run: bool = False) -> dict:
    now = pr.now_utc()
    t0 = time.time()
    events = fetch_events(state.session, now)
    qbe = {str(ev.get("id")): pr.quotes_of(ev) for ev in events}
    state.memory.observe([q for qs in qbe.values() for q in qs], now)
    watch = {q.token_id for qs in qbe.values() for q in qs}
    now_ts = int(now.timestamp())
    first = state.flow.last_ts == 0
    since = now_ts - TAPE_WARMUP_MIN * 60 if first else state.flow.last_ts - 120
    fills, complete = fetch_tape(state.session, since, TAPE_WARMUP_PAGES if first else TAPE_PAGES)
    added = state.flow.add(fills, watch)
    state.flow.prune(now_ts)
    if not complete:
        log.warning(f"[price_reader] tape: {len(fills)} fills read, did not reach back to the last cycle")
    for k in [k for k, t in state.skip_until.items() if t <= now]:
        del state.skip_until[k]

    conn = _conn()
    try:
        sid = strategy_id(conn)
        arms = load_arms(conn)
        pairs, today, held = already(conn)
        due = due_flips(conn, now)
    finally:
        conn.close()

    cands: list[tuple[dict, pr.Candidate]] = []
    for arm in arms:
        left = int(arm["daily_cap"]) - today.get(arm["id"], 0)
        if left <= 0:
            continue
        for c in pr.arm_candidates(arm["params"], events, state.memory, now, qbe, state.flow):
            key = (arm["id"], c.quote.event_id)
            if key in pairs or key in state.skip_until:
                continue
            cands.append((arm, c))
            left -= 1
            if left <= 0:
                break

    tokens = list(dict.fromkeys([p["token_id"] for p in due] + [c.quote.token_id for _, c in cands]))
    asked = set(tokens[:MAX_BOOKS_PER_CYCLE])
    books = read_books(state.session, list(asked))

    stats = Counter(events=len(events), quotes=sum(len(v) for v in qbe.values()), fills=added,
                    arms=len(arms), candidates=len(cands), books=len(books), due=len(due))
    if dry_run:
        for arm, c in cands:
            v = pr.entry_verdict(arm["params"], books.get(c.quote.token_id))
            stats["would_enter" if v is None else "would_skip"] += 1
            if v is None:
                log.info(f"[dry] {arm['key']:<32} {c.quote.sport:<12} {c.why} — {c.quote.event_title}")
        log.info(f"[price_reader] DRY {dict(stats)} in {time.time() - t0:.1f}s")
        return dict(stats)

    conn = _conn()
    try:
        for pos in due:
            # One position that cannot be closed never stops the cycle: on
            # 2026-11-06 a NameError here halted every entry and settle for 15 h.
            try:
                _exit_one(conn, pos, books.get(pos["token_id"]), now, stats)
            except Exception:
                stats["exit_errors"] += 1
                log.exception(f"[price_reader] exit of position {pos.get('id')} failed")
        for arm, c in cands:
            book = books.get(c.quote.token_id)
            if c.quote.token_id not in asked:
                continue                                   # read next cycle
            v = pr.entry_verdict(arm["params"], book)
            if v is not None:
                stats["skipped"] += 1
                state.skip_until[(arm["id"], c.quote.event_id)] = now + timedelta(minutes=RETRY_AFTER_SKIP_MIN)
                continue
            # Never both sides of one market on the tab: the second is watched.
            booked = (arm["mode"] == "active"
                      and not pr.excluded(arm["excluded_sports"], c.quote.sport, c.phase.phase)
                      and c.quote.token_id not in held and c.quote.other_token not in held)
            try:
                write_position(conn, sid, arm, c, book, booked, now)
            except _AlreadyIn:
                continue
            except psycopg2.Error as exc:
                log.warning(f"[price_reader] {arm['key']} {c.quote.event_title}: not written ({exc})")
                continue
            pairs.add((arm["id"], c.quote.event_id))
            if booked:
                held.add(c.quote.token_id)
            stats["booked" if booked else "shadow"] += 1
            log.info(f"[price_reader] {'BOOK' if booked else 'shadow':<6} {arm['key']:<32} "
                     f"{c.quote.sport:<12} {c.why} — {c.quote.event_title}")
    finally:
        conn.close()

    if time.time() - state.last_settle >= SETTLE_EVERY_S:
        state.last_settle = time.time()
        try:
            stats["settled"] = settle(state.session)
            stats["closes"] = fill_closes(state.session)
        except (psycopg2.Error, requests.RequestException) as exc:
            log.warning(f"[price_reader] settle failed: {exc}")
    log.info(f"[price_reader] {dict(stats)} in {time.time() - t0:.1f}s")
    return dict(stats)


def _exit_one(conn, pos: dict, book: dict | None, now: datetime, stats: Counter) -> None:
    """Sell a flip position at the bid. With no sellable book, try again next
    cycle; pr.EXIT_GIVE_UP_MIN after it was due, hold it to the end instead."""
    why = pr.sale_verdict(book)
    if why is None:
        bid = float(book["best_bid"])
        payout = float(pos["shares"]) * pr.sale_per_share(bid)
        close_position(conn, pos, status="sold", payout=payout, reason="sold_on_time", bid=bid)
        stats["sold"] += 1
        return
    if now - pos["exit_due_at"] > timedelta(minutes=pr.EXIT_GIVE_UP_MIN):
        with conn.cursor() as cur:
            cur.execute("""UPDATE price_reader_positions
                              SET exit_rule = 'hold', exit_reason = %s WHERE id = %s AND status = 'open'""",
                        (f"no sale ({why}); held to the end", pos["id"]))
        stats["held_no_bid"] += 1


def settle(session: requests.Session | None = None) -> int:
    """Pay out every open held position whose market has a winner (the CLOB
    `winner` flag of the token bought, never "the first outcome"). A market
    closed with no winner VOID_AFTER_D days after the start is void, at 0.5 a
    share — the venue's 50-50."""
    session = session or requests.Session()
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT condition_id, min(event_start) AS start
                             FROM price_reader_positions
                            WHERE status = 'open' AND exit_rule = 'hold'
                              AND event_start < now() - interval '30 minutes'
                            GROUP BY condition_id ORDER BY 2 LIMIT %s""", (SETTLE_BATCH,))
            todo = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    if not todo:
        return 0
    answers = {t["condition_id"]: (clob_market(session, t["condition_id"]), t["start"]) for t in todo}
    n = 0
    conn = _conn()
    try:
        now = pr.now_utc()
        for cid, (m, start) in answers.items():
            if m is None:
                continue
            if m["winners"] is None and not (m["closed"] and now - start > timedelta(days=VOID_AFTER_D)):
                continue
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""SELECT id, token_id, shares, stake, paper_trade_id
                                 FROM price_reader_positions
                                WHERE condition_id = %s AND status = 'open' AND exit_rule = 'hold'""", (cid,))
                rows = [dict(r) for r in cur.fetchall()]
            for pos in rows:
                if m["winners"] is None:
                    close_position(conn, pos, status="void", payout=float(pos["shares"]) * 0.5,
                                   reason="closed without a winner")
                elif pos["token_id"] in m["winners"]:
                    won = m["winners"][pos["token_id"]]
                    close_position(conn, pos, status="settled",
                                   payout=pr.settle_payout(float(pos["shares"]), won),
                                   reason="won" if won else "lost")
                else:
                    continue
                n += 1
    finally:
        conn.close()
    log.info(f"[price_reader] settled {n} positions on {len(todo)} markets asked")
    return n


def fill_closes(session: requests.Session | None = None, limit: int = 150) -> int:
    """Polymarket's last 1-minute price at the start, for positions bought
    before it — the agent's CLV on its own venue (pm_close). Booked positions
    also write it on their paper trade as pm_clv."""
    import pm_close
    session = session or requests.Session()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT token_id, event_start FROM price_reader_positions
                            WHERE phase = 'pre' AND close_price IS NULL
                              AND event_start < now() - make_interval(mins => %s)
                              AND event_start > now() - make_interval(days => %s)
                            ORDER BY event_start LIMIT %s""",
                        (pm_close.SETTLE_LAG_MIN, pm_close.HISTORY_DAYS, limit))
            todo = cur.fetchall()
    finally:
        conn.close()
    found = []
    for tok, start in todo:
        hit = pm_close.close_from_history(tok, int(start.timestamp()), session=session)
        if hit is not None:
            found.append((tok, start, datetime.fromtimestamp(hit[0], timezone.utc), hit[1]))
    if not found:
        return 0
    conn = _conn()
    try:
        with db_txn.atomic(conn):
            with conn.cursor() as cur:
                for tok, start, at, price in found:
                    cur.execute("""UPDATE price_reader_positions
                                      SET close_price = %s, clv = round(%s::numeric / entry_ask - 1, 5)
                                    WHERE token_id = %s AND event_start = %s AND phase = 'pre'
                                      AND close_price IS NULL""",
                                (round(price, 4), price, tok, start))
                    cur.execute("""UPDATE paper_trades pt
                                      SET pm_closing_price = %s, pm_closing_at = %s,
                                          pm_clv = round(%s::numeric / pt.entry_price - 1, 5),
                                          pm_clv_source = 'prices_history_1m'
                                     FROM price_reader_positions p
                                    WHERE p.paper_trade_id = pt.id AND p.token_id = %s
                                      AND p.event_start = %s AND p.phase = 'pre'
                                      AND pt.pm_closing_price IS NULL AND pt.entry_price > 0""",
                                (round(price, 4), at, price, tok, start))
    finally:
        conn.close()
    return len(found)


# ── the report ───────────────────────────────────────────────────────────────

def report() -> None:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT a.key, a.mode, a.daily_cap,
                                  count(p.id) AS n,
                                  count(p.id) FILTER (WHERE p.status = 'open') AS open,
                                  count(p.id) FILTER (WHERE p.booked) AS booked,
                                  array_agg(p.pl::float8) FILTER (WHERE p.status IN ('sold', 'settled')) AS pls,
                                  array_agg(p.stake::float8) FILTER (WHERE p.status IN ('sold', 'settled')) AS stakes,
                                  avg(p.clv)::float8 AS clv
                             FROM price_reader_arms a
                             LEFT JOIN price_reader_positions p ON p.arm_id = a.id
                            GROUP BY a.id ORDER BY a.base_key, a.id""")
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    print(f"{'arm':<40} {'mode':<7} {'cap':>4} {'pos':>5} {'open':>5} {'book':>5} "
          f"{'settled':>7} {'P&L':>8} {'yield':>8} {'90% CI':>17} {'CLV':>7}")
    for r in rows:
        rec = pr.record(r["pls"] or [], r["stakes"] or [])
        y = f"{rec['yield'] * 100:+.1f}%" if rec["yield"] is not None else "—"
        ci = (f"[{rec['lo90'] * 100:+.1f},{rec['hi90'] * 100:+.1f}]" if rec["lo90"] is not None else "—")
        clv = f"{r['clv'] * 100:+.2f}%" if r["clv"] is not None else "—"
        print(f"{r['key']:<40} {r['mode']:<7} {r['daily_cap']:>4} {r['n']:>5} {r['open']:>5} "
              f"{r['booked']:>5} {rec['n']:>7} {rec['pl']:>+8.2f} {y:>8} {ci:>17} {clv:>7}")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--interval", type=int, default=CYCLE_S)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
    if not DATABASE_URL:
        log.error("DATABASE_URL missing")
        return 1
    if args.report:
        report()
        return 0
    if args.seed:
        conn = _conn()
        try:
            log.info(f"[price_reader] {ensure_arms(conn)} arms added")
        finally:
            conn.close()
        return 0
    if args.settle:
        s = requests.Session()
        settle(s)
        fill_closes(s)
        return 0

    state = State()
    if args.once:
        cycle(state, dry_run=args.dry_run)
        return 0

    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("[price_reader] another instance holds the lock; exiting")
        return 0
    conn = _conn()
    try:
        ensure_arms(conn)
    finally:
        conn.close()
    log.info("[price_reader] daemon up")
    while True:
        started = time.time()
        try:
            cycle(state, dry_run=args.dry_run)
        except Exception as exc:                          # noqa: BLE001 — the daemon must not die on one cycle
            log.exception(f"[price_reader] cycle failed: {exc}")
        time.sleep(max(5.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
