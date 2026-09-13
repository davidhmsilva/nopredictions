"""lab_strategy_runner.py — paper-trade the strategies users built in the Lab.

A user writes a theory in the Lab, sees its backtest, saves it (`strategies`
with source='lab', db/049) and switches it on. This is what "on" means: every
cycle, each running spec is held against today's Polymarket football boards,
and a fixture that fits gets one paper trade — 1u at the real CLOB ask, logged
before kickoff, settled on the token that was bought.

    cd agent && source ../ingest/.venv/bin/activate
    python lab_strategy_runner.py --once --dry-run    # one cycle, no writes
    python lab_strategy_runner.py --once              # one cycle
    python lab_strategy_runner.py --settle            # settle finished trades

Driven by cron through cron_guard.sh — a short job that exits, so it can never
stack the way a forever-loop can.

WHAT IT WILL NOT DO, each a way the live trade would silently stop being the
rule the user tested:

  * Trade a competition the backtest never saw. "All leagues" in the Lab means
    the 22 leagues in bt_features, not every board Polymarket lists.
  * Enter early. The backtest bought at Pinnacle's CLOSE, so entries wait for
    the last ENTRY_WINDOW_MIN minutes before kickoff.
  * Trust Polymarket's title order for home and away. NBA titles are
    "Away vs. Home" and a side error inverts the bet rather than blunting it,
    so any rule that depends on sides (home/away, favourite, a named team)
    needs ESPN to confirm the orientation or the fixture is skipped.
  * Buy into a broken book. The 100-game review found the spread, not the
    depth, is what separates a price from a parked order.
  * Settle by "first outcome won". The resolver does that, which is a LOSS
    recorded as a win on an Under. Settled here by the token actually bought.
  * Evaluate a filter it cannot compute (recent form, rest days). Such a spec
    is paused with the reason written to `run_blocker`, where the site shows it.

Paper only. Nothing in this file can place an order.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

import db_txn                                                      # noqa: E402
import espn_stats                                                  # noqa: E402
from fixture_match import MIN_SIDE_SCORE, team_score               # noqa: E402
from late_goals_observer import _fetch_book                        # noqa: E402

log = logging.getLogger("lab_runner")

DATABASE_URL = os.getenv("DATABASE_URL")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# The season being traded, as a Lab season START year (2026-27 → 2026).
CURRENT_SEASON = 2026
ENTRY_WINDOW_MIN = 45          # enter only this close to kickoff
MAX_SPREAD = 0.05              # ask − bid; wider and the quote is not a price
MIN_ASK_DEPTH_USD = 50.0       # notional on the top five ask levels
MIN_ASK, MAX_ASK = 0.02, 0.98
STAKE_UNITS = 1.0
GAMMA_PAGES = 20               # 100 events a page (Gamma caps `limit` at 100)

# ⚠️ Mirrored in site/app/lib/agents.ts (LIVE_UNSUPPORTED). This file is the
# authority: it re-checks every running spec and writes run_blocker itself.
LIVE_UNSUPPORTED = (
    "home_rest_days", "away_rest_days",
    "home_form_pts5", "away_form_pts5",
    "home_avg_tg5", "away_avg_tg5",
)

# Lab league code → the Polymarket competition tags that mean it, by slug and by
# label (lowercased). Built from the tags actually stored in pm_markets and a
# live board, 2026-09-11. Anything unlisted is NOT traded rather than guessed:
# "Premier League (Kazakhstan)" and "Brazil Serie A" are real labels, which is
# exactly why nothing here matches on a substring.
PM_COMPETITIONS: dict[str, tuple[set[str], set[str]]] = {
    "ENG-PR":  ({"epl", "premier-league"}, {"epl", "premier league", "english premier league"}),
    "ENG-CH":  ({"efl-championship", "championship"}, {"efl championship", "championship"}),
    "ENG-L1":  ({"efl-league-one", "league-one"}, {"efl league one", "league one"}),
    "ENG-L2":  ({"efl-league-two", "league-two"}, {"efl league two", "league two"}),
    "ENG-CON": ({"national-league"}, {"national league"}),
    "ESP-LL":  ({"laliga", "la-liga"}, {"la liga", "laliga"}),
    "ESP-L2":  ({"laliga-2", "la-liga-2", "segunda-division"}, {"la liga 2", "laliga 2", "segunda división", "segunda division"}),
    "ITA-SA":  ({"sea", "serie-a"}, {"serie a"}),
    "ITA-SB":  ({"seb", "serie-b"}, {"serie b"}),
    "GER-BL1": ({"bundesliga"}, {"bundesliga"}),
    "GER-BL2": ({"bundesliga-2", "2-bundesliga"}, {"bundesliga 2", "2. bundesliga"}),
    "FRA-L1":  ({"ligue-1"}, {"ligue 1"}),
    "FRA-L2":  ({"ligue-2"}, {"ligue 2"}),
    "NED-ED":  ({"eredivisie"}, {"eredivisie"}),
    "POR-PL":  ({"primeira-liga", "liga-portugal"}, {"primeira liga", "liga portugal"}),
    "BEL-JPL": ({"belgium-pro-league", "jupiler-pro-league"}, {"belgium pro league", "jupiler pro league"}),
    "TUR-SL":  ({"super-lig", "turkey-super-lig"}, {"süper lig", "super lig"}),
    "GRE-SL":  ({"greece-super-league"}, {"greece super league", "super league greece"}),
    "SCO-PR":  ({"scottish-premiership"}, {"scottish premiership"}),
    "SCO-CH":  ({"scottish-championship"}, {"scottish championship"}),
    "SCO-L1":  ({"scottish-league-one"}, {"scottish league one"}),
    "SCO-L2":  ({"scottish-league-two"}, {"scottish league two"}),
}
LAB_CODES = frozenset(PM_COMPETITIONS)

# Where ESPN can confirm home/away. A league missing here can still run a rule
# that does not care about sides (draw, over, under), and nothing else.
ESPN_CODE = {
    "ENG-PR": "eng.1", "ENG-CH": "eng.2", "ENG-L1": "eng.3", "ENG-L2": "eng.4",
    "ESP-LL": "esp.1", "ESP-L2": "esp.2", "ITA-SA": "ita.1", "ITA-SB": "ita.2",
    "GER-BL1": "ger.1", "GER-BL2": "ger.2", "FRA-L1": "fra.1", "FRA-L2": "fra.2",
    "NED-ED": "ned.1", "POR-PL": "por.1", "BEL-JPL": "bel.1", "TUR-SL": "tur.1",
    "SCO-PR": "sco.1",
}

_TITLE_RE = re.compile(r"^(.+?)\s+vs\.?\s+(.+?)$", re.I)
_WIN_RE = re.compile(r"^will\s+(?P<team>.+?)\s+win(?:\s+on\s+[\d-]+)?\??$", re.I)
_DRAW_RE = re.compile(r"\bdraw\b", re.I)
_OU25_RE = re.compile(r"\bO/U\s*2\.5\b", re.I)


# ── the spec ─────────────────────────────────────────────────────────────────

def run_blocker(spec: dict) -> str | None:
    """Why this spec cannot run live, or None. Same wording as the site."""
    if spec.get("market") not in ("1x2", "ou25"):
        return ("NBA theories are backtest-only: the NBA data ends in 2021-22 and "
                "there are no NBA boards this agent trades.")
    used = [f for f in LIVE_UNSUPPORTED
            if spec.get(f"{f}_min") is not None or spec.get(f"{f}_max") is not None]
    if used:
        return (f"Uses {', '.join(f.replace('_', ' ') for f in used)} — the live agent "
                "cannot compute those yet, so it would be trading a different rule "
                "from the one you tested.")
    end = spec.get("season_end")
    if end is not None and end < CURRENT_SEASON - 1:
        return (f"Tested only up to the {end}-{str(end + 1)[2:]} season; running it "
                "today would trade outside what was tested.")
    return None


def needs_sides(spec: dict) -> bool:
    return (spec.get("side") in ("home", "away") or bool(spec.get("fav_status"))
            or bool(spec.get("home_team")) or bool(spec.get("away_team")))


# ── reading a Polymarket event ───────────────────────────────────────────────

def _jl(v) -> list:
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def competition_code(event: dict) -> str | None:
    """The one Lab league this event belongs to, or None — including when its
    tags point at two different leagues, which is ambiguity, not a choice."""
    hits: set[str] = set()
    for t in event.get("tags") or []:
        slug = str(t.get("slug") or "").strip().lower()
        label = str(t.get("label") or "").strip().lower()
        for code, (slugs, labels) in PM_COMPETITIONS.items():
            if slug in slugs or label in labels:
                hits.add(code)
    return hits.pop() if len(hits) == 1 else None


def is_full_match_event(title: str) -> bool:
    """The main fixture event, or its "More Markets" sibling.

    Polymarket splits one fixture across sibling events — "A vs. B - Halftime
    Result", "- Exact Score", "- Second Half Result". Those carry questions a
    draw pattern matches happily ("Will the first half end in a draw?"), and a
    Lab spec is always about the full 90 minutes."""
    m = re.search(r"\s+-\s+(.+)$", title or "")
    return m is None or m.group(1).strip().lower() == "more markets"


def teams_from_title(title: str) -> tuple[str, str] | None:
    clean = re.sub(r"\s+-\s+.*$", "", title or "").strip()
    m = _TITLE_RE.match(clean)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def kickoff_of(event: dict) -> datetime | None:
    raw = event.get("startTime") or next(
        (m.get("gameStartTime") for m in event.get("markets") or [] if m.get("gameStartTime")), None)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def _leg(m: dict, outcome: str) -> dict | None:
    toks, outs, prices = _jl(m.get("clobTokenIds")), _jl(m.get("outcomes")), _jl(m.get("outcomePrices"))
    labels = [str(o).strip().lower() for o in outs]
    if len(toks) != len(labels) or outcome not in labels:
        return None
    i = labels.index(outcome)
    return {"token_id": str(toks[i]), "mid": _f(prices[i]) if i < len(prices) else None,
            "condition_id": m.get("conditionId"), "question": m.get("question") or "",
            "outcome": outs[i]}


def parse_markets(event: dict) -> dict:
    """The markets a Lab spec can buy: the three 1X2 legs and O/U 2.5.

    Families come from `sportsMarketType`, never the question text — "… O/U 2.5
    Corners" matches a goals pattern perfectly. Over/Under come from the outcome
    LABELS, never their order."""
    out: dict = {"win": {}, "draw": None, "ou25": None}
    for m in event.get("markets") or []:
        if m.get("closed") or m.get("active") is False:
            continue
        kind = str(m.get("sportsMarketType") or "").lower()
        q = str(m.get("question") or "").strip()
        rem = q.split(": ", 1)[1] if ": " in q else q
        if kind == "moneyline":
            leg = _leg(m, "yes")
            if leg is None:
                continue
            if _DRAW_RE.search(rem):
                out["draw"] = leg
            else:
                w = _WIN_RE.match(rem)
                if w:
                    out["win"][w.group("team").strip()] = leg
        elif kind == "totals" and _OU25_RE.search(q):
            over, under = _leg(m, "over"), _leg(m, "under")
            if over and under:
                out["ou25"] = {"over": over, "under": under}
    return out


def orientation(pm_home: str, pm_away: str, fixtures: list) -> tuple[str | None, object | None]:
    """'same' when Polymarket's first team is the real home side, 'swapped' when
    it is the away side, None when ESPN cannot say — a missing fixture, or names
    that fit both ways."""
    f = espn_stats.match_fixture(pm_home, pm_away, fixtures)
    if f is None:
        return None, None
    same = team_score(pm_home, f.home) >= MIN_SIDE_SCORE and team_score(pm_away, f.away) >= MIN_SIDE_SCORE
    swap = team_score(pm_home, f.away) >= MIN_SIDE_SCORE and team_score(pm_away, f.home) >= MIN_SIDE_SCORE
    if same and not swap:
        return "same", f
    if swap and not same:
        return "swapped", f
    return None, f


@dataclass
class EventView:
    code: str
    title: str
    kickoff: datetime
    markets: dict
    home: str | None = None          # the REAL home side, once ESPN confirmed it
    away: str | None = None


def _leg_for(team: str, legs: dict) -> dict | None:
    """The win leg whose question names `team` — alias-aware, and None on a tie."""
    scored = sorted(((team_score(name, team), leg) for name, leg in legs.items()),
                    key=lambda x: -x[0])
    if not scored or scored[0][0] < MIN_SIDE_SCORE:
        return None
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9:
        return None
    return scored[0][1]


def pick(spec: dict, ev: EventView) -> tuple[dict | None, str, str]:
    """(leg to buy, outcome label, reason). The leg is None when the fixture does
    not fit; the reason says which condition failed, for --dry-run."""
    leagues = spec.get("leagues") or LAB_CODES
    if ev.code not in leagues:
        return None, "", "league"
    start = spec.get("season_start")
    if start is not None and start > CURRENT_SEASON:
        return None, "", "season"
    if needs_sides(spec) and ev.home is None:
        return None, "", "home/away not confirmed by ESPN"
    if spec.get("home_team") and team_score(spec["home_team"], ev.home) < MIN_SIDE_SCORE:
        return None, "", "home team"
    if spec.get("away_team") and team_score(spec["away_team"], ev.away) < MIN_SIDE_SCORE:
        return None, "", "away team"

    side = spec.get("side")
    if spec.get("market") == "ou25":
        ou = ev.markets.get("ou25")
        if not ou or side not in ("over", "under"):
            return None, "", "no O/U 2.5"
        return ou[side], f"{'Over' if side == 'over' else 'Under'} 2.5 — {ev.title}", "ok"

    if spec.get("market") != "1x2":
        return None, "", "market"
    if side == "draw":
        leg = ev.markets.get("draw")
        return (leg, f"Draw — {ev.title}", "ok") if leg else (None, "", "no draw leg")
    if side not in ("home", "away"):
        return None, "", "side"

    team, other = (ev.home, ev.away) if side == "home" else (ev.away, ev.home)
    leg = _leg_for(team, ev.markets.get("win") or {})
    if leg is None:
        return None, "", "no leg for the backed team"
    fav = spec.get("fav_status")
    if fav:
        opp = _leg_for(other, ev.markets.get("win") or {})
        if opp is None or leg.get("mid") is None or opp.get("mid") is None:
            return None, "", "favourite unknown"
        is_fav = leg["mid"] > opp["mid"]
        if (fav == "favorite") != is_fav:
            return None, "", "favourite filter"
    return leg, f"{team} to win — {ev.title}", "ok"


def book_verdict(book: dict | None, spec: dict) -> str | None:
    """None when the quote is a price this spec may buy at; else why not."""
    if not book:
        return "no two-sided book"
    ask, bid = book.get("best_ask"), book.get("best_bid")
    if ask is None or bid is None or not (MIN_ASK < ask < MAX_ASK):
        return "ask out of range"
    if ask - bid > MAX_SPREAD:
        return f"spread {ask - bid:.2f} > {MAX_SPREAD}"
    if (book.get("ask_depth_usd") or 0) < MIN_ASK_DEPTH_USD:
        return f"ask depth ${book.get('ask_depth_usd') or 0:.0f} < ${MIN_ASK_DEPTH_USD:.0f}"
    odds = 1.0 / ask
    lo, hi = spec.get("odds_min"), spec.get("odds_max")
    if lo is not None and odds < lo:
        return f"odds {odds:.2f} < {lo}"
    if hi is not None and odds > hi:
        return f"odds {odds:.2f} > {hi}"
    return None


# ── the network ──────────────────────────────────────────────────────────────

def fetch_upcoming(now: datetime) -> list[dict]:
    """Soccer events kicking off inside the entry window, not yet started.

    Bounded by `end_date_*` given as plain DATES. A fixture event's `endDate`
    equals its kick-off, so this is a kick-off filter in all but name; and Gamma
    silently drops negRisk match events when the bound is an ISO timestamp (see
    paper_trader.fetch_pm_markets_today). Unbounded, `order=startTime` opens on
    hundreds of stale, never-closed events from February — twelve pages in it
    had not reached September, so the first version of this saw 0 fixtures in a
    24-hour window with 863 on the board (2026-09-11). The window does the exact
    bounding, client-side."""
    horizon = now + timedelta(minutes=ENTRY_WINDOW_MIN)
    params = {
        "tag_slug": "soccer", "closed": "false", "limit": 100,
        "order": "startTime", "ascending": "true",
        "end_date_min": now.strftime("%Y-%m-%d"),
        "end_date_max": (horizon + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    out: list[dict] = []
    for page in range(GAMMA_PAGES):
        try:
            r = requests.get(f"{GAMMA}/events", params={**params, "offset": page * 100}, timeout=20)
            r.raise_for_status()
            batch = r.json()
        except Exception as exc:                        # noqa: BLE001
            log.warning(f"[lab_runner] gamma page {page}: {exc}")
            break
        if not isinstance(batch, list) or not batch:
            break
        for ev in batch:
            ko = kickoff_of(ev)
            if ko and now < ko <= horizon and not ev.get("live") and not ev.get("ended"):
                out.append(ev)
        last = kickoff_of(batch[-1])
        if len(batch) < 100 or (last and last > horizon):
            break
    return out


def _clob_winner(condition_id: str) -> dict[str, bool] | None:
    """{token_id: won} once the market has a winner, else None."""
    try:
        d = requests.get(f"{CLOB}/markets/{condition_id}", timeout=10).json()
    except Exception:                                   # noqa: BLE001
        return None
    toks = d.get("tokens") or []
    if not toks or not any(t.get("winner") for t in toks):
        return None
    return {str(t.get("token_id")): bool(t.get("winner")) for t in toks}


# ── the database ─────────────────────────────────────────────────────────────

def _conn():
    return db_txn.connect(DATABASE_URL)


def running_strategies(conn) -> list[dict]:
    """Running Lab specs. Any that cannot run live is paused here, with the
    reason written where its owner will see it."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, name, theory, spec FROM strategies
                        WHERE source = 'lab' AND run_status = 'running' AND retired_at IS NULL""")
        rows = [dict(r) for r in cur.fetchall()]
    ok = []
    for r in rows:
        spec = r["spec"] if isinstance(r["spec"], dict) else json.loads(r["spec"] or "{}")
        r["spec"] = spec
        why = run_blocker(spec)
        if why:
            with conn.cursor() as cur:
                cur.execute("""UPDATE strategies SET run_status = 'paused', run_blocker = %s
                                WHERE id = %s""", (why, r["id"]))
            log.info(f"[lab_runner] paused #{r['id']}: {why}")
            continue
        ok.append(r)
    return ok


def already_in(conn, strategy_id: int, condition_ids: list[str]) -> bool:
    if not condition_ids:
        return False
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM paper_trades pt JOIN pm_markets pm ON pm.id = pt.market_id
                        WHERE pt.strategy_id = %s AND pm.platform = 'polymarket'
                          AND pm.external_id = ANY(%s) LIMIT 1""", (strategy_id, condition_ids))
        return cur.fetchone() is not None


