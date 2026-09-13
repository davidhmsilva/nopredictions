"""
soccer_live_recorder.py — record EVERY Polymarket soccer market of every live game,
every minute, from the CLOB book. DATA ONLY, no orders.

Polymarket serves no price history for closed markets, so a match not recorded is a
match lost. The in-play tapes the project already had cover four families (the
next-goal over, 1st-half over 0.5, favourite leading at HT, decided markets);
Polymarket lists ~70 markets per game across sibling events — match odds, draw,
every totals line, BTTS (full / 1H / 2H), halftime and second-half result, exact
score, first team to score, team totals, corners, handicaps. This records all of
them, so any in-play strategy on any of those markets can be backtested on the
price it would really have paid (strategy factory: agent/factory/).

Measured 2026-09-13 18:21Z: ~51 games in the window, 334 events with siblings,
3,735 markets. POST /books returns 500 books in 0.46s, so the real CLOB book of
every market costs ~8 requests a minute — Gamma's quote lags the book, and
in-play that lag is the whole question.

Files (the Supabase project is over its quota), in agent/data/soccer_live/:
  YYYY-MM-DD.meta.jsonl.gz  one line per market the first time it is seen that day, with
                            its day index `i` (a day's files are self-contained: the
                            index resets daily)
  YYYY-MM-DD.jsonl.gz       one line per minute: game state + every market's book as rows
                            [i, bid, bid_size, ask, ask_size, bid_usd_top3, ask_usd_top3,
                             last, closed]
  outcomes.jsonl            how every market resolved (--settle)

Rows carry the day index, not the 66-character condition id: random hex does not
compress, and keyed by id the first snapshot was 186KB gzipped — ~100MB on a busy
Saturday. `iter_snapshots()` maps the rows back to condition ids.

The game state is Polymarket's own (live / score / period / elapsed, identical on
every sibling). Match statistics are NOT here: join `pressure_observations` on
event title and time — the pressure daemon already records them, and a second
api-football consumer is how the key has been drained before.

    python soccer_live_recorder.py --once            # cron, every minute
    python soccer_live_recorder.py --settle          # cron, every 30 minutes
    python soccer_live_recorder.py --summary [DATE]  # what a day's files hold
"""
from __future__ import annotations

import argparse
import collections
import fcntl
import gzip
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "soccer_live")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

PRE_MIN = 10              # start this long before kick-off
POST_MIN = 165            # until kick-off + 2h45: 90', the break, stoppage, and the post-whistle window
BOOK_BATCH = 500
SETTLE_AFTER_H = 4
SETTLE_GIVE_UP_H = 96

SUFFIX = re.compile(r"\s+-\s+(.+)$")
VS = re.compile(r"\bvs?\.?\s", re.I)


# ── pure helpers (tested) ────────────────────────────────────────────────────

def _ts(s) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def group_of(title: str) -> str:
    """'PSV vs. Sparta - Exact Score' → 'PSV vs. Sparta': siblings share a game."""
    return SUFFIX.sub("", title or "").strip()


def in_window(kickoff: Optional[datetime], now: datetime) -> bool:
    return kickoff is not None and (kickoff - timedelta(minutes=PRE_MIN)
                                    <= now <= kickoff + timedelta(minutes=POST_MIN))


def is_game_event(e: dict) -> bool:
    """A fixture (or one of its siblings) — not an outright like 'Premier League Winner'."""
    return bool(_ts(e.get("startTime"))) and bool(VS.search(group_of(e.get("title") or "") + " "))


def compact_book(b: dict) -> list:
    """[bid, bid_size, ask, ask_size, bid_usd_top3, ask_usd_top3]; None where a side is empty."""
    bids = sorted(((_f(x.get("price")), _f(x.get("size"))) for x in b.get("bids") or []),
                  key=lambda t: -(t[0] or 0))
    asks = sorted(((_f(x.get("price")), _f(x.get("size"))) for x in b.get("asks") or []),
                  key=lambda t: (t[0] if t[0] is not None else 9))
    usd = lambda side: round(sum((p or 0) * (s or 0) for p, s in side[:3]), 2)   # noqa: E731
    return [bids[0][0] if bids else None, round(bids[0][1] or 0, 2) if bids else 0,
            asks[0][0] if asks else None, round(asks[0][1] or 0, 2) if asks else 0,
            usd(bids), usd(asks)]


def market_meta(e: dict, m: dict, group: str) -> Optional[dict]:
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        outs = json.loads(m.get("outcomes") or "[]")
    except (TypeError, ValueError):
        return None
    if not m.get("conditionId") or not toks:
        return None
    return {"cid": m["conditionId"], "token0": str(toks[0]),
            "token1": str(toks[1]) if len(toks) > 1 else None, "group": group,
            "event_slug": e.get("slug"), "event_title": e.get("title"),
            "question": m.get("question"), "family": m.get("sportsMarketType"),
            "line": _f(m.get("line")), "outcomes": outs, "item": m.get("groupItemTitle"),
            "kickoff": e.get("startTime"), "neg_risk": bool(m.get("negRisk"))}


