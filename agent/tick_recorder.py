"""
tick_recorder.py — high-frequency Polymarket order-book capture for football.

WHY THIS EXISTS
---------------
On 2026-07-21 every pre-match edge we could measure died the same death: the
signal was ~0.5-2pp while the round-trip spread cost ~1-2.5pp. Direction was
sometimes predictable (PM-rich-vs-Pinnacle tokens moved down 88% of the time)
but the magnitude never covered the spread. The one place where price moves
dwarf the spread is in-play — a goal repricies 1x2 by 20-50pp.

We were blind there: market_observations polls every ~30 min. This records
top-of-book every ~60s so the in-play microstructure is finally measurable.

DESIGN
------
PM books and live fixture state go into SEPARATE tables (pm_ticks,
live_fixture_ticks) and are reconciled offline by team name. A name-matching
miss must never cost us the underlying data — that is what crippled the
observer (see the endDate/live-match coverage bug).

Only tokens with a live two-sided book are polled. Of ~3,900 football tokens
listed, ~140 have a real book — polling the rest is pure waste.

USAGE
-----
    python tick_recorder.py                     # run forever, 60s book cycle
    python tick_recorder.py --once              # single cycle (smoke test)
    python tick_recorder.py --interval 30       # faster book polling
    python tick_recorder.py --dry-run           # no DB writes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg2
import requests
from psycopg2.extras import Json, execute_batch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dc_scanner import _fetch_pm_events  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [tick_recorder] %(message)s", datefmt="%H:%M:%S"
)
# dc_scanner calls basicConfig at import time and wins the root config, which
# would relabel our lines as [dc_scanner]. Own handler keeps the prefix honest.
log = logging.getLogger(__name__)
log.propagate = False
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter("%(asctime)s [tick_recorder] %(message)s", "%H:%M:%S"))
log.addHandler(_h)
log.setLevel(logging.INFO)

DATABASE_URL = os.getenv("DATABASE_URL")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
CLOB_BOOK = "https://clob.polymarket.com/book"

BOOK_INTERVAL_S = 60        # how often to snapshot PM books
# api-football is a HARD constraint: the daily request quota is shared with the
# existing crons and is routinely exhausted. Score/minute is a nice-to-have here,
# not a dependency — a >8pp jump in a 1x2 token inside one 60s cycle IS a goal,
# so the book alone reconstructs match events. Keep this poll cheap (144/day).
FIXTURE_INTERVAL_S = 600
REFRESH_MARKETS_S = 900     # how often to re-pull the PM market universe
EVENT_WINDOW_H = 5          # keep tokens for matches within +-5h of now
MAX_TOKENS = 260            # hard cap on tokens polled per cycle
DEPTH_LEVELS = 5


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── PM market universe ───────────────────────────────────────────────────────

def _candidate_tokens() -> list[dict]:
    """Football tokens for matches within +-EVENT_WINDOW_H hours of now.

    Gamma only exposes events by end-date, so we pull a 2-day window and filter
    on endDate locally. '<team> vs <team>' in the title is our football filter;
    _fetch_pm_events already drops women's competitions.
    """
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(hours=EVENT_WINDOW_H), now + timedelta(hours=EVENT_WINDOW_H)

    out: list[dict] = []
    for event in _fetch_pm_events(days_ahead=2):
        title = event.get("title", "")
        if " vs" not in title:
            continue
        for mkt in event.get("markets", []):
            if mkt.get("closed") or not mkt.get("clobTokenIds"):
                continue
            end = _parse_ts(mkt.get("endDate") or event.get("endDate"))
            if end and not (lo <= end <= hi):
                continue
            try:
                token_ids = json.loads(mkt["clobTokenIds"])
                outcomes = json.loads(mkt.get("outcomes") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            for i, tid in enumerate(token_ids):
                out.append({
                    "token_id": tid,
                    "condition_id": mkt.get("conditionId"),
                    "event_title": title,
                    "question": mkt.get("question"),
                    "outcome": outcomes[i] if i < len(outcomes) else None,
                    "end_date": end,
                    "volume": _f(mkt.get("volumeNum"), 0.0),
                })

    out.sort(key=lambda t: -(t["volume"] or 0))
    return out[:MAX_TOKENS * 3]   # book probe will thin this down further


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Book snapshots ───────────────────────────────────────────────────────────

def _fetch_book(tok: dict) -> dict | None:
    """Top-of-book + depth for one token. None when the book is one-sided."""
    try:
        resp = requests.get(CLOB_BOOK, params={"token_id": tok["token_id"]}, timeout=10)
        if resp.status_code != 200:
            return None
        book = resp.json()
    except Exception:
        return None

    bids = sorted(book.get("bids") or [], key=lambda x: -_f(x["price"], 0))[:DEPTH_LEVELS]
    asks = sorted(book.get("asks") or [], key=lambda x: _f(x["price"], 1))[:DEPTH_LEVELS]
    if not bids or not asks:
        return None

    lv = lambda side: [[_f(x["price"]), _f(x["size"])] for x in side]  # noqa: E731
    notional = lambda side: sum(_f(x["price"], 0) * _f(x["size"], 0) for x in side)  # noqa: E731

    return {
        **tok,
        "best_bid": _f(bids[0]["price"]),
        "best_ask": _f(asks[0]["price"]),
        "bid_depth_usd": notional(bids),
        "ask_depth_usd": notional(asks),
        "bid_levels": lv(bids),
        "ask_levels": lv(asks),
    }


def _snapshot_books(tokens: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=16) as pool:
        rows = [r for r in pool.map(_fetch_book, tokens) if r]
    return rows


def _write_books(conn, rows: list[dict]) -> None:
    execute_batch(conn.cursor(), """
        INSERT INTO pm_ticks (token_id, condition_id, event_title, question, outcome,
                              best_bid, best_ask, bid_depth_usd, ask_depth_usd,
                              bid_levels, ask_levels, end_date)
        VALUES (%(token_id)s, %(condition_id)s, %(event_title)s, %(question)s, %(outcome)s,
                %(best_bid)s, %(best_ask)s, %(bid_depth_usd)s, %(ask_depth_usd)s,
                %(bid_levels)s, %(ask_levels)s, %(end_date)s)
    """, [{**r, "bid_levels": Json(r["bid_levels"]), "ask_levels": Json(r["ask_levels"])}
          for r in rows], page_size=200)
    conn.commit()


# ── Live fixture state ───────────────────────────────────────────────────────

def _fetch_live_fixtures() -> list[dict]:
    """All in-play football from api-football. No model filtering — record raw."""
    if not FOOTBALL_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"live": "all"},
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            timeout=12,
        )
        if resp.status_code != 200:
            log.warning(f"api-football HTTP {resp.status_code}")
            return []
        data = resp.json()
    except Exception as exc:
        log.warning(f"api-football error: {exc}")
        return []

    out = []
    for fix in data.get("response", []):
        teams, goals = fix.get("teams", {}), fix.get("goals", {})
        info, status = fix.get("fixture", {}), fix.get("fixture", {}).get("status", {})
        home = teams.get("home", {}).get("name")
        away = teams.get("away", {}).get("name")
        if not home or not away:
            continue

        reds_h = reds_a = 0
        for ev in fix.get("events", []) or []:
            if ev.get("type") == "Card" and ev.get("detail") == "Red Card":
                name = ev.get("team", {}).get("name", "")
                if name == home:
                    reds_h += 1
                elif name == away:
                    reds_a += 1

        out.append({
            "fixture_id": info.get("id"),
            "home": home, "away": away,
            "league": (fix.get("league") or {}).get("name"),
            "status": status.get("short"),
            "elapsed": status.get("elapsed"),
            "home_goals": goals.get("home"), "away_goals": goals.get("away"),
            "home_reds": reds_h, "away_reds": reds_a,
        })
    return out


def _write_fixtures(conn, rows: list[dict]) -> None:
    execute_batch(conn.cursor(), """
        INSERT INTO live_fixture_ticks (fixture_id, home, away, league, status, elapsed,
                                        home_goals, away_goals, home_reds, away_reds)
        VALUES (%(fixture_id)s, %(home)s, %(away)s, %(league)s, %(status)s, %(elapsed)s,
                %(home_goals)s, %(away_goals)s, %(home_reds)s, %(away_reds)s)
    """, rows, page_size=200)
    conn.commit()


# ── Main loop ────────────────────────────────────────────────────────────────

def run(interval: int, once: bool, dry_run: bool) -> None:
    conn = None if dry_run else _conn()
    tokens: list[dict] = []
    live_tokens: list[dict] = []
    last_markets = last_fixtures = 0.0

    while True:
        cycle_start = time.time()

        if cycle_start - last_markets > REFRESH_MARKETS_S or not tokens:
            tokens = _candidate_tokens()
            live_tokens = []          # force a full re-probe against the new universe
            last_markets = cycle_start
            log.info(f"universe refreshed → {len(tokens)} candidate tokens")

        # Probe everything periodically; between probes poll only tokens that
        # actually had a two-sided book, which is ~4% of the listed universe.
        probe = live_tokens or tokens
        rows = _snapshot_books(probe[:MAX_TOKENS] if live_tokens else probe)
        live_tokens = [{k: r[k] for k in
                        ("token_id", "condition_id", "event_title", "question",
                         "outcome", "end_date", "volume")} for r in rows]

        if rows and not dry_run:
            _write_books(conn, rows)

        fixtures = []
        if cycle_start - last_fixtures > FIXTURE_INTERVAL_S:
            fixtures = _fetch_live_fixtures()
            last_fixtures = cycle_start
            if fixtures and not dry_run:
                _write_fixtures(conn, fixtures)

        tight = sum(1 for r in rows if (r["best_ask"] - r["best_bid"]) <= 0.02)
        log.info(
            f"books={len(rows):3d} (tight<=2pp {tight:3d})  live_fixtures={len(fixtures):3d}  "
            f"{time.time() - cycle_start:.1f}s"
        )

        if once:
            break
        time.sleep(max(1.0, interval - (time.time() - cycle_start)))

    if conn:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="High-frequency PM football tick recorder")
    ap.add_argument("--interval", type=int, default=BOOK_INTERVAL_S,
                    help=f"seconds between book snapshots (default {BOOK_INTERVAL_S})")
    ap.add_argument("--once", action="store_true", help="single cycle then exit")
    ap.add_argument("--dry-run", action="store_true", help="no DB writes")
    args = ap.parse_args()

    if not DATABASE_URL and not args.dry_run:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    try:
        run(args.interval, args.once, args.dry_run)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