def write_trade(conn, strat: dict, ev: EventView, leg: dict, label: str, book: dict) -> int:
    ask = float(book["best_ask"])
    meta = {"event_title": ev.title, "competition": ev.code, "token_id": leg["token_id"],
            "outcome": leg["outcome"], "home": ev.home, "away": ev.away}
    reasoning = (
        f"Lab agent — \"{(strat.get('theory') or strat['name'])[:200]}\". {label}, "
        f"{ev.code}, kick-off {ev.kickoff:%Y-%m-%d %H:%M}Z. Bought at the CLOB ask "
        f"{ask:.3f} ({1 / ask:.2f}), bid {book['best_bid']:.3f}, ask depth "
        f"${book['ask_depth_usd']:.0f}. 1u flat, PAPER."
    )
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pm_markets (platform, external_id, title, market_type,
                                        resolution_time, status, raw_metadata, ingested_at)
                VALUES ('polymarket', %s, %s, %s, %s, 'active', %s::jsonb, now())
                ON CONFLICT (platform, external_id) DO UPDATE
                   SET title = EXCLUDED.title, resolution_time = EXCLUDED.resolution_time
                RETURNING id""",
                (leg["condition_id"], leg["question"] or label,
                 "over_under" if "2.5" in label else "1x2", ev.kickoff, json.dumps(meta)))
            market_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO paper_trades (strategy_id, market_id, outcome, entry_price, entry_odds,
                                          stake_units, reasoning, pm_token_id, pm_live, placed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false, now()) RETURNING id""",
                (strat["id"], market_id, label, ask, round(1 / ask, 4), STAKE_UNITS,
                 reasoning, leg["token_id"]))
            return cur.fetchone()[0]


