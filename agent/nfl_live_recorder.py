"""
nfl_live_recorder.py — record NFL in-play: every Polymarket market of every live game,
the CLOB book of the main ones, and ESPN's game state + win probability. DATA ONLY.

Forward-only by construction: Polymarket serves no price history for closed markets,
so a Sunday not recorded is a Sunday lost. What this makes answerable later:
  * in-play pricing — PM's moneyline against ESPN's per-play win probability
    (which already carries the pre-game spread), and how PM reacts to scores,
    turnovers and the end of each quarter;
  * settlement windows — NFL decides ~163 of ~381 markets per game while the game
    is still running (end of Q1, half time, end of Q3); is the decided side still
    quoted below 1, for how long, at what depth?
  * garbage time — how cheap a near-certain side trades in the last minutes.

Files, not the database (the Supabase project is over its quota): one gzip member
per run appended to agent/data/nfl_live/YYYY-MM-DD.jsonl.gz — `zcat` reads them.

    python nfl_live_recorder.py --once     # cron, every minute; exits fast when no game is on
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "nfl_live")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
GAME_SLUG = re.compile(r"^nfl-[a-z]{2,4}-[a-z]{2,4}-\d{4}-\d{2}-\d{2}$")

PRE_MIN = 15                  # start recording this long before kick-off
POST_H = 5.0                  # keep recording this long after kick-off (post-whistle window)
BOOK_FAMILIES = ("moneyline", "spreads", "totals", "first_half_moneyline",
                 "first_half_spreads", "first_half_totals")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def pm_games(now: datetime) -> list[dict]:
    """Main game events kicking off inside the recording window — open AND closed,
    because the post-whistle window is exactly when an event flips to closed."""
    out = {}
    for closed in ("false", "true"):
        try:
            r = requests.get(f"{GAMMA}/events", params={
                "tag_slug": "nfl", "closed": closed, "limit": 100,
                "end_date_min": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
                "end_date_max": (now + timedelta(days=1)).strftime("%Y-%m-%d")}, timeout=25)
            batch = r.json() if r.ok else []
        except Exception:                                   # noqa: BLE001
            batch = []
        for e in batch if isinstance(batch, list) else []:
            ko = _ts(e.get("startTime"))
            if not GAME_SLUG.match(e.get("slug") or "") or ko is None:
                continue
            if ko - timedelta(minutes=PRE_MIN) <= now <= ko + timedelta(hours=POST_H):
                out[e["slug"]] = e
    return list(out.values())


def compact_markets(e: dict) -> list[list]:
    """[conditionId, family, line, question, outcome0, bid, ask, last, liquidity, closed]."""
    rows = []
    for m in e.get("markets") or []:
        outs = m.get("outcomes")
        try:
            outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
        except ValueError:
            outs = []
        rows.append([m.get("conditionId"), m.get("sportsMarketType"), _f(m.get("line")),
                     m.get("question"), outs[0] if outs else None, _f(m.get("bestBid")),
                     _f(m.get("bestAsk")), _f(m.get("lastTradePrice")),
                     round(_f(m.get("liquidityNum")) or 0), bool(m.get("closed"))])
    return rows


def main_tokens(e: dict) -> dict[str, str]:
    """token0 of the most liquid market in each BOOK_FAMILY — read from the CLOB,
    because Gamma's quote lags the book and in-play that lag is the whole question."""
    best: dict[str, tuple] = {}
    for m in e.get("markets") or []:
        fam = m.get("sportsMarketType")
        if fam not in BOOK_FAMILIES or m.get("closed"):
            continue
        liq = _f(m.get("liquidityNum")) or 0
        try:
            tok = json.loads(m.get("clobTokenIds") or "[]")[0]
        except (ValueError, IndexError):
            continue
        if fam not in best or liq > best[fam][0]:
            best[fam] = (liq, tok, m.get("conditionId"))
    return {v[1]: f"{fam}|{v[2]}" for fam, v in best.items()}


def clob_books(tokens: dict[str, str]) -> dict[str, dict]:
    if not tokens:
        return {}
    try:
        r = requests.post(f"{CLOB}/books", json=[{"token_id": t} for t in tokens], timeout=20)
        books = r.json() if r.ok else []
    except Exception:                                       # noqa: BLE001
        books = []
    out = {}
    for b in books if isinstance(books, list) else []:
        bids = sorted(((_f(x["price"]), _f(x["size"])) for x in b.get("bids") or []), reverse=True)[:5]
        asks = sorted((_f(x["price"]), _f(x["size"])) for x in b.get("asks") or [])[:5]
        out[str(b.get("asset_id"))] = {"key": tokens.get(str(b.get("asset_id"))),
                                       "bids": bids, "asks": asks, "ts": b.get("timestamp")}
    return out


def espn_board() -> list[dict]:
    try:
        d = requests.get(ESPN, timeout=20).json()           # no custom User-Agent: ESPN 403s them
    except Exception:                                       # noqa: BLE001
        return []
    out = []
    for ev in d.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        st = ev.get("status") or {}
        sit = comp.get("situation") or {}
        lp = sit.get("lastPlay") or {}
        out.append({
            "id": ev.get("id"), "date": ev.get("date"), "state": (st.get("type") or {}).get("name"),
            "period": st.get("period"), "clock": st.get("displayClock"),
            "home": (teams.get("home") or {}).get("team", {}).get("displayName"),
            "away": (teams.get("away") or {}).get("team", {}).get("displayName"),
            "hs": _f((teams.get("home") or {}).get("score")), "as": _f((teams.get("away") or {}).get("score")),
            "poss": sit.get("possession"), "down": sit.get("down"), "dist": sit.get("distance"),
            "yard": sit.get("yardLine"), "redzone": sit.get("isRedZone"),
            "play_id": lp.get("id"), "play": (lp.get("text") or "")[:160],
            "wp_home": _f((lp.get("probability") or {}).get("homeWinPercentage")),
        })
    return out


def run_once() -> int:
    now = datetime.now(timezone.utc)
    games = pm_games(now)
    if not games:
        return 0
    tokens: dict[str, str] = {}
    for e in games:
        tokens.update(main_tokens(e))
    rec = {"ts": now.isoformat(), "espn": espn_board(), "books": clob_books(tokens),
           "pm": [{"slug": e["slug"], "title": e.get("title"), "start": e.get("startTime"),
                   "live": e.get("live"), "score": e.get("score"), "period": e.get("period"),
                   "elapsed": e.get("elapsed"), "ended": e.get("ended"),
                   "teams": [(t.get("name"), t.get("ordering")) for t in e.get("teams") or []],
                   "markets": compact_markets(e)} for e in games]}
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{now:%Y-%m-%d}.jsonl.gz")
    with open(path, "ab") as fh, gzip.GzipFile(fileobj=fh, mode="wb") as gz:
        gz.write((json.dumps(rec, separators=(",", ":")) + "\n").encode())
    return len(games)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true")
    ap.parse_args()
    lock = open(os.path.join(HERE, ".nfl_live_recorder.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    n = run_once()
    if n:
        print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} recorded {n} games", flush=True)


if __name__ == "__main__":
    sys.exit(main())
