"""
late_goals_observer.py — paper-only observation of PM late-goal over lines.

THE QUESTION
------------
At minute 70-88, with the match short of goals, does Polymarket price the "one
more goal" over line above or below its empirical fair value? We know the fair
side cold (agent/late_goals_table.py, 16,479 clean matches). We do not know the
PM side: pm_ticks only starts 2026-07-21, so there is nothing to backtest. This
records it. NO ORDERS ARE EVER PLACED FROM HERE.

WHICH MARKET IT WATCHES
-----------------------
Over (goals_so_far + 0.5) — the line that needs exactly ONE more goal. Deliberate:
buying the two-goals-away line to cash out on the first goal is EV-neutral by
construction, because the price is a martingale (entry 0.116 at 1-0/75' implies
an exit of 0.116/0.470 = 0.247, +113% on 47% of trades = EV exactly 1.00). The
leverage adds no edge and the round trip pays taker fee and spread twice —
about 15-18% of stake. Same view, one line lower, held to settlement: ~2.6%.

HOW IT KNOWS THE SCORE WITHOUT A LIVE FEED
------------------------------------------
api-football is not usable — its daily quota is shared with the crons and
routinely exhausted (live_fixture_ticks held 46 rows in total on 2026-07-22).
Instead the score is read off PM itself. "Over N.5" pays if the total exceeds N,
so once the match is past N goals that token is quoted at ~1.00. A settled line
gives a lower bound, the cheapest unsettled line an upper bound, and two adjacent
lines pin the total exactly. api-football is still called opportunistically, but
only to CHECK the ladder — disagreements are logged, never silently trusted.

The clock comes from gameStartTime, with a flat 15-minute halftime assumed.
wall_minute is stored raw so that assumption can be recalibrated later.

USAGE
-----
    cd agent && source ../ingest/.venv/bin/activate
    python late_goals_observer.py                 # run forever, 60s cycle
    python late_goals_observer.py --once          # single cycle (smoke test)
    python late_goals_observer.py --dry-run       # print, no DB writes
    python late_goals_observer.py --report        # what has been collected
    python late_goals_observer.py --settle        # backfill outcomes only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import Json, execute_batch

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_txn                                                       # noqa: E402
import late_goals_table as lgt  # noqa: E402
from edge_engine import taker_fee_pp  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [late_goals] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("late_goals")

DATABASE_URL = os.getenv("DATABASE_URL")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_BOOK = "https://clob.polymarket.com/book"

CYCLE_S = 60                # book poll interval
REFRESH_MARKETS_S = 300     # how often to re-pull the PM football universe
API_CHECK_S = 900           # opportunistic api-football cross-check

LIVE_FROM, LIVE_TO = 66, 90     # game-minute window the STRATEGY is about
PREMATCH_WINDOW_MIN = 45        # capture the pre-kickoff over 2.5 within this many minutes of KO
HALFTIME_MIN = 15               # assumed break length when converting wall clock

# Recording runs on the wall clock, not on game_minute, and runs well past the
# point a match can possibly still be going. game_minute 90 is wall 105, and
# cutting there is what truncated the v1 series: on 26 settled fixtures an
# average of 0.96 goals arrived after our "minute 90" (true full time 2.65 vs
# 1.69 at that poll), which at ~0.03 goals/min is ~30 minutes of match we never
# watched. The cause is PM's listed start time, not the arithmetic — smaller
# leagues kick off late against it. So the window has to absorb a clock error
# that large, and a row past the real full time is harmless: the ladder has
# frozen and every line has resolved, which is itself the signal that it is over.
MAX_WALL_MIN = 155

# api-football's elapsed minute is the only independent clock we have. It
# answers on ~6% of polls (the quota is shared with the crons and routinely
# exhausted), so it cannot drive the clock — it can only certify it.
CLOCK_TOLERANCE_MIN = 3

# The quota-free second anchor. A pre-kickoff over ladder is essentially static
# at this resolution; a live one moves every cycle. So the first refresh at
# which the ladder departs from its pre-kickoff baseline dates the real kick-off
# without any external feed. Resolution is REFRESH_MARKETS_S, hence the loose
# tolerance — it is not there to time the match to the minute, it is there to
# catch the failure mode we actually have: a fixture that starts half an hour
# after the time PM lists, which is what made the v1 series unusable.
KICKOFF_MOVE_PP = 0.03          # ladder deviation that means the ball is rolling
KICKOFF_TOLERANCE_MIN = 12

# Bumped whenever a defect makes the rows before it unusable for the question,
# so analysis can cut the series cleanly instead of pooling good data with bad.
# 2 = clock verification + CLOB confirmation of the score (db/030, 2026-08-04).
OBS_VERSION = 2

# An over line quoted at/above SETTLED_PRICE has already paid; at/below
# UNSETTLED_MAX it certainly has not. In between is a DEAD ZONE and must be
# treated as unknown.
#
# Both edges were found the hard way. 0.97 as a single threshold is wrong: a
# pre-kickoff Over 0.5 legitimately trades 0.9715 (0-0 is rare) and reads as a
# goal already scored. But a single 0.99 threshold is wrong in the other
# direction: caught mid-update at minute 63, a just-resolved Over 2.5 quoted
# 0.985 read as UNRESOLVED and the score came out confidently wrong by exactly
# one goal. A wrong-but-certain score is far worse than a missing one — it
# silently mislabels the target line and every fair value derived from it.
#
# Inside the live window an unsettled line needs at least one more goal, which
# tops out near 55%, so nothing legitimate lands anywhere near 0.95.
SETTLED_PRICE = 0.99
UNSETTLED_MAX = 0.95
MAX_WORKERS = 16

# Gamma's outcomePrices lag the CLOB. On 20% of v1 polls the CLOB ask sat more
# than 20pp above the Gamma mid for the same token — the signature of a line the
# match has already passed while Gamma still quotes it live, which reads back as
# a goal that has not been scored. So the two rungs that carry the score bound
# get their books checked against the CLOB before the score is called certain.
BOOK_SETTLED_BID = 0.97     # a settled line's bid does not sit below this
BOOK_UNSETTLED_ASK = 0.96   # an unsettled line's ask does not sit above this

# A resolved PM token's final price is 0 or 1. Anything strictly between these
# is a market that stopped printing while still live, and carries no outcome.
RESOLVED_LO, RESOLVED_HI = 0.03, 0.97

# What the strategy would act on. Kept here rather than in the DB so the numbers
# stay visible next to the reasoning; nothing here moves money either way.
MIN_EDGE_PP = 2.0           # after taker fee
MIN_DEPTH_USD = 50.0        # live over books ran ~$800/side; $50 is a floor, not a target

# "The market believed in goals": a displayed over-2.5 price of 1.80 or shorter.
# That is a RAW odd with vig in it, and pre_over25 here is a normalised mid, so
# the two are not the same number. Calibrated against 96,810 Pinnacle over/under
# pairs: raw <= 1.80 covers 32.9% of matches, and the de-vigged threshold that
# selects the same 32.9% is 0.534 (they agree on 90% of matches individually).
# Using 1/1.80 = 0.5556 directly would silently tighten the filter to the top
# 24% and quietly change the strategy being tested.
MIN_PRE_OVER25 = 0.534      # == displayed odds of about 1.80

# A full-match total is the ONLY thing that may enter the ladder. After the
# "<home> vs. <away>: " prefix is stripped, the remainder has to be exactly
# "O/U <n>.5" — nothing before it, nothing after. That one rule rejects team
# totals ("NK Celje O/U 1.5"), period totals ("1st Half O/U 0.5") and corners
# ("O/U 7.5 Total Corners") in one go. Getting this wrong is not cosmetic: a
# leaked team total overwrites the real line and the inferred score comes out
# silently wrong — the exact bug class that hit the NBA scanner.
_FULL_LINE_RE = re.compile(r"^O/U\s*(\d+\.5)$", re.I)
_SUFFIX_RE = re.compile(r"\s+-\s+[A-Z][A-Za-z0-9 /'&.]*$")


def _conn():
    # autocommit, via db_txn — a SELECT on a psycopg2 default connection opens a
    # transaction that stays open until something commits, and this process then
    # sleeps on it. See db_txn.py for the 8-minute one that was found in
    # production. Writes that must land together use db_txn.atomic().
    return db_txn.connect(DATABASE_URL)


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None


# ── clock ────────────────────────────────────────────────────────────────────

def game_minute(kickoff: datetime, now: datetime) -> tuple[int | None, int | None]:
    """(wall_minute, game_minute). Flat halftime — good to about +-3 minutes.

    Returns game_minute=45 through the break rather than letting it run on, so a
    match sitting at halftime is never mistaken for one at minute 55.
    """
    if not kickoff:
        return None, None
    wall = int((now - kickoff).total_seconds() // 60)
    if wall < 0:
        return wall, None
    if wall <= 45:
        return wall, wall
    if wall <= 45 + HALFTIME_MIN:
        return wall, 45
    return wall, wall - HALFTIME_MIN


# ── PM universe ──────────────────────────────────────────────────────────────

def _fetch_events() -> list[dict]:
    """Football fixtures with a kickoff inside +-4h, one entry per fixture.

    Polymarket splits a fixture across sibling events — "A vs. B", "A vs. B -
    More Markets", "A vs. B - Total Corners" — and the full-match over ladder
    lives in the sibling, not the main event. They are merged back into one
    fixture here, otherwise the ladder is never seen whole and the score can
    never be pinned.
    """
    now = datetime.now(timezone.utc)
    date_min = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    date_max = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    events: list[dict] = []
    for offset in range(0, 2000, 100):
        try:
            resp = requests.get(f"{GAMMA_API}/events", params={
                "closed": "false", "active": "true", "limit": 100, "offset": offset,
                "end_date_min": date_min, "end_date_max": date_max,
            }, timeout=15)
            resp.raise_for_status()
            page = resp.json()
        except Exception as exc:
            log.warning(f"gamma events error: {exc}")
            break
        if not isinstance(page, list) or not page:
            break
        events.extend(page)

    fixtures: dict[str, dict] = {}
    candidates = 0
    for ev in events:
        title = ev.get("title", "")
        if " vs" not in title or _is_womens(title) or _is_esports(title):
            continue
        candidates += 1
        if not _is_football(ev):
            continue

        ko = _parse_ts(ev.get("startTime"))
        if not ko:
            for mkt in ev.get("markets", []) or []:
                ko = _parse_ts(mkt.get("gameStartTime"))
                if ko:
                    break
        if not ko or abs((ko - now).total_seconds()) > 4 * 3600:
            continue

        base = _SUFFIX_RE.sub("", title).strip()
        fx = fixtures.setdefault(base, {"title": base, "kickoff": ko, "markets": []})
        fx["markets"].extend(ev.get("markets") or [])

    # A silent zero is how this observer lost 9 days: Gamma changed the shape of
    # `sport` and every fixture fell through the classifier while the log kept
    # printing a healthy "universe refreshed -> 0". Plenty of "A vs B" events but
    # none of them football means the classifier broke, not that football stopped.
    if not fixtures and candidates >= 25:
        log.error(f"NO football fixtures out of {candidates} 'X vs Y' events — "
                  f"sport classifier is probably broken, check Gamma's event shape")
    return list(fixtures.values())


def _is_football(ev: dict) -> bool:
    """Gamma labels the sport in three places and has changed which one it fills.

    The `tags` array is the stable one — a soccer event carries {'slug': 'soccer'}
    alongside its competition tag. `sport` used to be a string and is now an object
    keyed 'sport'/'name' (NOT 'slug'), holding the competition ("UEFA Europa
    League"), so the old `"soccer" in sport` test rejects every football fixture.
    All three are read here, and an event Gamma labels not at all falls through to
    the structural guards (title shape, line magnitude <= 6.5).
    """
    for tag in ev.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        if str(tag.get("slug") or "").lower() == "soccer":
            return True
        if str(tag.get("label") or "").lower() == "soccer":
            return True

    sport = ev.get("sport")
    if isinstance(sport, dict):
        # 'tags' here is a comma-separated id list; 100350 is Gamma's soccer tag.
        if "100350" in str(sport.get("tags") or "").split(","):
            return True
        sport = sport.get("slug") or sport.get("sport") or sport.get("name") or ""
    sport = str(sport or "").lower()
    if not sport:
        return True                        # unlabelled — let the structure decide
    return "soccer" in sport or "football" in sport


# Esports events carry "<Team> vs <Team>" titles and O/U map lines in the 2.5
# range, so every structural filter above passes them straight through. Gamma's
# sport field is not set on them. Blocked by discipline name.
_ESPORTS = ("lol:", "counter-strike", "cs2", "dota", "valorant", "rainbow six",
            "overwatch", "call of duty", "rocket league", "starcraft", "esports")


def _is_womens(title: str) -> bool:
    t = title.lower()
    return " women" in t or "(w)" in t or " girls" in t or t.endswith(" w")


def _is_esports(title: str) -> bool:
    t = title.lower()
    return any(tag in t for tag in _ESPORTS)


def ladder_of(fixture: dict) -> dict[float, dict]:
    """Full-match over ladder: {line: {over_price, token_id, condition_id}}.

    Prices come from Gamma's outcomePrices, NOT from the CLOB book. A line the
    match has already passed stops having a two-sided book — market makers pull
    it once the outcome is certain — so the CLOB returns nothing for exactly the
    lines that establish the lower bound on the score. Gamma keeps quoting them
    at ~0.9995, which is the signal the inference needs.
    """
    out: dict[float, dict] = {}
    for mkt in fixture["markets"]:
        q = mkt.get("question") or ""
        if mkt.get("closed"):
            continue
        rem = q.split(": ", 1)[1] if ": " in q else q
        m = _FULL_LINE_RE.match(rem.strip())
        if not m:
            continue
        line = _f(m.group(1))
        if line is None or line > 6.5:      # guards non-football totals
            continue

        try:
            prices = json.loads(mkt.get("outcomePrices") or "[]")
            outcomes = json.loads(mkt.get("outcomes") or "[]")
            token_ids = json.loads(mkt.get("clobTokenIds") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue

        for i, name in enumerate(outcomes):
            if not str(name).strip().lower().startswith("over"):
                continue
            out[line] = {
                "line": line,
                "over_price": _f(prices[i]) if i < len(prices) else None,
                "token_id": token_ids[i] if i < len(token_ids) else None,
                "condition_id": mkt.get("conditionId"),
                "question": q,
            }
    return out


def ladder_consistent(ladder: dict[float, float], up_to: float | None = None) -> bool:
    """Over prices must be non-increasing in the line — Over 3.5 implies Over 2.5.

    Real PM ladders violate this regularly on the far end (a stale or
    placeholder book on a line nobody trades). Only the lines that actually
    carry the inference are checked: a broken quote on Over 5.5 says nothing
    about whether Over 1.5 has resolved, and discarding the fixture over it
    throws away good data.
    """
    seq = [p for ln, p in sorted(ladder.items())
           if p is not None and (up_to is None or ln <= up_to)]
    return all(a >= b - 0.02 for a, b in zip(seq, seq[1:]))


# ── books ────────────────────────────────────────────────────────────────────

def _fetch_book(tok: dict) -> dict | None:
    try:
        resp = requests.get(CLOB_BOOK, params={"token_id": tok["token_id"]}, timeout=10)
        if resp.status_code != 200:
            return None
        book = resp.json()
    except Exception:
        return None

    bids = sorted(book.get("bids") or [], key=lambda x: -_f(x["price"], 0))
    asks = sorted(book.get("asks") or [], key=lambda x: _f(x["price"], 1))
    if not bids or not asks:
        return None
    notional = lambda side: sum(_f(x["price"], 0) * _f(x["size"], 0) for x in side[:5])  # noqa: E731
    return {
        **tok,
        "best_bid": _f(bids[0]["price"]),
        "best_ask": _f(asks[0]["price"]),
        "bid_depth_usd": notional(bids),
        "ask_depth_usd": notional(asks),
    }


# ── score inference ──────────────────────────────────────────────────────────

def infer_goals(ladder: dict[float, float]) -> tuple[int | None, int | None, bool]:
    """(lower, upper, certain) total goals implied by the over ladder.

    Over N.5 quoted >= SETTLED_PRICE  =>  total >= N+1
    Over N.5 quoted <= UNSETTLED_MAX  =>  total <= N
    anything between the two          =>  says nothing, and poisons certainty

    A contradiction (lower > upper) means a stale or crossed book — return it as
    uncertain rather than picking a side.
    """
    lower, upper, dead = 0, None, False
    for line, price in sorted(ladder.items()):
        n = int(line)
        if price is None:
            continue
        if price >= SETTLED_PRICE:
            lower = max(lower, n + 1)
        elif price <= UNSETTLED_MAX:
            upper = n if upper is None else min(upper, n)
        else:
            dead = True     # mid-update rung: bounds around it are not trustworthy
    if upper is not None and lower > upper:
        return None, None, False
    return lower, upper, (not dead and upper is not None and lower == upper)


# ── pre-match total ──────────────────────────────────────────────────────────

def pm_over25(ladder: dict[float, dict]) -> float | None:
    """PM's own P(over 2.5) — the pre-kickoff 'market believed in goals' signal.

    Gamma's outcomePrices are already normalised across the pair, so this is a
    mid rather than an ask. It is still only a PROXY for the vig-free Pinnacle
    number the fair-value table buckets on; the mapping between the two is one
    of the things this table is collecting evidence for.
    """
    cell = ladder.get(2.5)
    return None if not cell else cell.get("over_price")


def _pre_over25_from_ticks(conn, event_title: str, kickoff: datetime) -> float | None:
    """Fallback: the last pre-kickoff over-2.5 mid recorded by tick_recorder.

    Prefix match, because pm_ticks stores PM's raw sibling-event titles ("X vs. Y
    - More Markets") while we key on the merged fixture title.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT (best_bid + best_ask) / 2
            FROM pm_ticks
            WHERE event_title LIKE %s || '%%'
              AND question ~* ': O/U 2\\.5$'
              AND outcome ILIKE 'over%%'
              AND observed_at <= %s
              AND best_bid IS NOT NULL AND best_ask IS NOT NULL
            ORDER BY observed_at DESC LIMIT 1
        """, (event_title, kickoff))
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


# ── api-football cross-check (opportunistic only) ────────────────────────────

def _api_scores() -> dict[str, tuple[int, int | None]]:
    """{normalised 'home vs away': (total goals, elapsed minute)}.

    Empty when the quota is gone, which is most of the time. `elapsed` is the
    only clock in this process that does not come from PM's own listed start
    time, so it is what certifies game_minute — see CLOCK_TOLERANCE_MIN.
    """
    if not FOOTBALL_API_KEY:
        return {}
    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"live": "all"},
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            timeout=12,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except Exception:
        return {}

    out = {}
    for fix in data.get("response", []):
        teams, goals = fix.get("teams", {}), fix.get("goals", {})
        h, a = teams.get("home", {}).get("name"), teams.get("away", {}).get("name")
        gh, ga = goals.get("home"), goals.get("away")
        elapsed = ((fix.get("fixture") or {}).get("status") or {}).get("elapsed")
        if h and a and gh is not None and ga is not None:
            out[_norm(f"{h} vs {a}")] = (gh + ga, elapsed)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", s.lower()).replace(" fc", "").replace(" cf", "").strip()


def _api_lookup(scores: dict[str, tuple[int, int | None]],
                title: str) -> tuple[int | None, int | None]:
    """(goals, elapsed). Loose match — PM and api-football disagree on club naming constantly."""
    if not scores:
        return None, None
    want = _norm(title.split(" - ")[0])
    parts = [p.strip() for p in re.split(r" vs\.? ", want) if p.strip()]
    if len(parts) != 2:
        return None, None
    for key, val in scores.items():
        if all(any(w in key for w in p.split() if len(w) > 3) for p in parts):
            return val
    return None, None


# ── score confirmation against the CLOB ──────────────────────────────────────

def confirm_score_books(certain: bool, goals: int | None, needed: int,
                        target_book: dict | None, settled_book: dict | None) -> bool:
    """Does the CLOB agree with the Gamma ladder on the two rungs pinning the score?

    infer_goals() reads Gamma's outcomePrices, and Gamma lags. On 20% of v1
    polls the CLOB ask sat more than 20pp above the Gamma mid for the same
    token — the signature of a line the match has already passed while Gamma
    still quotes it live, which reads back as one more goal being needed when
    it has already been scored. Where api-football could referee, the ladder was
    wrong on 29% of polls (305 of 1,048), under-reading the score in 181 of them.

    Both rungs must hold: the one below the score has to look paid on the CLOB,
    the one above has to look live. A 0-0 match has no rung below, so there the
    upper rung carries it alone — that is the whole of what can be checked, not
    a relaxation.
    """
    if not certain or goals is None:
        return False
    if target_book is None or target_book.get("best_ask") is None:
        return False
    # Only the needed=1 target IS the upper boundary rung. The leveraged line
    # sits a rung higher and says nothing about where the score is.
    if needed == 1 and target_book["best_ask"] > BOOK_UNSETTLED_ASK:
        return False
    if goals == 0:
        return True
    return bool(settled_book is not None
                and settled_book.get("best_bid") is not None
                and settled_book["best_bid"] >= BOOK_SETTLED_BID)


# ── kick-off detection ───────────────────────────────────────────────────────

def track_kickoff(clock_state: dict, title: str, ladder: dict[float, float],
                  wall: int | None, now: datetime) -> datetime | None:
    """First time this fixture's ladder moved. Returns the detected kick-off.

    Before kick-off the book is quoted off a pre-match model and barely moves
    between refreshes. Once the match is live every rung drifts continuously,
    and a goal snaps one of them to ~1.00. Either is unambiguous, and neither
    needs api-football.

    Only the baseline is trusted from before kick-off: a fixture first seen
    after PM's listed start has no baseline and stays undetected rather than
    being assumed to have started, because "no evidence it began late" is not
    evidence it began on time.
    """
    if wall is None:
        return None
    st = clock_state.setdefault(title, {"baseline": None, "moved_at": None})
    if st["moved_at"] is not None:
        return st["moved_at"]

    live_rungs = {ln: p for ln, p in ladder.items() if p is not None}
    if not live_rungs:
        return None

    if wall <= 0:
        st["baseline"] = live_rungs          # keep the latest pre-kickoff snapshot
        return None
    if st["baseline"] is None:
        return None

    shared = [(p, st["baseline"][ln]) for ln, p in live_rungs.items() if ln in st["baseline"]]
    if shared and any(abs(now_p - was) >= KICKOFF_MOVE_PP or
                      (now_p >= SETTLED_PRICE) != (was >= SETTLED_PRICE)
                      for now_p, was in shared):
        st["moved_at"] = now
    return st["moved_at"]


# ── one cycle ────────────────────────────────────────────────────────────────

def observe(table: dict, fixtures: list[dict], api_scores: dict[str, int],
            pre_cache: dict[str, float], conn,
            clock_state: dict | None = None) -> list[dict]:
    now = datetime.now(timezone.utc)
    clock_state = {} if clock_state is None else clock_state
    rows: list[dict] = []
    pending: list[tuple] = []          # (fixture-state, target cell) awaiting a book

    for fx in fixtures:
        title = fx["title"]
        cells = ladder_of(fx)
        if not cells:
            continue
        ladder = {ln: c["over_price"] for ln, c in cells.items()}
        wall, minute = game_minute(fx["kickoff"], now)
        detected_ko = track_kickoff(clock_state, title, ladder, wall, now)

        # Pre-kickoff: capture the "market believed in goals" signal and move on.
        if minute is None or minute < LIVE_FROM:
            mid = pm_over25(cells)
            if mid is not None and wall is not None and -PREMATCH_WINDOW_MIN <= wall <= 5:
                pre_cache[title] = mid
                rows.append(_row(title, "prematch", fx, wall, minute, ladder,
                                 pre_over25=mid, pre_src="self"))
            continue
        # Recording stops on the WALL clock, not on game_minute. Cutting at
        # game_minute 90 is what truncated the v1 series — on the fixtures where
        # PM's start time runs ahead of the real kick-off it stopped the tape
        # around real minute 60. See MAX_WALL_MIN.
        if wall > MAX_WALL_MIN:
            continue

        lower, upper, certain = infer_goals(ladder)
        # Verify only the rungs the bounds actually rest on, one above included.
        if not ladder_consistent(ladder, up_to=(upper + 1.5) if upper is not None else None):
            log.warning(f"inconsistent ladder on {title[:44]}: {ladder}")
            lower, upper, certain = None, None, False

        api_goals, api_minute = _api_lookup(api_scores, title)
        if api_goals is not None and certain and api_goals != lower:
            log.warning(f"score disagreement on {title[:44]}: ladder={lower} api={api_goals}")
            certain = False

        # The clock is guilty until proven innocent. game_minute derives from
        # PM's listed start time and nothing else, and on a real subset of
        # fixtures that time is ~30 minutes ahead of the actual kick-off. Only
        # api-football's elapsed minute can certify it, and it answers rarely —
        # so most rows are recorded with clock_verified=false and are
        # observation only. A row is never dropped for this: an unverified row
        # is still evidence, an entry taken on an unverified clock is not.
        clock_offset = None if (api_minute is None or minute is None) else minute - api_minute
        clock_verified = clock_offset is not None and abs(clock_offset) <= CLOCK_TOLERANCE_MIN
        clock_src = "api" if clock_verified else "clock"
        if clock_offset is not None and not clock_verified:
            log.warning(f"clock off by {clock_offset:+d}min on {title[:44]}: "
                        f"ours={minute} api={api_minute}")

        # Second anchor, used when the api quota is gone — which is most of the
        # time. The ladder started moving when it should have, so PM's listed
        # start time is the real one. api-football wins where both are present.
        if not clock_verified and detected_ko is not None:
            ko_err = int((detected_ko - fx["kickoff"]).total_seconds() // 60)
            if abs(ko_err) <= KICKOFF_TOLERANCE_MIN:
                clock_verified, clock_src = True, "ladder"
            elif clock_offset is None:
                clock_offset = ko_err
                log.warning(f"kick-off ~{ko_err:+d}min off listed on {title[:44]}")

        goals = lower if certain else (api_goals if api_goals is not None else None)
        src = "both" if (certain and api_goals is not None) else \
              ("ladder" if certain else ("api" if api_goals is not None else None))

        pre = pre_cache.get(title)
        pre_src = "self" if pre is not None else None
        if pre is None and conn is not None:
            pre = _pre_over25_from_ticks(conn, title, fx["kickoff"])
            pre_src = "pm_ticks" if pre is not None else None
            if pre is not None:
                pre_cache[title] = pre

        # Two markets are watched per poll, one row each:
        #   needed=1  Over(goals + 0.5)  — the one-more-goal line
        #   needed=2  Over(goals + 1.5)  — the leveraged line
        # Both, because which one the strategy should buy depends on the FORM of
        # PM's error, which is not yet known. If PM misprices the goal RATE the
        # error compounds and the leveraged line carries roughly double the
        # edge; if it misprices each line flatly, the cheap line wins. Recording
        # only one of them makes that question unanswerable after the fact.
        # Books come from the CLOB — Gamma's price says nothing about executable
        # size, and an unexecutable quote is what turned the convergence
        # trader's paper P&L into fiction (db/028).
        # The rung that establishes the LOWER bound — Over(goals - 0.5), the
        # highest line Gamma quotes as already paid. Its book is fetched purely
        # to confirm the score off the CLOB rather than off Gamma. The rung that
        # establishes the UPPER bound is Over(goals + 0.5), which is already the
        # needed=1 target, so it costs no extra call.
        settled_cell = cells.get(goals - 0.5) if (certain and goals) else None

        state = dict(title=title, fx=fx, wall=wall, minute=minute, ladder=ladder,
                     pre=pre, pre_src=pre_src, lower=lower, upper=upper,
                     certain=certain, src=src, api_goals=api_goals, goals=goals,
                     api_minute=api_minute, clock_offset=clock_offset,
                     clock_verified=clock_verified, clock_src=clock_src,
                     settled_cell=settled_cell)
        for needed in (1, 2):
            target = None if goals is None else cells.get(goals + needed - 0.5)
            pending.append((state, needed, target))

    # One fetch per distinct token per cycle — the needed=1 target of one
    # fixture-state is the needed=2 target of none, but the boundary rung is
    # shared by both rows of a fixture, so dedupe before hitting the CLOB.
    wanted: dict[str, dict] = {}
    for s, _needed, target in pending:
        for cell in (target, s.get("settled_cell")):
            if cell and cell.get("token_id"):
                wanted.setdefault(cell["token_id"], cell)
    books_by_token: dict[str, dict] = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for tok, book in zip(wanted, pool.map(_fetch_book, wanted.values())):
                if book:
                    books_by_token[tok] = book

    for s, needed, target in pending:
        book = books_by_token.get(target["token_id"]) if (target and target.get("token_id")) else None
        fair = fair_n = edge = fee = None
        enter = False

        sc = s.get("settled_cell")
        lo_book = books_by_token.get(sc["token_id"]) if (sc and sc.get("token_id")) else None
        book_confirmed = confirm_score_books(s["certain"], s["goals"], needed, book, lo_book)

        if book and s["goals"] is not None:
            fair, fair_n = lgt.lookup(table, s["minute"], s["goals"], s["pre"], needed=needed)
            if fair is not None and book["best_ask"] is not None:
                fee = taker_fee_pp(book["best_ask"])
                edge = 100.0 * (fair - book["best_ask"]) - fee
                # would_enter stays on the one-more-goal line until the shape of
                # PM's error is measured. The leveraged rows are observation.
                #
                # clock_verified and book_confirmed are both hard requirements.
                # Every number downstream of the minute and the score — the fair
                # value, and therefore the edge — is only as good as those two,
                # and the v1 series is what an ungated version produces: 4,916
                # eligible polls whose edge measured our own clock error.
                enter = bool(
                    needed == 1
                    and edge >= MIN_EDGE_PP
                    and s["certain"]
                    and s["clock_verified"]
                    and book_confirmed
                    and s["pre"] is not None and s["pre"] >= MIN_PRE_OVER25
                    and (book["ask_depth_usd"] or 0) >= MIN_DEPTH_USD
                )
        rows.append(_row(s["title"], "live", s["fx"], s["wall"], s["minute"], s["ladder"],
                         pre_over25=s["pre"], pre_src=s["pre_src"],
                         lower=s["lower"], upper=s["upper"], certain=s["certain"],
                         src=s["src"], api_goals=s["api_goals"], target=book,
                         needed=needed, fair=fair, fair_n=fair_n, fee=fee,
                         edge=edge, enter=enter,
                         api_minute=s["api_minute"], clock_offset=s["clock_offset"],
                         clock_verified=s["clock_verified"], clock_src=s["clock_src"],
                         book_confirmed=book_confirmed))
    return rows


def _row(title, phase, fx, wall, minute, ladder, *, pre_over25=None, pre_src=None,
         lower=None, upper=None, certain=False, src=None, api_goals=None, target=None,
         needed=None, fair=None, fair_n=None, fee=None, edge=None, enter=False,
         api_minute=None, clock_offset=None, clock_verified=False,
         clock_src="clock", book_confirmed=False) -> dict:
    return {
        "obs_version": OBS_VERSION,
        "api_minute": api_minute,
        "clock_offset_min": clock_offset,
        "clock_verified": clock_verified,
        "book_confirmed": book_confirmed,
        "phase": phase,
        "event_title": title,
        "condition_id": target["condition_id"] if target else None,
        "token_id": target["token_id"] if target else None,
        "kickoff_utc": fx["kickoff"],
        "wall_minute": wall,
        "game_minute": minute,
        "minute_source": clock_src,
        "goals_lower": lower,
        "goals_upper": upper,
        "goals_certain": certain,
        "score_source": src,
        "api_goals": api_goals,
        "ladder": Json({str(k): v for k, v in sorted(ladder.items())}),
        "pre_over25": pre_over25,
        "pre_over25_src": pre_src,
        "goals_needed": needed,
        "target_line": target["line"] if target else None,
        "best_bid": target["best_bid"] if target else None,
        "best_ask": target["best_ask"] if target else None,
        "bid_depth_usd": target["bid_depth_usd"] if target else None,
        "ask_depth_usd": target["ask_depth_usd"] if target else None,
        "fair_prob": fair,
        "fair_n": fair_n,
        "fee_pp": fee,
        "edge_pp": edge,
        "would_enter": enter,
    }


def _write(conn, rows: list[dict]) -> None:
    execute_batch(conn.cursor(), """
        INSERT INTO late_goal_observations
            (phase, event_title, condition_id, token_id, kickoff_utc, wall_minute,
             game_minute, minute_source, goals_lower, goals_upper, goals_certain,
             score_source, api_goals, ladder, pre_over25, pre_over25_src,
             goals_needed, target_line,
             best_bid, best_ask, bid_depth_usd, ask_depth_usd, fair_prob, fair_n,
             fee_pp, edge_pp, would_enter,
             obs_version, api_minute, clock_offset_min, clock_verified, book_confirmed)
        VALUES
            (%(phase)s, %(event_title)s, %(condition_id)s, %(token_id)s, %(kickoff_utc)s,
             %(wall_minute)s, %(game_minute)s, %(minute_source)s, %(goals_lower)s,
             %(goals_upper)s, %(goals_certain)s, %(score_source)s, %(api_goals)s,
             %(ladder)s, %(pre_over25)s, %(pre_over25_src)s,
             %(goals_needed)s, %(target_line)s,
             %(best_bid)s, %(best_ask)s, %(bid_depth_usd)s, %(ask_depth_usd)s,
             %(fair_prob)s, %(fair_n)s, %(fee_pp)s, %(edge_pp)s, %(would_enter)s,
             %(obs_version)s, %(api_minute)s, %(clock_offset_min)s,
             %(clock_verified)s, %(book_confirmed)s)
    """, rows, page_size=200)
    conn.commit()


# ── settlement ───────────────────────────────────────────────────────────────

def settle(conn) -> int:
    """Backfill final goals from the ladder's own end state.

    Once every over line has resolved, the highest settled line IS the final
    total, so no external results feed is needed for the ones we watched. Rows
    whose ladder never resolved stay open rather than being guessed at.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, token_id, target_line
            FROM late_goal_observations
            WHERE settled_at IS NULL AND phase = 'live' AND token_id IS NOT NULL
              AND kickoff_utc < now() - interval '3 hours'
            ORDER BY id LIMIT 500
        """)
        pending = cur.fetchall()

    if not pending:
        return 0

    def _price(token_id):
        try:
            r = requests.get("https://clob.polymarket.com/prices-history",
                             params={"market": token_id, "interval": "max", "fidelity": 60},
                             timeout=12)
            hist = (r.json() or {}).get("history") or []
            return _f(hist[-1].get("p")) if hist else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        finals = list(pool.map(lambda p: _price(p[1]), pending))

    # A resolved token's last price is 0 or 1. Anything in between means the
    # market stopped printing while still live — abandoned, cancelled, or simply
    # never resolved — and `last >= 0.5` there is not an outcome, it is a coin
    # flip biased by whatever the price happened to be. On a 60-token sample of
    # the v1 series 8 (13%) finished mid-book, including finals of 0.54 and 0.41.
    # Those rows stay unsettled: a missing label costs sample, a wrong one costs
    # the answer.
    updates, unresolved = [], 0
    for (obs_id, _tok, line), last in zip(pending, finals):
        if last is None:
            continue
        if RESOLVED_LO < last < RESOLVED_HI:
            unresolved += 1
            continue
        # The target line pays iff one more goal arrived after the observation.
        updates.append({"id": obs_id, "hit": last >= RESOLVED_HI})
    if unresolved:
        log.info(f"left {unresolved} rows unsettled — token never resolved")

    if updates:
        execute_batch(conn.cursor(), """
            UPDATE late_goal_observations
               SET goal_after = %(hit)s, settled_at = now()
             WHERE id = %(id)s
        """, updates, page_size=200)
        conn.commit()
    return len(updates)


# ── report ───────────────────────────────────────────────────────────────────

def report(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT obs_version,
                   count(*) FILTER (WHERE phase='live'),
                   count(*) FILTER (WHERE phase='prematch'),
                   count(DISTINCT event_title) FILTER (WHERE phase='live'),
                   count(*) FILTER (WHERE goals_certain),
                   count(*) FILTER (WHERE clock_verified),
                   count(*) FILTER (WHERE book_confirmed),
                   count(*) FILTER (WHERE would_enter),
                   count(*) FILTER (WHERE settled_at IS NOT NULL)
            FROM late_goal_observations GROUP BY 1 ORDER BY 1
        """)
        print(f"\n{'ver':>3} {'live':>7} {'prematch':>9} {'fixtures':>9} {'certain':>8} "
              f"{'clock_ok':>9} {'book_ok':>8} {'enter':>6} {'settled':>8}")
        for v, live, pre, events, certain, clk, bk, enter, settled in cur.fetchall():
            print(f"{v:>3} {live:>7} {pre:>9} {events:>9} {certain:>8} "
                  f"{clk:>9} {bk:>8} {enter:>6} {settled:>8}")
        print("\n  v1 = pre-2026-08-04. Clock unverified and score unconfirmed by the CLOB;")
        print("  0.96 goals on average arrived after its 'minute 90'. Excluded below.")

        cur.execute("""
            SELECT count(*) n,
                   round(avg(fair_prob)::numeric, 4)  fair,
                   round(avg(best_ask)::numeric, 4)   ask,
                   round(avg(goal_after::int)::numeric, 4) realised
            FROM late_goal_observations
            WHERE obs_version >= 2 AND phase='live' AND goals_certain
              AND clock_verified AND book_confirmed
              AND settled_at IS NOT NULL AND fair_prob IS NOT NULL
        """)
        n, fair, ask, realised = cur.fetchone()
        print(f"\nv2 settled, clock-verified and book-confirmed: n={n}")
        if n:
            print(f"  our fair   {fair}")
            print(f"  PM ask     {ask}")
            print(f"  realised   {realised}")
        print("\n  (200-selection rule: verdict needs n>=200 AND a CI clear of 0 "
              "after the fee. Nothing below that is a result.)")

        # The clock error itself is now a measurable quantity rather than a
        # guess. Every row where api-football answered contributes one reading.
        cur.execute("""
            SELECT count(*), round(avg(clock_offset_min)::numeric, 1),
                   min(clock_offset_min), max(clock_offset_min),
                   count(*) FILTER (WHERE abs(clock_offset_min) > 3)
            FROM late_goal_observations
            WHERE clock_offset_min IS NOT NULL
        """)
        n, avg, lo, hi, bad = cur.fetchone()
        if n:
            print(f"\nclock offset (ours - api-football), n={n}: "
                  f"avg {avg:+} min, range {lo:+}..{hi:+}, "
                  f"{bad} readings ({100*bad/n:.0f}%) outside +-3")