# ── the cycle ────────────────────────────────────────────────────────────────

def run_once(dry_run: bool = False, adhoc_spec: dict | None = None) -> int:
    conn = _conn()
    if adhoc_spec is not None:
        # --spec: hold a spec against today's boards without a saved agent.
        # Always a dry run — an ad-hoc spec has no strategy row to trade under.
        strategies = [{"id": 0, "name": "ad-hoc", "theory": None, "spec": adhoc_spec}]
        dry_run = True
    else:
        strategies = running_strategies(conn)
    if not strategies:
        log.info("[lab_runner] no running Lab agents")
        return 0

    now = datetime.now(timezone.utc)
    events = fetch_upcoming(now)
    espn_cache: dict[str, list] = {}
    entered = considered = 0
    for raw in events:
        if not is_full_match_event(raw.get("title") or ""):
            continue
        code = competition_code(raw)
        teams = teams_from_title(raw.get("title") or "")
        if not code or not teams:
            continue
        ev = EventView(code=code, title=raw.get("title") or "", kickoff=kickoff_of(raw),
                       markets=parse_markets(raw))
        if any(needs_sides(s["spec"]) for s in strategies) and code in ESPN_CODE:
            if code not in espn_cache:
                espn_cache[code] = espn_stats.fetch_league(ESPN_CODE[code])
            orient, _ = orientation(teams[0], teams[1], espn_cache[code])
            if orient == "same":
                ev.home, ev.away = teams
            elif orient == "swapped":
                ev.away, ev.home = teams

        for strat in strategies:
            leg, label, why = pick(strat["spec"], ev)
            if leg is None:
                continue
            considered += 1
            cids = [x["condition_id"] for x in (
                list((ev.markets.get("win") or {}).values())
                + [ev.markets.get("draw")]
                + list((ev.markets.get("ou25") or {}).values())) if x and x.get("condition_id")]
            if already_in(conn, strat["id"], cids):
                continue
            book = _fetch_book({"token_id": leg["token_id"]})
            bad = book_verdict(book, strat["spec"])
            if bad:
                log.info(f"[lab_runner] #{strat['id']} {label}: skip — {bad}")
                continue
            if dry_run:
                log.info(f"[lab_runner] DRY #{strat['id']} would buy {label} at {book['best_ask']:.3f}")
                continue
            tid = write_trade(conn, strat, ev, leg, label, book)
            entered += 1
            log.info(f"[lab_runner] #{strat['id']} ENTERED pt#{tid} {label} at {book['best_ask']:.3f}")

    log.info(f"[lab_runner] {len(strategies)} running · {len(events)} fixtures in the window · "
             f"{considered} fits · {entered} entered")
    return entered