def outcome_of(m: dict) -> Optional[dict]:
    """How a closed market resolved: the index paid 1, or None for a 50-50. None
    while it has not resolved — a closed market is not yet a resolved one."""
    if not m.get("closed"):
        return None
    try:
        prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    except (TypeError, ValueError):
        return None
    if not prices:
        return None
    if max(prices) >= 0.99:
        return {"prices": prices, "winner": prices.index(max(prices))}
    if all(abs(p - 0.5) < 0.01 for p in prices):
        return {"prices": prices, "winner": None}
    return None                                  # closed but not final yet


# ── files ────────────────────────────────────────────────────────────────────

def _day_paths(day: str) -> tuple[str, str, str]:
    return (os.path.join(OUT_DIR, f"{day}.jsonl.gz"), os.path.join(OUT_DIR, f"{day}.meta.jsonl.gz"),
            os.path.join(OUT_DIR, f".seen-{day}.json"))


def _append_gz(path: str, lines: list) -> None:
    if not lines:
        return
    with open(path, "ab") as fh, gzip.GzipFile(fileobj=fh, mode="wb") as gz:
        gz.write(("\n".join(json.dumps(x, separators=(",", ":")) for x in lines) + "\n").encode())


def _read_json(path: str, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def iter_snapshots(day: str) -> Iterator[dict]:
    """Each minute, with q as {conditionId: [bid, bid_size, ask, ask_size,
    bid_usd_top3, ask_usd_top3, last, closed]}."""
    path = _day_paths(day)[0]
    if not os.path.exists(path):
        return
    cid_of = {m["i"]: cid for cid, m in load_meta(day).items() if "i" in m}
    with gzip.open(path, "rt") as fh:           # reads every appended gzip member
        for line in fh:
            if line.strip():
                s = json.loads(line)
                s["q"] = {cid_of[r[0]]: r[1:] for r in s["q"] if r[0] in cid_of}
                yield s


def load_meta(day: str) -> dict:
    path = _day_paths(day)[1]
    out: dict = {}
    if os.path.exists(path):
        with gzip.open(path, "rt") as fh:
            for line in fh:
                if line.strip():
                    m = json.loads(line)
                    out[m["cid"]] = m
    return out


def load_outcomes() -> dict:
    path = os.path.join(OUT_DIR, "outcomes.jsonl")
    out: dict = {}
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    o = json.loads(line)
                    out[o["cid"]] = o
    return out


# ── the network ──────────────────────────────────────────────────────────────

def fetch_window_events(now: datetime) -> list:
    """Every soccer game event (and sibling) inside the recording window — open AND
    closed, because the post-whistle window is exactly when events flip to closed.
    Bounded by plain DATES: a game's endDate is its kick-off, and Gamma's date
    filters misbehave on timestamps (lab_strategy_runner.fetch_upcoming)."""
    lo = (now - timedelta(minutes=POST_MIN + 60)).strftime("%Y-%m-%d")
    hi = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    out: dict = {}
    for closed in ("false", "true"):
        for off in range(0, 3000, 100):
            try:
                r = requests.get(f"{GAMMA}/events", params={
                    "tag_slug": "soccer", "closed": closed, "limit": 100, "offset": off,
                    "end_date_min": lo, "end_date_max": hi}, timeout=30)
                batch = r.json() if r.ok else []
            except Exception:                           # noqa: BLE001
                batch = []
            if not isinstance(batch, list) or not batch:
                break
            for e in batch:
                if is_game_event(e) and in_window(_ts(e.get("startTime")), now):
                    out[e.get("slug") or e.get("id")] = e
            if len(batch) < 100:
                break
    return list(out.values())


def fetch_books(tokens: list) -> dict:
    out: dict = {}
    for i in range(0, len(tokens), BOOK_BATCH):
        chunk = tokens[i:i + BOOK_BATCH]
        try:
            r = requests.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk], timeout=60)
            books = r.json() if r.ok else []
        except Exception:                               # noqa: BLE001
            books = []
        for b in books if isinstance(books, list) else []:
            out[str(b.get("asset_id"))] = compact_book(b)
    return out


# ── the cycle ────────────────────────────────────────────────────────────────