# ── main ─────────────────────────────────────────────────────────────────────

def run(once: bool, dry_run: bool, interval: int) -> None:
    table = lgt.load()
    log.info(f"fair-value table built {table['built_at']} — {len(table['cells'])} cells")

    conn = None if dry_run else _conn()
    pre_cache: dict[str, float] = {}
    # Per-fixture kick-off detection state. Survives across cycles by design —
    # the baseline has to be laid down before kick-off to be worth anything.
    clock_state: dict[str, dict] = {}
    events: list[dict] = []
    api_scores: dict[str, int] = {}
    last_markets = last_api = 0.0

    while True:
        t0 = time.time()
        if t0 - last_markets > REFRESH_MARKETS_S or not events:
            events = _fetch_events()
            last_markets = t0
            # Drop kick-off state for fixtures that have aged out of the +-4h
            # universe, or the dict grows for as long as the daemon runs.
            alive = {fx["title"] for fx in events}
            for gone in [k for k in clock_state if k not in alive]:
                del clock_state[gone]
            log.info(f"universe refreshed -> {len(events)} football fixtures within +-4h")
        if t0 - last_api > API_CHECK_S:
            api_scores = _api_scores()
            last_api = t0

        rows = observe(table, events, api_scores, pre_cache, conn, clock_state)
        live = [r for r in rows if r["phase"] == "live"]
        enter = [r for r in live if r["would_enter"]]

        if rows and conn is not None:
            _write(conn, rows)

        for r in enter:
            log.info(
                f"  ENTER  {r['event_title'][:44]:44s} {r['game_minute']}' "
                f"O{r['target_line']} ask={r['best_ask']:.3f} "
                f"({1/r['best_ask']:.2f})  fair={r['fair_prob']:.3f} "
                f"({1/r['fair_prob']:.2f})  edge={r['edge_pp']:+.1f}pp"
            )
        log.info(f"rows={len(rows):3d} live={len(live):3d} "
                 f"certain={sum(1 for r in live if r['goals_certain']):3d} "
                 f"clock_ok={sum(1 for r in live if r['clock_verified']):3d} "
                 f"book_ok={sum(1 for r in live if r['book_confirmed']):3d} "
                 f"enter={len(enter):2d}  {time.time()-t0:.1f}s")

        if once:
            break
        time.sleep(max(1.0, interval - (time.time() - t0)))

    if conn:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper-only late-goal observation layer")
    ap.add_argument("--once", action="store_true", help="single cycle then exit")
    ap.add_argument("--dry-run", action="store_true", help="no DB writes")
    ap.add_argument("--interval", type=int, default=CYCLE_S)
    ap.add_argument("--report", action="store_true", help="summarise what has been collected")
    ap.add_argument("--settle", action="store_true", help="backfill outcomes and exit")
    args = ap.parse_args()

    if not DATABASE_URL and not args.dry_run:
        raise SystemExit("DATABASE_URL not set")

    if args.report:
        conn = _conn()
        report(conn)
        conn.close()
        return
    if args.settle:
        conn = _conn()
        log.info(f"settled {settle(conn)} observations")
        conn.close()
        return

    try:
        run(args.once, args.dry_run, args.interval)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
