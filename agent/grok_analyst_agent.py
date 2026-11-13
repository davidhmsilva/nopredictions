#!/usr/bin/env python3
"""grok_analyst_agent.py — the daemon (paper).

One cycle every CYCLE_S:
  1. Pull open sports/esports events from Gamma in a short horizon.
  2. Build a compact board of markets with real CLOB books (ask, depth, spread).
  3. Ask Grok (xAI) which tokens to buy and at what stake.
  4. Re-check book quality for each proposed stake; only paper those that pass.
  5. Write paper_trades under the Grok Analyst strategy.

Requires XAI_API_KEY and DATABASE_URL. Paper only.

    python grok_analyst_agent.py --once --dry-run
    python grok_analyst_agent.py --once
    python grok_analyst_agent.py          # loop
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_txn  # noqa: E402
import grok_analyst as ga  # noqa: E402

log = logging.getLogger("grok_analyst")

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
XAI_URL = "https://api.x.ai/v1/chat/completions"
DATABASE_URL = os.getenv("DATABASE_URL")
XAI_API_KEY = os.getenv("XAI_API_KEY")

CYCLE_S = 300          # 5 min
HORIZON_MIN = 180      # look 3h ahead
POST_H = 2             # and a little live
MAX_OPPS = 60


def _conn():
    return db_txn.connect(DATABASE_URL.replace(":5432/", ":6543/"))


def strategy_id(conn) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (ga.STRATEGY_NAME,))
        row = cur.fetchone()
    return row[0] if row else None


def fetch_events(session: requests.Session, now: datetime) -> list[dict]:
    params = {
        "tag_slug": "sports",
        "closed": "false",
        "end_date_min": (now - timedelta(hours=POST_H)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date_max": (now + timedelta(minutes=HORIZON_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 100,
    }
    out = []
    for off in range(0, 800, 100):
        try:
            r = session.get(f"{GAMMA}/events", params={**params, "offset": off}, timeout=25)
            if r.status_code != 200:
                break
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            out += batch
            if len(batch) < 100:
                break
        except Exception as exc:  # noqa: BLE001
            log.warning(f"gamma: {exc}")
            break
    return out


def sport_of(event: dict) -> str:
    slugs = [str(t.get("slug") or "").lower() for t in event.get("tags") or [] if isinstance(t, dict)]
    for tag in ("esports", "soccer", "nfl", "nba", "mlb", "nhl", "tennis", "ufc", "mma"):
        if tag in slugs:
            return tag
    return "other"


def read_books(session: requests.Session, tokens: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(tokens), 200):
        chunk = tokens[i:i + 200]
        try:
            r = session.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk], timeout=30)
            if r.status_code != 200:
                continue
            for b in r.json() or []:
                tok = str(b.get("asset_id") or "")
                if not tok:
                    continue
                asks = sorted(b.get("asks") or [], key=lambda x: float(x.get("price") or 1))
                bids = sorted(b.get("bids") or [], key=lambda x: -float(x.get("price") or 0))
                if not asks or not bids:
                    continue
                ask = float(asks[0]["price"])
                bid = float(bids[0]["price"])
                depth = sum(float(x.get("price") or 0) * float(x.get("size") or 0) for x in asks[:5])
                out[tok] = {"ask": ask, "bid": bid, "spread": ask - bid, "ask_depth_usd": depth}
        except Exception as exc:  # noqa: BLE001
            log.warning(f"books: {exc}")
    return out


def build_opportunities(events: list[dict], books: dict, now: datetime) -> list[ga.Opportunity]:
    opps = []
    for ev in events:
        start = None
        try:
            st = ev.get("startTime")
            if st:
                start = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
        except ValueError:
            pass
        mins = (start - now).total_seconds() / 60.0 if start else None
        sport = sport_of(ev)
        for m in ev.get("markets") or []:
            if m.get("closed"):
                continue
            tokens = m.get("clobTokenIds") or m.get("tokens") or []
            outcomes = m.get("outcomes") or []
            for i, tok in enumerate(tokens):
                tok = str(tok)
                book = books.get(tok)
                if not book:
                    continue
                outcome = outcomes[i] if i < len(outcomes) else "?"
                opps.append(ga.Opportunity(
                    event_id=str(ev.get("id") or ""),
                    event_title=ev.get("title") or "",
                    sport=sport,
                    league=ev.get("seriesSlug"),
                    start=start,
                    minutes_to_start=mins,
                    condition_id=str(m.get("conditionId") or ""),
                    token_id=tok,
                    question=m.get("question") or "",
                    outcome=str(outcome),
                    family=m.get("sportsMarketType") or "other",
                    ask=book["ask"],
                    bid=book["bid"],
                    spread=book["spread"],
                    ask_depth_usd=book["ask_depth_usd"],
                ))
    # prefer tighter books and nearer starts
    opps.sort(key=lambda o: (o.spread, abs(o.minutes_to_start or 999)))
    return opps[:MAX_OPPS]


def call_grok(prompt: str) -> str:
    if not XAI_API_KEY:
        raise RuntimeError("XAI_API_KEY not set")
    r = requests.post(
        XAI_URL,
        headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "grok-3",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 1200,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def recent_pl(conn) -> float | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM(payout_units - stake_units), 0)
              FROM paper_trades pt
              JOIN strategies s ON s.id = pt.strategy_id
             WHERE s.name = %s AND pt.result IN ('won', 'lost')
               AND pt.resolved_at > now() - interval '7 days'
        """, (ga.STRATEGY_NAME,))
        row = cur.fetchone()
    return float(row[0]) if row else None