def settle() -> int:
    conn = _conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pt.id, pt.pm_token_id, pt.stake_units, pt.entry_odds, pm.external_id
              FROM paper_trades pt
              JOIN strategies s ON s.id = pt.strategy_id
              JOIN pm_markets pm ON pm.id = pt.market_id
             WHERE s.source = 'lab' AND pt.result IS NULL
               AND pm.resolution_time < now() - interval '2 hours'""")
        pending = [dict(r) for r in cur.fetchall()]
    # Every network call first, no transaction open (db_txn.py).
    winners = {cid: _clob_winner(cid) for cid in {p["external_id"] for p in pending}}
    settled = 0
    for p in pending:
        w = winners.get(p["external_id"])
        if not w or p["pm_token_id"] not in w:
            continue
        won = w[p["pm_token_id"]]
        with conn.cursor() as cur:
            cur.execute("""UPDATE paper_trades
                              SET result = %s,
                                  payout_units = CASE WHEN %s THEN stake_units * entry_odds ELSE 0 END,
                                  resolved_at = now()
                            WHERE id = %s AND result IS NULL""",
                        ("won" if won else "lost", won, p["id"]))
        settled += 1
    log.info(f"[lab_runner] settled {settled} of {len(pending)} finished trades")
    return settled


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="one entry cycle (the default)")
    ap.add_argument("--dry-run", action="store_true", help="evaluate, write nothing")
    ap.add_argument("--settle", action="store_true", help="settle finished trades")
    ap.add_argument("--spec", help="a Lab spec as JSON, dry-run against today's boards")
    ap.add_argument("--window", type=int, help=f"minutes ahead to consider (default {ENTRY_WINDOW_MIN})")
    args = ap.parse_args()
    # force: late_goals_observer configures the root logger on import, and
    # without it every line here would carry that module's "[late_goals]" tag.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S", force=True)
    if args.window:
        globals()["ENTRY_WINDOW_MIN"] = args.window
    if args.settle:
        settle()
    else:
        run_once(dry_run=args.dry_run, adhoc_spec=json.loads(args.spec) if args.spec else None)


if __name__ == "__main__":
    main()