def run_once(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    events = fetch_window_events(now)
    if not events:
        return {"games": 0, "markets": 0}
    day = f"{now:%Y-%m-%d}"
    snap_path, meta_path, seen_path = _day_paths(day)
    os.makedirs(OUT_DIR, exist_ok=True)
    seen = _read_json(seen_path, {})             # conditionId -> day index

    games: dict = {}
    metas, rows = [], []                         # rows: (index, token0, last, closed)
    for e in events:
        g = group_of(e.get("title") or "")
        key = f"{g}|{e.get('startTime')}"
        main = SUFFIX.search(e.get("title") or "") is None
        if key not in games or main:
            games[key] = {"g": g, "start": e.get("startTime"), "live": e.get("live"),
                          "score": e.get("score"), "period": e.get("period"),
                          "elapsed": e.get("elapsed"), "ended": e.get("ended"),
                          **({"slug": e.get("slug")} if main else {})}
        for m in e.get("markets") or []:
            meta = market_meta(e, m, g)
            if meta is None:
                continue
            if meta["cid"] not in seen:
                meta["i"] = seen[meta["cid"]] = len(seen)
                metas.append(meta)
            rows.append((seen[meta["cid"]], meta["token0"], _f(m.get("lastTradePrice")),
                         bool(m.get("closed"))))

    books = fetch_books([t for _, t, _, closed in rows if not closed])
    q = [[i] + books.get(tok, [None, 0, None, 0, 0, 0]) + [last, int(closed)]
         for i, tok, last, closed in rows]
    _append_gz(meta_path, metas)                 # meta before the rows that point at it
    _append_gz(snap_path, [{"ts": now.isoformat(), "games": list(games.values()), "q": q}])
    _write_json(seen_path, seen)
    two_sided = sum(1 for v in q if v[1] is not None and v[3] is not None)
    return {"games": len(games), "live": sum(bool(g["live"]) for g in games.values()),
            "markets": len(q), "two_sided": two_sided, "new_meta": len(metas), "books": len(books)}


def settle(now: Optional[datetime] = None) -> dict:
    """Record how every market of every game seen in the last few days resolved —
    one Gamma call per event slug, retried until all its markets are final or the
    game is SETTLE_GIVE_UP_H old."""
    now = now or datetime.now(timezone.utc)
    state_path = os.path.join(OUT_DIR, ".settled_events.json")
    state = _read_json(state_path, {"done": []})
    done = set(state["done"])
    have = load_outcomes()
    slugs: dict = {}
    for d in range(0, SETTLE_GIVE_UP_H // 24 + 2):
        for m in load_meta(f"{now - timedelta(days=d):%Y-%m-%d}").values():
            ko = _ts(m.get("kickoff"))
            if m.get("event_slug") and ko and m["event_slug"] not in done:
                slugs[m["event_slug"]] = ko
    new, finished = [], 0
    for slug, ko in slugs.items():
        age_h = (now - ko).total_seconds() / 3600
        if age_h < SETTLE_AFTER_H:
            continue
        ev = None
        for closed in (None, "true"):
            params = {"slug": slug, **({"closed": closed} if closed else {})}
            try:
                r = requests.get(f"{GAMMA}/events", params=params, timeout=20).json()
            except Exception:                           # noqa: BLE001
                r = []
            if isinstance(r, list) and r:
                ev = r[0]
                break
        if ev is None:
            continue
        ms = ev.get("markets") or []
        final = 0
        for m in ms:
            o = outcome_of(m)
            if o is None:
                continue
            final += 1
            if m.get("conditionId") not in have:
                new.append({"cid": m["conditionId"], "slug": slug, **o,
                            "uma": m.get("umaResolutionStatus"), "at": now.isoformat()})
                have[m["conditionId"]] = True
        if (ms and final == len(ms)) or age_h > SETTLE_GIVE_UP_H:
            done.add(slug)
            finished += 1
    if new:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, "outcomes.jsonl"), "a") as fh:
            fh.write("\n".join(json.dumps(x, separators=(",", ":")) for x in new) + "\n")
    _write_json(state_path, {"done": sorted(done)})
    return {"events_checked": len(slugs), "events_finished": finished, "outcomes_new": len(new)}


def summary(day: str) -> None:
    meta = load_meta(day)
    snaps = list(iter_snapshots(day))
    if not snaps:
        print(f"{day}: nothing recorded")
        return
    fams = collections.Counter(m.get("family") for m in meta.values())
    games = {g["g"] for s in snaps for g in s["games"]}
    two = [sum(1 for v in s["q"].values() if v[0] is not None and v[2] is not None) for s in snaps]
    outs = load_outcomes()
    size = sum(os.path.getsize(p) for p in _day_paths(day)[:2] if os.path.exists(p))
    print(f"{day}: {len(snaps)} snapshots {snaps[0]['ts'][11:16]}–{snaps[-1]['ts'][11:16]}Z · "
          f"{len(games)} games · {len(meta)} markets · two-sided books per snapshot median "
          f"{sorted(two)[len(two) // 2]} · {size / 1e6:.1f} MB · outcomes known for "
          f"{sum(1 for c in meta if c in outs)}/{len(meta)}")
    print("families: " + ", ".join(f"{k} {v}" for k, v in fams.most_common(25)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--summary", nargs="?", const=f"{datetime.now(timezone.utc):%Y-%m-%d}")
    a = ap.parse_args()
    if a.summary:
        summary(a.summary)
        return
    lock = open(os.path.join(HERE, ".soccer_live_recorder.lock" if not a.settle
                             else ".soccer_live_settle.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}"
    res = settle() if a.settle else run_once()
    if res.get("games") or a.settle:
        print(f"{stamp} {'settle' if a.settle else 'recorded'} {res}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
