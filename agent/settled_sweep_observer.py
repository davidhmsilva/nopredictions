"""
settled_sweep_observer.py — H-SETTLED-SWEEP. Observation only. No orders, ever.

WHAT IT WATCHES
---------------
Every Polymarket football market whose outcome the SCORE has already decided,
while the market is still quotable. Three windows, in rising order of how often
they occur:

  * post_whistle — the match is over and the board has not resolved. This is
    where wallet GSX- made half of its +$85k (see reports/wallet_gsx_2026-09-02.md).
  * halftime     — every 1st-half market is settled at the break while the
    fixture is still live. It happens in EVERY match, not just the ones that end
    while a board is open, so it should be the more frequent window by far.
  * in_match     — an Over passes its line, a BTTS gets its second goal, an exact
    score dies. Settled the instant the ball crosses.

WHY OBSERVE FIRST
-----------------
The strategy has no model risk and no price risk. It has settlement-logic risk,
and that is not a thing to discover with money: db/038 is what happened the last
time this repo trusted its own idea of a final score. `rule_correct` — our
verdict checked back against PM's own resolution — is the primary measurement
here, ahead of any yield.

The second unknown is whether the quote exists at all. `ladder_of` in
late_goals_observer notes that makers PULL the book once a line is decided, and
the standard `_fetch_book` helper returns None for a one-sided book — which
would blind this observer to its own subject. So books are fetched here with an
ask-only reader, and `book_missing` is recorded as a finding rather than skipped
as an error.

WHY ITS OWN PROCESS
-------------------
It does not ride inside pressure_agent like the HT and favourite arms do. Those
share a poll because they price the same live fixture at the same minute. This
one watches fixtures pressure_agent has already dropped (it needs FT, which is
after that agent stops caring), on a different cadence, with a different failure
mode. Bundling it would put a post-match watcher inside a loop built to exit
when the football stops.

USAGE
-----
    python settled_sweep_observer.py --once --dry-run   # one cycle, no writes
    python settled_sweep_observer.py --once
    python settled_sweep_observer.py                    # forever, 30s
    python settled_sweep_observer.py --settle           # backfill PM resolutions
    python settled_sweep_observer.py --report
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from psycopg2.extras import Json, execute_batch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

from edge_engine import taker_fee_pp                                    # noqa: E402
from fixture_match import pair_score, split_title                       # noqa: E402
from late_goals_observer import _fetch_events                           # noqa: E402
from settled_markets import (                                           # noqa: E402
    PHASE_HALFTIME, PHASE_POST_WHISTLE, MatchState, decide,
)

# force=True on purpose: late_goals_observer calls basicConfig at import time
# with "[late_goals]" HARD-CODED into its format, so without this every line
# below is filed under another agent's name. A log that misattributes itself is
# the same failure as a log that says nothing.
logging.basicConfig(level=logging.INFO, force=True,
                    format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sweep")

DATABASE_URL = os.getenv("DATABASE_URL")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
AF_BASE = "https://v3.football.api-sports.io/fixtures"
CLOB_BOOK = "https://clob.polymarket.com/book"
CLOB_MARKET = "https://clob.polymarket.com/markets"

OBS_VERSION = 1
CYCLE_S = 30                    # the whistle window is minutes wide, not hours
REFRESH_MARKETS_S = 300         # PM opens live boards whenever it likes
# A fixture stops being interesting three hours after we last saw it live. Long
# enough to cover a delayed resolution, short enough that a fixture whose feed
# died does not sit in the pending-final queue forever.
KEEP_AFTER_LIVE_S = 3 * 3600
# One row per token per this interval, unless the ask moved. Without it a
# fixture with 30 settled markets writes 3,600 rows an hour saying the same thing.
RECORD_TTL_S = 180
RECORD_ASK_MOVE = 0.02          # ...or the ask moved by this much
BOOK_BUDGET_PER_CYCLE = 150     # CLOB calls; the cheap-Gamma-price ones go first

# What a sweeper would take. Deliberately loose — this is an observer, and the
# thresholds exist so `would_enter` means something, not to select rows.
MAX_ENTRY_ASK = 0.90            # above this there is no discount worth the risk
MIN_EDGE_PP = 2.0               # (1 - vwap)*100 - fee, in pp
MAX_STAKE_USD = 25.0            # GSX-'s median sweep ticket was $1.52, p90 $11
MIN_ENTRY_USD = 1.0             # a fill smaller than this is not a position

LOCK_PATH = "/tmp/nopredictions_settled_sweep.lock"


# ── singleton ────────────────────────────────────────────────────────────────

def acquire_lock():
    """flock, not pgrep and not mkdir.

    A name-based guard is not a guard — the pressure agent's `pgrep` singleton
    also matched its own `--settle` cron and freed a lock it did not own, which
    stacked 22 hours of duplicate rows. mkdir is atomic but leaks the directory
    when the process is killed. flock is released by the kernel on exit, however
    the process dies.
    """
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    return fh


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── api-football ─────────────────────────────────────────────────────────────

def _parse_af(f: dict) -> dict | None:
    try:
        status = (f["fixture"]["status"] or {}).get("short")
        score = f.get("score") or {}
        ht = score.get("halftime") or {}
        ft = score.get("fulltime") or {}
        goals = f.get("goals") or {}
        # At FT prefer score.fulltime: `goals` carries extra time on the statuses
        # we refuse anyway, and preferring the explicit regulation field means a
        # future status change cannot quietly redefine what "the score" is.
        gh = ft.get("home") if status == "FT" and ft.get("home") is not None else goals.get("home")
        ga = ft.get("away") if status == "FT" and ft.get("away") is not None else goals.get("away")
        return {
            "fixture_id": f["fixture"]["id"],
            "status": status,
            "minute": (f["fixture"]["status"] or {}).get("elapsed"),
            "home": f["teams"]["home"]["name"],
            "away": f["teams"]["away"]["name"],
            "league": (f.get("league") or {}).get("name"),
            "goals_home": int(gh or 0),
            "goals_away": int(ga or 0),
            "ht_home": ht.get("home"),
            "ht_away": ht.get("away"),
        }
    except (KeyError, TypeError, ValueError):
        return None


# Set when api-football answered but REFUSED. "No live fixtures" and "the feed
# will not talk to us" produce the same empty list, and this repo has already
# paid for that ambiguity twice — 36,917 rows recorded "no stats coverage" when
# the real answer was the budget, and 176 rows labelled "quota spent" on a day
# the allowance was 3% used. The distinction is carried, not inferred.
_AF_REFUSED: str | None = None


def _af_get(params: dict) -> list[dict]:
    global _AF_REFUSED
    if not FOOTBALL_API_KEY:
        _AF_REFUSED = "no FOOTBALL_API_KEY"
        return []
    try:
        resp = requests.get(AF_BASE, params=params,
                            headers={"x-apisports-key": FOOTBALL_API_KEY}, timeout=15)
        if resp.status_code != 200:
            _AF_REFUSED = f"HTTP {resp.status_code}"
            return []
        body = resp.json()
        if body.get("errors"):
            # api-football's own headers can contradict this message — seen on
            # 2026-09-02 refusing every call while advertising 74,999/75,000
            # remaining — so the headers are logged alongside the refusal
            # rather than either one being believed on its own.
            quota = {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower()}
            _AF_REFUSED = f"{body['errors']} (headers said {quota})"
            return []
        _AF_REFUSED = None
        return body.get("response", []) or []
    except Exception as exc:
        _AF_REFUSED = f"transport {exc.__class__.__name__}: {exc}"
        return []


@dataclass
class FixtureFeed:
    """Live fixtures, plus the ones that have just finished.

    `/fixtures?live=all` drops a match the moment it ends — which is precisely
    when the post-whistle window opens. So every fixture seen live is followed
    with a batched `/fixtures?ids=` lookup until it reports a terminal status,
    and then kept for KEEP_AFTER_LIVE_S so its board can go on being watched.
    """
    state: dict[int, dict] = field(default_factory=dict)
    last_live: dict[int, float] = field(default_factory=dict)
    final: set[int] = field(default_factory=set)
    poll_failed: bool = False

    def poll(self) -> dict[int, dict]:
        now = time.time()
        live = _af_get({"live": "all"})
        self.poll_failed = _AF_REFUSED is not None
        seen: set[int] = set()
        for raw in live:
            rec = _parse_af(raw)
            if not rec:
                continue
            self.state[rec["fixture_id"]] = rec
            self.last_live[rec["fixture_id"]] = now
            seen.add(rec["fixture_id"])

        # Fixtures we watched live that are no longer on the live feed and have
        # not yet reported a terminal status.
        pending = [fid for fid, ts in self.last_live.items()
                   if fid not in seen and fid not in self.final
                   and now - ts <= KEEP_AFTER_LIVE_S]
        for i in range(0, len(pending), 20):
            for raw in _af_get({"ids": "-".join(str(f) for f in pending[i:i + 20])}):
                rec = _parse_af(raw)
                if not rec:
                    continue
                self.state[rec["fixture_id"]] = rec
                if rec["status"] in ("FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO", "PST"):
                    self.final.add(rec["fixture_id"])

        for fid, ts in list(self.last_live.items()):
            if now - ts > KEEP_AFTER_LIVE_S:
                self.last_live.pop(fid, None)
                self.state.pop(fid, None)
                self.final.discard(fid)
        return self.state


# ── PM side ──────────────────────────────────────────────────────────────────

def match_af(pm_title: str, feed_state: dict[int, dict]) -> dict | None:
    """The api-football fixture for a PM board title, or None when ambiguous.

    Alias-aware token scoring, never substring: the naive version once paired
    River Plate with Platense and a first team with its own reserve side.
    """
    split = split_title(pm_title)
    if not split:
        return None
    best, best_score = None, 0.0
    for rec in feed_state.values():
        score = pair_score(split[0], split[1], rec["home"], rec["away"])
        if score > best_score:
            best, best_score = rec, score
        elif score == best_score and score > 0:
            best = None
    return best


def _token_of(mkt: dict, outcome: str) -> tuple[str | None, float | None]:
    """(token_id, gamma_price) for one outcome label of a PM market."""
    try:
        outcomes = json.loads(mkt.get("outcomes") or "[]")
        tokens = json.loads(mkt.get("clobTokenIds") or "[]")
        prices = json.loads(mkt.get("outcomePrices") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None, None
    for i, name in enumerate(outcomes):
        if str(name).strip().lower() == outcome.strip().lower():
            tok = tokens[i] if i < len(tokens) else None
            return tok, _f(prices[i]) if i < len(prices) else None
    return None, None


def _fetch_ask_book(token_id: str) -> dict:
    """Top of the ASK side, kept even when nothing is bid.

    The usual helper returns None for a one-sided book. A market whose outcome is
    already decided is exactly where the bid disappears, so using that helper
    here would discard every reading this observer exists to take.
    """
    out = {"best_ask": None, "best_bid": None, "ask_depth_usd": None,
           "ask_levels": None, "book_missing": True}
    try:
        resp = requests.get(CLOB_BOOK, params={"token_id": token_id}, timeout=10)
        if resp.status_code != 200:
            return out
        book = resp.json()
    except Exception:
        return out
    asks = sorted((book.get("asks") or []), key=lambda x: _f(x.get("price"), 1.0))
    bids = sorted((book.get("bids") or []), key=lambda x: -_f(x.get("price"), 0.0))
    if bids:
        out["best_bid"] = _f(bids[0].get("price"))
    if not asks:
        return out
    levels = [[_f(a.get("price")), _f(a.get("size"))] for a in asks[:5]]
    levels = [lv for lv in levels if lv[0] is not None and lv[1] is not None]
    if not levels:
        return out
    out.update(best_ask=levels[0][0], ask_levels=levels, book_missing=False,
               ask_depth_usd=sum(p * s for p, s in levels))
    return out


def walk_ladder(levels: list[list[float]], max_usd: float) -> tuple[float, float, float | None]:
    """(shares, cost, vwap) a taker would get spending up to max_usd.

    The ask LEVEL is not the price you pay for size — GSX-'s whole business is
    sub-$10 tickets precisely because the good rungs are shallow.
    """
    shares = cost = 0.0
    for price, size in levels:
        if cost >= max_usd or not price:
            break
        take = min(size, (max_usd - cost) / price)
        if take <= 0:
            break
        shares += take
        cost += take * price
    return shares, cost, (cost / shares if shares > 0 else None)


# ── one cycle ────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    rec: dict
    pm_title: str
    mkt: dict
    rule: str
    outcome: str
    token_id: str
    gamma_price: float | None
    state: MatchState


_PHASE_RANK = {PHASE_POST_WHISTLE: 0, PHASE_HALFTIME: 1}


def build_candidates(pm_fixtures: list[dict], feed_state: dict[int, dict]) -> list[Candidate]:
    out: list[Candidate] = []
    for fx in pm_fixtures:
        rec = match_af(fx["title"], feed_state)
        if rec is None:
            continue
        state = MatchState(
            status=rec["status"], goals_home=rec["goals_home"], goals_away=rec["goals_away"],
            home=rec["home"], away=rec["away"], minute=rec["minute"],
            ht_home=rec["ht_home"], ht_away=rec["ht_away"],
        )
        if not state.safe:
            continue
        for mkt in fx.get("markets") or []:
            if mkt.get("closed"):
                continue
            d = decide(mkt.get("question") or "", state)
            if d is None:
                continue
            tok, gp = _token_of(mkt, d.winning_outcome)
            if not tok:
                continue
            out.append(Candidate(rec=rec, pm_title=fx["title"], mkt=mkt, rule=d.rule,
                                 outcome=d.winning_outcome, token_id=tok,
                                 gamma_price=gp, state=state))
    return out


@dataclass
class Seen:
    """Per-token de-duplication and first-seen bookkeeping.

    `first_seen` is when THIS PROCESS first found the outcome determined. It is
    not "when the goal went in": a restart resets it and a fixture picked up late
    never gets a true zero. The column is named for that.
    """
    first_seen: dict[str, float] = field(default_factory=dict)
    last_row: dict[str, float] = field(default_factory=dict)
    last_ask: dict[str, float | None] = field(default_factory=dict)

    def note(self, token_id: str, now: float) -> None:
        self.first_seen.setdefault(token_id, now)

    def worth_recording(self, token_id: str, ask: float | None, now: float) -> bool:
        prev_at = self.last_row.get(token_id)
        if prev_at is None or now - prev_at >= RECORD_TTL_S:
            return True
        prev_ask = self.last_ask.get(token_id)
        if (ask is None) != (prev_ask is None):
            return True
        if ask is not None and prev_ask is not None and abs(ask - prev_ask) >= RECORD_ASK_MOVE:
            return True
        return False

    def recorded(self, token_id: str, ask: float | None, now: float) -> None:
        self.last_row[token_id] = now
        self.last_ask[token_id] = ask


def evaluate(cand: Candidate, book: dict, seen: Seen, now: float) -> dict:
    shares = cost = 0.0
    vwap = fee = edge = None
    would = False
    if book.get("ask_levels"):
        shares, cost, vwap = walk_ladder(book["ask_levels"], MAX_STAKE_USD)
        if vwap is not None:
            fee = taker_fee_pp(vwap)
            edge = (1.0 - vwap) * 100.0 - fee
            would = (book["best_ask"] is not None and book["best_ask"] <= MAX_ENTRY_ASK
                     and edge >= MIN_EDGE_PP and cost >= MIN_ENTRY_USD)
    first = seen.first_seen.get(cand.token_id, now)
    return {
        "obs_version": OBS_VERSION,
        "fixture_id": cand.rec["fixture_id"],
        "league": cand.rec["league"],
        "home": cand.rec["home"],
        "away": cand.rec["away"],
        "pm_title": cand.pm_title,
        "af_status": cand.state.status,
        "minute": cand.state.minute,
        "goals_home": cand.state.goals_home,
        "goals_away": cand.state.goals_away,
        "ht_home": cand.state.ht_home,
        "ht_away": cand.state.ht_away,
        "phase": cand.state.phase,
        "mins_since_first_seen": int((now - first) / 60),
        "condition_id": cand.mkt.get("conditionId"),
        "token_id": cand.token_id,
        "question": cand.mkt.get("question"),
        "rule": cand.rule,
        "winning_outcome": cand.outcome,
        "gamma_price": cand.gamma_price,
        "best_ask": book.get("best_ask"),
        "best_bid": book.get("best_bid"),
        "ask_depth_usd": book.get("ask_depth_usd"),
        "ask_levels": Json(book.get("ask_levels")) if book.get("ask_levels") else None,
        "book_missing": bool(book.get("book_missing")),
        "would_enter": would,
        "entry_shares": shares or None,
        "entry_cost_usd": cost or None,
        "entry_vwap": vwap,
        "fee_pp": fee,
        "edge_pp": edge,
    }


INSERT_SQL = """
INSERT INTO settled_market_observations (
    obs_version, fixture_id, league, home, away, pm_title,
    af_status, minute, goals_home, goals_away, ht_home, ht_away,
    phase, mins_since_first_seen, condition_id, token_id, question,
    rule, winning_outcome, gamma_price, best_ask, best_bid,
    ask_depth_usd, ask_levels, book_missing, would_enter,
    entry_shares, entry_cost_usd, entry_vwap, fee_pp, edge_pp)