def write_trade(conn, sid: int, opp: ga.Opportunity, stake: float, reasoning: str, now: datetime) -> int | None:
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pm_markets (platform, external_id, title, market_type, resolution_time, status, raw_metadata, ingested_at)
                VALUES ('polymarket', %s, %s, %s, %s, 'active', %s::jsonb, now())
                ON CONFLICT (platform, external_id) DO UPDATE SET title = COALESCE(pm_markets.title, EXCLUDED.title)
                RETURNING id
            """, (opp.condition_id, opp.question or opp.event_title, opp.family, opp.start,
                  json.dumps({"event_title": opp.event_title, "sport": opp.sport, "agent": "grok_analyst"})))
            market_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO paper_trades
                  (strategy_id, market_id, outcome, entry_price, entry_odds, stake_units,
                   reasoning, confidence, pm_token_id, pm_live, placed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'paper', %s, false, %s)
                RETURNING id
            """, (
                sid, market_id,
                f"{opp.outcome} — {opp.event_title}",
                opp.ask, round(ga.net_odds(opp.ask), 4), stake,
                f"Grok Analyst. {reasoning} Stake ${stake:.0f}. Book: ask {opp.ask:.3f}, depth ${opp.ask_depth_usd:.0f}, spread {opp.spread:.3f}.",
                opp.token_id, now,
            ))
            return cur.fetchone()[0]


def cycle(session: requests.Session, dry_run: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    events = fetch_events(session, now)
    # collect candidate tokens
    tokens = []
    for ev in events:
        for m in ev.get("markets") or []:
            if not m.get("closed"):
                tokens.extend(str(t) for t in (m.get("clobTokenIds") or m.get("tokens") or []))
    tokens = list(dict.fromkeys(tokens))[:400]
    books = read_books(session, tokens)
    opps = build_opportunities(events, books, now)
    log.info(f"events={len(events)} books={len(books)} opps={len(opps)}")

    if not opps:
        return {"trades": 0, "reason": "no opportunities"}

    conn = _conn()
    try:
        sid = strategy_id(conn)
        if sid is None:
            log.error(f"strategy '{ga.STRATEGY_NAME}' not found — create it first")
            return {"trades": 0, "reason": "no strategy"}
        pl = recent_pl(conn)
        prompt = ga.build_prompt(opps, pl)
        raw = call_grok(prompt)
        proposed = ga.parse_trades(raw)
        log.info(f"Grok proposed {len(proposed)} trades")

        written = 0
        by_token = {o.token_id: o for o in opps}
        for t in proposed:
            opp = by_token.get(t["token_id"])
            if not opp:
                continue
            stake = min(t["stake_usd"], ga.MAX_STAKE_USD)
            if not ga.can_fill(opp, stake):
                log.info(f"skip {t['token_id']}: book cannot fill ${stake:.0f}")
                continue
            if dry_run:
                log.info(f"DRY {opp.event_title} {opp.outcome} ${stake:.0f} — {t['reasoning'][:80]}")
                written += 1
                continue
            tid = write_trade(conn, sid, opp, stake, t["reasoning"], now)
            if tid:
                written += 1
                log.info(f"papered #{tid} {opp.event_title} {opp.outcome} ${stake:.0f}")
        return {"trades": written, "proposed": len(proposed)}
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    session = requests.Session()
    if args.once:
        print(cycle(session, args.dry_run))
        return
    while True:
        try:
            cycle(session)
        except Exception as exc:  # noqa: BLE001
            log.exception(f"cycle failed: {exc}")
        time.sleep(CYCLE_S)


if __name__ == "__main__":
    main()