VALUES (
    %(obs_version)s, %(fixture_id)s, %(league)s, %(home)s, %(away)s, %(pm_title)s,
    %(af_status)s, %(minute)s, %(goals_home)s, %(goals_away)s, %(ht_home)s, %(ht_away)s,
    %(phase)s, %(mins_since_first_seen)s, %(condition_id)s, %(token_id)s, %(question)s,
    %(rule)s, %(winning_outcome)s, %(gamma_price)s, %(best_ask)s, %(best_bid)s,
    %(ask_depth_usd)s, %(ask_levels)s, %(book_missing)s, %(would_enter)s,
    %(entry_shares)s, %(entry_cost_usd)s, %(entry_vwap)s, %(fee_pp)s, %(edge_pp)s)
"""


def cycle(conn, pm_fixtures: list[dict], feed: FixtureFeed, seen: Seen,
          dry_run: bool = False) -> dict:
    now = time.time()
    state = feed.poll()
    if feed.poll_failed:
        # Loud, and no rows. A refused feed must never look like a quiet evening
        # with no football on, and it must never let a row be written from a
        # score we did not actually read.
        log.error(f"api-football REFUSED — recording nothing this cycle: {_AF_REFUSED}")
        return {"live_fixtures": 0, "pm_fixtures": len(pm_fixtures), "determined": 0,
                "booked": 0, "rows": 0, "no_book": 0, "would_enter": 0,
                "af_refused": 1}
    cands = build_candidates(pm_fixtures, state)
    for c in cands:
        seen.note(c.token_id, now)

    # Gamma's quote is free and the CLOB call is not, so the cheap ones — which
    # is where the whole opportunity lives — are read first.
    cands.sort(key=lambda c: (_PHASE_RANK.get(c.state.phase, 2),
                              c.gamma_price if c.gamma_price is not None else 1.0))
    budget = cands[:BOOK_BUDGET_PER_CYCLE]

    with ThreadPoolExecutor(max_workers=12) as pool:
        books = list(pool.map(lambda c: _fetch_ask_book(c.token_id), budget))

    rows = []
    for c, book in zip(budget, books):
        if not seen.worth_recording(c.token_id, book.get("best_ask"), now):
            continue
        rows.append(evaluate(c, book, seen, now))
        seen.recorded(c.token_id, book.get("best_ask"), now)

    if rows and not dry_run:
        with conn.cursor() as cur:
            execute_batch(cur, INSERT_SQL, rows, page_size=200)
        conn.commit()

    enters = [r for r in rows if r["would_enter"]]
    stats = {
        "live_fixtures": len(state),
        "pm_fixtures": len(pm_fixtures),
        "determined": len(cands),
        "booked": len(budget),
        "rows": len(rows),
        "no_book": sum(1 for r in rows if r["book_missing"]),
        "would_enter": len(enters),
        "af_refused": 0,
    }
    for r in enters:
        log.info(f"  ENTER {r['best_ask']:.3f} edge {r['edge_pp']:+.1f}pp "
                 f"${r['entry_cost_usd']:.2f} [{r['phase']}/{r['rule']}] "
                 f"{r['home']} {r['goals_home']}-{r['goals_away']} {r['away']} "
                 f"| {r['question']} -> {r['winning_outcome']}")
    return stats


# ── settlement: was the RULE right? ──────────────────────────────────────────

def _pm_winner(condition_id: str) -> str | None:
    try:
        resp = requests.get(f"{CLOB_MARKET}/{condition_id}", timeout=20)
        if resp.status_code != 200:
            return None
        for t in (resp.json().get("tokens") or []):
            if t.get("winner"):
                return t.get("outcome")
    except Exception:
        return None
    return None


def settle(conn) -> int:
    """Check every recorded verdict against PM's own resolution.

    This is the primary measurement of the whole experiment. A rule that is
    right about the score but wrong about what the market PAYS is the failure
    mode that costs money, and it is invisible in any statistic that does not
    ask PM.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT condition_id
              FROM settled_market_observations
             WHERE settled_at IS NULL AND condition_id IS NOT NULL
               AND observed_at < now() - interval '3 hours'
             LIMIT 400
        """)
        cids = [r["condition_id"] for r in cur.fetchall()]
    if not cids:
        return 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = dict(zip(cids, pool.map(_pm_winner, cids)))

    done = 0
    with conn.cursor() as cur:
        for cid, win in winners.items():
            if win is None:
                continue                      # not resolved yet: leave it open
            cur.execute("""
                UPDATE settled_market_observations
                   SET settled_at = now(), pm_winner = %s,
                       rule_correct = (winning_outcome = %s)
                 WHERE condition_id = %s AND settled_at IS NULL
            """, (win, win, cid))
            done += cur.rowcount
    conn.commit()
    return done


# ── report ───────────────────────────────────────────────────────────────────

def report(conn) -> None:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""SELECT count(*) n, count(DISTINCT fixture_id) fx,
                          count(DISTINCT token_id) tok, min(observed_at) lo,
                          max(observed_at) hi
                     FROM settled_market_observations WHERE obs_version = %s""",
                (OBS_VERSION,))
    r = cur.fetchone()
    print(f"\nH-SETTLED-SWEEP  obs_version {OBS_VERSION}")
    print(f"  {r['n']} rows · {r['fx']} fixtures · {r['tok']} tokens · {r['lo']} -> {r['hi']}")
    if not r["n"]:
        print("  nothing recorded yet")
        return

    print("\n── is the RULE right? (the primary test) ──")
    cur.execute("""SELECT rule, count(*) n, count(rule_correct) settled,
                          sum((rule_correct)::int) ok
                     FROM settled_market_observations
                    WHERE obs_version = %s GROUP BY rule ORDER BY n DESC""",
                (OBS_VERSION,))
    for row in cur.fetchall():
        acc = f"{100.0 * row['ok'] / row['settled']:.1f}%" if row["settled"] else "—"
        print(f"  {row['rule']:18s} n={row['n']:6d}  settled={row['settled']:5d}  correct={acc}")
    cur.execute("""SELECT count(rule_correct) s, sum((rule_correct)::int) ok
                     FROM settled_market_observations WHERE obs_version = %s""",
                (OBS_VERSION,))
    t = cur.fetchone()
    if t["s"]:
        print(f"  {'TOTAL':18s} settled={t['s']}  correct={100.0 * t['ok'] / t['s']:.2f}%")
        if t["ok"] != t["s"]:
            print("  ⚠️  a wrong rule is a bought-at-6-cents-worth-zero, not a missed trade —")
            print("      list them with: SELECT question, winning_outcome, pm_winner ...")

    print("\n── does the quote exist? ──")
    cur.execute("""SELECT phase, count(*) n, sum(book_missing::int) nobook,
                          avg(best_ask) ask, sum(would_enter::int) enters
                     FROM settled_market_observations
                    WHERE obs_version = %s GROUP BY phase ORDER BY n DESC""",
                (OBS_VERSION,))
    for row in cur.fetchall():
        ask = f"{row['ask']:.3f}" if row["ask"] is not None else "—"
        print(f"  {row['phase']:14s} n={row['n']:6d}  no book {100.0 * row['nobook'] / row['n']:5.1f}%"
              f"  mean ask {ask}  would_enter {row['enters']}")

    print("\n── ask distribution on determined winners ──")
    cur.execute("""SELECT width_bucket(best_ask, 0, 1, 10) b, count(*) n,
                          avg(ask_depth_usd) depth
                     FROM settled_market_observations
                    WHERE obs_version = %s AND best_ask IS NOT NULL
                    GROUP BY b ORDER BY b""", (OBS_VERSION,))
    for row in cur.fetchall():
        lo = (row["b"] - 1) / 10.0
        print(f"  {lo:.1f}-{lo + 0.1:.1f}  n={row['n']:6d}  mean ask depth ${row['depth'] or 0:,.0f}")

    print("\n── what the entries would have been ──")
    cur.execute("""SELECT count(*) n, sum(entry_cost_usd) cost, avg(entry_vwap) vwap,
                          count(rule_correct) settled, sum((rule_correct)::int) ok,
                          sum(CASE WHEN rule_correct THEN entry_shares - entry_cost_usd
                                   WHEN rule_correct IS FALSE THEN -entry_cost_usd END) pnl
                     FROM settled_market_observations
                    WHERE obs_version = %s AND would_enter""", (OBS_VERSION,))
    e = cur.fetchone()
    if not e["n"]:
        print("  no would_enter rows yet")
    else:
        print(f"  {e['n']} entries · ${e['cost'] or 0:,.2f} deployed · mean vwap {e['vwap'] or 0:.3f}")
        if e["settled"]:
            print(f"  settled {e['settled']} · rule correct {e['ok']} · "
                  f"gross P&L ${e['pnl'] or 0:+,.2f}")
        print("  ⚠️  hypothetical: nothing was ever sent to the book, so no fill is proven,")
        print("      and rows repeat the same token across polls — dedupe by token before")
        print("      reading this as a yield.")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="H-SETTLED-SWEEP observer (no orders)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--interval", type=int, default=CYCLE_S)
    args = ap.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL missing")
        return 1

    if args.report:
        with _conn() as conn:
            report(conn)
        return 0

    if args.settle:
        with _conn() as conn:
            log.info(f"settled {settle(conn)} rows")
        return 0

    lock = acquire_lock()
    if lock is None:
        log.info("another observer holds the lock — exiting")
        return 0

    if not FOOTBALL_API_KEY:
        log.error("FOOTBALL_API_KEY missing — this observer cannot run without a "
                  "score feed, and inferring the score from PM's own prices is "
                  "exactly the mistake db/030 documents")
        return 1

    conn = None if args.dry_run else _conn()
    feed, seen = FixtureFeed(), Seen()
    pm_fixtures: list[dict] = []
    pm_at = 0.0

    while True:
        try:
            if time.time() - pm_at > REFRESH_MARKETS_S or not pm_fixtures:
                pm_fixtures = _fetch_events()
                pm_at = time.time()
                log.info(f"PM universe: {len(pm_fixtures)} fixtures")
            stats = cycle(conn, pm_fixtures, feed, seen, dry_run=args.dry_run)
            log.info(" ".join(f"{k}={v}" for k, v in stats.items()))
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            # This Mac sleeps; the Supabase socket dies while the process is
            # frozen and the first write after the wake raises. Reconnect rather
            # than exit — a restart only happens once the machine is awake again.
            log.warning(f"db dropped ({exc.__class__.__name__}), reconnecting")
            try:
                conn = _conn()
            except psycopg2.Error as e2:
                log.warning(f"reconnect failed: {e2}")
        except Exception as exc:
            log.exception(f"cycle failed: {exc}")
        if args.once:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
