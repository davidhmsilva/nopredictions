"""
close_maker_agent.py — Close Forecast (maker bids). PAPER, no orders.

H-STATS-CLOSE (#47), by the operator's decision (2026-10-10): the skill the
project is after is knowing where a price will CLOSE before the market gets
there. agent/close_model.py forecasts it from team statistics plus the price
itself; this agent acts on the forecast as a MAKER on Polymarket.

Each cycle (every 5 minutes):
  1. Every Polymarket 1X2 fixture kicking off 3-30 h from now, in one of the 22
     leagues the model was fitted on, with the real home side confirmed by ESPN
     (never Polymarket's title order — a side error inverts the bet).
  2. The venue's three mids from the CLOB book, normalised to 1. The model's
     probability for the home and away wins (close_model.fair_pair).
  3. Where the model sits >= GAP_MIN above a side's mid, a paper BID on that
     side's Yes token: one tick above the best bid when the spread allows,
     else at it, and never closer than GAP_MIN to the model. One bid per
     fixture, the bigger gap.
  4. A resting bid FILLS when the book's best ask comes down to it
     ('ask_at_bid') or a trade prints strictly below it ('traded_through'). A
     print exactly at it depends on queue position and is only recorded
     (`touched_at`). A fill writes a 1u paper trade at the bid, no fee.
  5. Inside 15 minutes of kick-off every order of the fixture gets the venue's
     close; resting bids expire at kick-off. Settlement from the CLOB winner
     flag, for filled and unfilled rows alike.

Why a maker. As a taker the entry (half-spread + fee, ~1.6pp) costs more than
the move the model predicts (+0.56pp CI[+0.25,+0.84] on 387 PM prices with a
>= 2pp gap, 2024-26). At the bid there is no fee and the half-spread is earned
instead of paid. What a bid risks is adverse selection: it fills when someone
wants to sell, which may be exactly when the forecast is wrong. The unfilled
rows stay in close_maker_orders with their close and payout, so the
filled-vs-unfilled gap measures that directly (finding-maker-adverse-selection
found -7pp pre-match on a model that did not update; this is a different
signal, and the reason to measure rather than assume).

Verdict gate: CLV first (close mid − bid on filled rows, tens to hundreds of
fills), then results (thousands). Never pool obs_versions.

    python close_maker_agent.py --once --dry-run   # the board and the bids it would post
    python close_maker_agent.py --once             # what the timer runs
    python close_maker_agent.py --settle
    python close_maker_agent.py --report
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
load_dotenv(os.path.join(HERE, "../ingest/.env"))

import close_model as cm                                           # noqa: E402
import db_txn                                                      # noqa: E402
from espn_stats import EspnFixture                                 # noqa: E402
from fixture_match import MIN_SIDE_SCORE, team_score               # noqa: E402
from lab_strategy_runner import (_leg_for, competition_code,      # noqa: E402
                                 is_full_match_event, kickoff_of, orientation,
                                 parse_markets, teams_from_title)

log = logging.getLogger("close_maker")

DATABASE_URL = os.getenv("DATABASE_URL")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"

STRATEGY_NAME = "Close Forecast — maker bids"
OWNER_FROM = "NFL Every Game"      # the new strategy is owned by whoever owns this one
HYPOTHESIS_PREFIX = "H-STATS-CLOSE"
OBS_VERSION = 1

WINDOW_MIN_H, WINDOW_MAX_H = 3.0, 30.0
GAP_MIN = 0.02            # model − normalised mid; the tested threshold
TICK = 0.01
MAX_SPREAD = 0.04         # on the side we bid; wider and the mid is not a price
MAX_LEG_SPREAD = 0.06     # on the other two legs, which only feed the normalisation
MID_SUM = (0.97, 1.05)    # three mids that do not sum near 1 are not one book
MIN_DEPTH_USD = 100.0     # bid + ask notional, top five levels, on the side we bid
MAX_TEAM_LAG_DAYS = 10    # a team whose last match trails its league's latest by more is missing data
MAX_LEAGUE_GAP_DAYS = 35  # a league silent this long before kick-off is off-season, or not being loaded
CLOSE_WINDOW_MIN = 15
STAKE_U = 1.0
GAMMA_PAGES = 20

ESPN_CODE = {
    "ENG-PR": "eng.1", "ENG-CH": "eng.2", "ENG-L1": "eng.3", "ENG-L2": "eng.4", "ENG-CON": "eng.5",
    "ESP-LL": "esp.1", "ESP-L2": "esp.2", "ITA-SA": "ita.1", "ITA-SB": "ita.2",
    "GER-BL1": "ger.1", "GER-BL2": "ger.2", "FRA-L1": "fra.1", "FRA-L2": "fra.2",
    "NED-ED": "ned.1", "POR-PL": "por.1", "BEL-JPL": "bel.1", "TUR-SL": "tur.1",
    "GRE-SL": "gre.1", "SCO-PR": "sco.1", "SCO-CH": "sco.2", "SCO-L1": "sco.3", "SCO-L2": "sco.4",
}
ET = ZoneInfo("America/New_York")


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── pure decisions (tested) ──────────────────────────────────────────────────

def book_of(raw: dict | None) -> dict | None:
    """Best bid/ask and top-five notional from a CLOB book, or None when a side is empty."""
    if not raw:
        return None
    bids = sorted(raw.get("bids") or [], key=lambda x: -(_f(x.get("price")) or 0))
    asks = sorted(raw.get("asks") or [], key=lambda x: (_f(x.get("price")) or 1))
    if not bids or not asks:
        return None
    notional = lambda side: sum((_f(x["price"]) or 0) * (_f(x["size"]) or 0) for x in side[:5])  # noqa: E731
    bid, ask = _f(bids[0]["price"]), _f(asks[0]["price"])
    if bid is None or ask is None or ask <= bid:
        return None
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": ask - bid,
            "bid_depth": notional(bids), "ask_depth": notional(asks)}


def normalised(books: dict[str, dict | None]) -> dict[str, float] | None:
    """The three mids scaled to sum to 1, or None when any leg has no usable
    book or the three do not look like one market."""
    if any(books.get(k) is None for k in ("home", "draw", "away")):
        return None
    if any(books[k]["spread"] > MAX_LEG_SPREAD + 1e-9 for k in ("home", "draw", "away")):
        return None
    s = sum(books[k]["mid"] for k in ("home", "draw", "away"))
    if not (MID_SUM[0] <= s <= MID_SUM[1]):
        return None
    return {k: books[k]["mid"] / s for k in ("home", "draw", "away")}


def bid_price(book: dict, q: float) -> float | None:
    """Our paper bid: one tick above the best bid when that still leaves a tick
    to the ask, else at the best bid; never within GAP_MIN of the model."""
    p = book["bid"] + TICK if book["ask"] - book["bid"] >= 2 * TICK - 1e-9 else book["bid"]
    p = round(p, 4)
    if q - p < GAP_MIN - 1e-9 or p <= 0:
        return None
    return p


def choose(q: dict[str, float], p: dict[str, float], books: dict[str, dict]) -> tuple[str, float, str] | None:
    """(side, bid price, reason) for the side with the bigger gap, or None with nothing posted."""
    best = None
    for side in ("home", "away"):
        gap = q[side] - p[side]
        if gap < GAP_MIN - 1e-9:
            continue
        b = books[side]
        if b["spread"] > MAX_SPREAD + 1e-9 or b["bid_depth"] + b["ask_depth"] < MIN_DEPTH_USD:
            continue
        price = bid_price(b, q[side])
        if price is None:
            continue
        if best is None or gap > best[3]:
            best = (side, price, f"gap {gap * 100:+.2f}pp", gap)
    return best[:3] if best else None


def fill_from(book: dict | None, trades: list[dict], yes_token: str, price: float,
              since_ts: float) -> tuple[str | None, float | None, float | None]:
    """(fill rule, fill time as epoch seconds, touched time) for a resting Yes bid at `price`.

    Trades are read as their Yes-equivalent price: a trade on the No token at y
    is the Yes side at 1 − y. Strictly below our bid means the book traded
    through it; exactly at it depends on the queue, so it is only 'touched'."""
    fill_t, touch_t = None, None
    for t in trades:
        ts = _f(t.get("timestamp"))
        px = _f(t.get("price"))
        if ts is None or px is None or ts <= since_ts:
            continue
        yes_px = px if str(t.get("asset")) == yes_token else 1.0 - px
        if yes_px < price - 1e-9:
            fill_t = ts if fill_t is None else min(fill_t, ts)
        elif abs(yes_px - price) < 1e-9:
            touch_t = ts if touch_t is None else min(touch_t, ts)
    if fill_t is not None:
        return "traded_through", fill_t, touch_t
    if book is not None and book["ask"] <= price + 1e-9:
        return "ask_at_bid", time.time(), touch_t
    return None, None, touch_t


# ── the network ──────────────────────────────────────────────────────────────

def fetch_events(now: datetime) -> list[dict]:
    """Soccer fixture events kicking off 3-30 h from now, not started."""
    lo, hi = now + timedelta(hours=WINDOW_MIN_H), now + timedelta(hours=WINDOW_MAX_H)
    params = {"tag_slug": "soccer", "closed": "false", "limit": 100, "order": "startTime",
              "ascending": "true", "end_date_min": now.strftime("%Y-%m-%d"),
              "end_date_max": (hi + timedelta(days=1)).strftime("%Y-%m-%d")}
    out = []
    for page in range(GAMMA_PAGES):
        try:
            r = requests.get(f"{GAMMA}/events", params={**params, "offset": page * 100}, timeout=20)
            r.raise_for_status()
            batch = r.json()
        except Exception as exc:                                     # noqa: BLE001
            log.warning(f"gamma page {page}: {exc}")
            break
        if not isinstance(batch, list) or not batch:
            break
        for ev in batch:
            ko = kickoff_of(ev)
            if ko and lo <= ko <= hi and not ev.get("live") and not ev.get("ended") \
                    and is_full_match_event(ev.get("title") or ""):
                out.append(ev)
        last = kickoff_of(batch[-1])
        if len(batch) < 100 or (last and last > hi):
            break
    return out


def fetch_books(tokens: list[str]) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for i in range(0, len(tokens), 400):
        chunk = tokens[i:i + 400]
        try:
            r = requests.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk], timeout=20)
            r.raise_for_status()
            for b in r.json() or []:
                out[str(b.get("asset_id"))] = book_of(b)
        except Exception as exc:                                     # noqa: BLE001
            log.warning(f"books: {exc}")
    return out


def fetch_trades(condition_id: str) -> list[dict]:
    try:
        r = requests.get(f"{DATA_API}/trades", params={"market": condition_id, "limit": 500,
                                                         "takerOnly": "true"}, timeout=15)
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) else []
    except Exception as exc:                                         # noqa: BLE001
        log.warning(f"trades {condition_id[:10]}: {exc}")
        return []


def espn_fixtures(code: str, days: list[str]) -> list[tuple[EspnFixture, datetime]]:
    """ESPN's fixtures for one league on the given US-Eastern days, with kick-off.
    No User-Agent: ESPN's edge refuses browser-shaped and custom ones."""
    out = []
    for day in days:
        try:
            r = requests.get(f"{ESPN}/{code}/scoreboard", params={"dates": day}, timeout=12)
            r.raise_for_status()
            evs = r.json().get("events") or []
        except Exception as exc:                                     # noqa: BLE001
            log.warning(f"espn {code} {day}: {exc}")
            continue
        for ev in evs:
            comp = (ev.get("competitions") or [{}])[0]
            sides = comp.get("competitors") or []
            home = next((s for s in sides if s.get("homeAway") == "home"), None)
            away = next((s for s in sides if s.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            try:
                ko = datetime.fromisoformat(str(ev.get("date")).replace("Z", "+00:00"))
            except ValueError:
                continue
            fx = EspnFixture(event_id=str(ev.get("id")), league_code=code, league=code,
                             home=str((home.get("team") or {}).get("displayName") or ""),
                             away=str((away.get("team") or {}).get("displayName") or ""),
                             minute=None, state="pre", detail="")
            out.append((fx, ko))
    return out


def clob_winner(condition_id: str) -> dict[str, float] | None:
    try:
        d = requests.get(f"{CLOB}/markets/{condition_id}", timeout=10).json()
    except Exception:                                                # noqa: BLE001
        return None
    toks = d.get("tokens") or []
    if any(t.get("winner") for t in toks):
        return {str(t["token_id"]): (1.0 if t.get("winner") else 0.0) for t in toks}
    if d.get("closed") and toks and all(abs((_f(t.get("price")) or 0) - 0.5) < 0.01 for t in toks):
        return {str(t["token_id"]): 0.5 for t in toks}
    return None


# ── team identity ────────────────────────────────────────────────────────────

class Teams:
    """Our team ids for ESPN's names, league by league. ESPN's own spelling
    first (site/app/lib/team_names.json, written from ESPN's team list), then
    the alias-aware scorer over canonical names and every stored alias. Only
    teams last seen in that league compete, and a tie resolves nothing."""

    def __init__(self, conn, states: dict):
        self.by_league: dict[str, list[tuple[int, list[str]]]] = {}
        names: dict[int, list[str]] = {}
        with conn.cursor() as cur:
            cur.execute("""SELECT t.id, t.canonical_name, COALESCE(array_agg(a.alias) FILTER (WHERE a.alias IS NOT NULL), '{}')
                             FROM teams t LEFT JOIN team_aliases a ON a.team_id = t.id GROUP BY 1, 2""")
            for tid, canon, aliases in cur.fetchall():
                names[tid] = [canon, *aliases]
        try:
            with open(os.path.join(HERE, "../site/app/lib/team_names.json")) as fh:
                for tid, d in json.load(fh).items():
                    names.setdefault(int(tid), []).extend(x for x in (d.get("name"), d.get("short")) if x)
        except OSError:
            pass
        for tid, s in states["teams"].items():
            lg = s.get("league")
            if lg in cm.STATS_LEAGUES and int(tid) in names:
                self.by_league.setdefault(lg, []).append((int(tid), names[int(tid)]))

    def resolve(self, league: str, name: str) -> int | None:
        scored = []
        for tid, ns in self.by_league.get(league, []):
            s = max((1.0 if n.strip().lower() == name.strip().lower() else team_score(name, n) for n in ns),
                    default=0.0)
            scored.append((s, tid))
        scored.sort(reverse=True)
        if not scored or scored[0][0] < MIN_SIDE_SCORE:
            return None
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
            return None
        return scored[0][1]


# ── the database ─────────────────────────────────────────────────────────────

def _conn():
    return db_txn.connect(DATABASE_URL)


def ensure_strategy(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("SELECT id FROM research_hypotheses WHERE title LIKE %s ORDER BY id LIMIT 1",
                    (HYPOTHESIS_PREFIX + "%",))
        hyp = cur.fetchone()
        cur.execute("""
            INSERT INTO strategies (hypothesis_id, name, rules, source, run_status, theory, owner_id)
            VALUES (%s, %s, %s::jsonb, 'agent', 'running', %s,
                    (SELECT owner_id FROM strategies WHERE name = %s))
            RETURNING id""", (
            hyp[0] if hyp else None, STRATEGY_NAME, json.dumps({
                "venue": "polymarket", "sport": "soccer", "phase": "paper-only", "execution": "maker",
                "self_settling": True, "obs_version": OBS_VERSION, "model": "close_model v1",
                "window_h": [WINDOW_MIN_H, WINDOW_MAX_H], "gap_min": GAP_MIN, "max_spread": MAX_SPREAD,
                "min_depth_usd": MIN_DEPTH_USD, "stake_u": STAKE_U, "leagues": sorted(cm.STATS_LEAGUES),
            }),
            "Forecast where each 1X2 price will close from team statistics plus the price "
            "itself, and post a bid on Polymarket where the price sits at least 2pp below "
            "that forecast a day before kick-off.", OWNER_FROM))
        sid = cur.fetchone()[0]
    log.info(f"created strategy '{STRATEGY_NAME}' id={sid}")
    return sid


def posted_events(conn, sid: int) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT event_id FROM close_maker_orders WHERE strategy_id = %s", (sid,))
        return {r[0] for r in cur.fetchall()}


def open_orders(conn, sid: int) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM close_maker_orders
                        WHERE strategy_id = %s AND kickoff > now() - interval '30 minutes'
                          AND (status = 'resting' OR close_mid IS NULL)""", (sid,))
        return [dict(r) for r in cur.fetchall()]


def write_trade(conn, sid: int, o: dict, filled_at: datetime) -> int:
    p, q = float(o["bid_price"]), float(o["model_q"])
    mins = (o["kickoff"] - filled_at).total_seconds() / 60
    meta = {"event_slug": o["event_slug"], "home": o["home_name"], "away": o["away_name"],
            "token_id": o["token_id"], "side": o["side"], "league": o["league"], "sport": "soccer"}
    src = {"kind": "maker", "model": "close_model v1", "model_q": round(q, 5),
           "gap_pp": round(float(o["gap"]) * 100, 2), "fill_rule": o["fill_rule"],
           "minutes_to_ko": round(mins, 1), "obs_version": OBS_VERSION, "order_id": o["id"]}
    reasoning = (
        f"MAKER BID — {o['title']}, kick-off {o['kickoff']:%Y-%m-%d %H:%M}Z. {o['team_name']} to win: "
        f"model {q:.3f} ({1 / q:.2f}) against the venue's mid {float(o['p_' + o['side']]):.3f}, "
        f"gap {float(o['gap']) * 100:+.2f}pp. Paper bid {p:.3f} ({1 / p:.2f}), filled {o['fill_rule']} "
        f"{mins:.0f} min before kick-off. {STAKE_U}u, no fee (maker). PAPER.")
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pm_markets (platform, external_id, title, market_type, category,
                                        resolution_time, status, raw_metadata, ingested_at)
                VALUES ('polymarket', %s, %s, 'soccer_1x2', 'soccer', %s, 'active', %s::jsonb, now())
                ON CONFLICT (platform, external_id) DO UPDATE
                   SET title = EXCLUDED.title, resolution_time = EXCLUDED.resolution_time,
                       raw_metadata = COALESCE(pm_markets.raw_metadata, '{}'::jsonb) || EXCLUDED.raw_metadata
                RETURNING id""", (o["condition_id"], o["title"], o["kickoff"], json.dumps(meta)))
            mid = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO paper_trades (strategy_id, market_id, outcome, entry_price, entry_odds,
                    stake_units, model_probability, sharp_consensus_sources, expected_edge,
                    confidence, reasoning, pm_token_id, pm_live, placed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'maker', %s, %s, false, %s)
                RETURNING id""", (sid, mid, f"{o['team_name']} to win — {o['title']}", p, round(1 / p, 4),
                                  STAKE_U, round(q, 5), json.dumps(src), round(q / p - 1, 5), reasoning,
                                  o["token_id"], filled_at))
            tid = cur.fetchone()[0]
            cur.execute("""UPDATE close_maker_orders SET status = 'filled', status_at = now(),
                                  filled_at = %s, fill_rule = %s, paper_trade_id = %s
                            WHERE id = %s""", (filled_at, o["fill_rule"], tid, o["id"]))
    return tid


# ── the cycle ────────────────────────────────────────────────────────────────

@dataclass
class Fixture:
    ev: dict
    league: str
    kickoff: datetime
    title: str
    home: str                 # ESPN's names, the real sides
    away: str
    legs: dict = field(default_factory=dict)   # home / draw / away → leg
    home_id: int | None = None
    away_id: int | None = None


def _et_days(now: datetime) -> list[str]:
    a = (now + timedelta(hours=WINDOW_MIN_H)).astimezone(ET).date()
    b = (now + timedelta(hours=WINDOW_MAX_H)).astimezone(ET).date()
    return [(a + timedelta(days=i)).strftime("%Y%m%d") for i in range((b - a).days + 1)]


def board(now: datetime, teams: Teams) -> tuple[list[Fixture], dict[str, int]]:
    """Fixtures we can price, and a count of why the others were dropped."""
    why: dict[str, int] = {}
    skip = lambda k: why.__setitem__(k, why.get(k, 0) + 1)          # noqa: E731
    events = fetch_events(now)
    by_league: dict[str, list[dict]] = {}
    for ev in events:
        code = competition_code(ev)
        if code is None or code not in cm.STATS_LEAGUES:
            skip("league not modelled")
            continue
        by_league.setdefault(code, []).append(ev)
    days = _et_days(now)
    out = []
    for code, evs in by_league.items():
        espn = espn_fixtures(ESPN_CODE[code], days) if code in ESPN_CODE else []
        for ev in evs:
            title = ev.get("title") or ""
            pm = teams_from_title(title)
            mk = parse_markets(ev)
            if not pm or len(mk["win"]) != 2 or mk["draw"] is None:
                skip("no 1X2 on the event")
                continue
            ko = kickoff_of(ev)
            near = [fx for fx, k in espn if abs((k - ko).total_seconds()) <= 3 * 3600]
            ori, fx = orientation(pm[0], pm[1], near)
            if ori is None:
                skip("ESPN could not confirm sides")
                continue
            home_pm, away_pm = (pm if ori == "same" else (pm[1], pm[0]))
            lh, la = _leg_for(home_pm, mk["win"]), _leg_for(away_pm, mk["win"])
            if lh is None or la is None or lh is la:
                skip("win legs ambiguous")
                continue
            f = Fixture(ev=ev, league=code, kickoff=ko, title=title, home=fx.home, away=fx.away,
                        legs={"home": lh, "draw": mk["draw"], "away": la})
            f.home_id, f.away_id = teams.resolve(code, fx.home), teams.resolve(code, fx.away)
            if f.home_id is None or f.away_id is None or f.home_id == f.away_id:
                skip("team not in our database")
                continue
            out.append(f)
    return out, why


def history_gap(states: dict, f: Fixture) -> str | None:
    """Why the two teams' histories cannot be trusted for this fixture, or None.

    A team is compared with its OWN league, not with the calendar: Football-Data
    files a round days after it is played, and leagues pause for three weeks at
    a time (the Premier League played 2026-09-20 and next on 10-10). A team
    whose last match trails its league's latest by more than a round is missing
    data; a league silent for five weeks is off-season or no longer loading."""
    t = states["teams"]
    latest = max((datetime.fromisoformat(s["last"]) for s in t.values()
                  if s.get("league") == f.league and s.get("last")), default=None)
    if latest is None or (f.kickoff - latest).days > MAX_LEAGUE_GAP_DAYS:
        return f"league data stale ({f.league})"
    for tid in (f.home_id, f.away_id):
        last = t[str(tid)].get("last")
        if not last or (latest - datetime.fromisoformat(last)).days > MAX_TEAM_LAG_DAYS:
            return f"team history behind its league ({f.league})"
    return None


def price_fixture(f: Fixture, books: dict[str, dict | None], spec: dict, states: dict) -> dict:
    """Everything the decision needs, or {'skip': reason}."""
    bk = {k: books.get(f.legs[k]["token_id"]) for k in ("home", "draw", "away")}
    p = normalised(bk)
    if p is None:
        return {"skip": "book not usable"}
    x = cm.features_for(states, f.home_id, f.away_id, f.kickoff)
    if x is None:
        return {"skip": "too little history"}
    why = history_gap(states, f)
    if why:
        return {"skip": why}
    q_h, q_a = cm.fair_pair(spec, x, p["home"], p["away"])
    q = {"home": q_h, "away": q_a}
    return {"p": p, "q": q, "books": bk, "x": x, "pick": choose(q, p, bk)}


def run_once(dry_run: bool = False) -> None:
    now = datetime.now(timezone.utc)
    conn = _conn()
    spec = cm.load_model()
    states = cm.load_states(conn)
    sid = None if dry_run else ensure_strategy(conn)

    # 1. resting bids: fills, closes, expiry — before anything new is posted
    if sid is not None:
        manage_open(conn, sid, now)

    # 2. the board
    teams = Teams(conn, states)
    fixtures, why = board(now, teams)
    done = posted_events(conn, sid) if sid is not None else set()
    todo = [f for f in fixtures if str(f.ev.get("id")) not in done]
    books = fetch_books([f.legs[k]["token_id"] for f in todo for k in ("home", "draw", "away")])
    posted = 0
    for f in todo:
        r = price_fixture(f, books, spec, states)
        hrs = (f.kickoff - now).total_seconds() / 3600
        if "skip" in r:
            why[r["skip"]] = why.get(r["skip"], 0) + 1
            continue
        p, q = r["p"], r["q"]
        line = (f"{f.league:8s} {f.home} v {f.away}  KO {f.kickoff:%a %H:%M}Z (T-{hrs:4.1f}h)  "
                f"PM {1 / p['home']:.2f}/{1 / p['draw']:.2f}/{1 / p['away']:.2f}  "
                f"model H {1 / q['home']:.2f} ({(q['home'] - p['home']) * 100:+.1f}pp)  "
                f"A {1 / q['away']:.2f} ({(q['away'] - p['away']) * 100:+.1f}pp)")
        if r["pick"] is None:
            log.info(line + "  — no bid")
            continue
        side, price, reason = r["pick"]
        log.info(line + f"  → BID {side} {price:.3f} ({1 / price:.2f}) [{reason}]")
        if dry_run:
            continue
        b = r["books"][side]
        leg = f.legs[side]
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO close_maker_orders (obs_version, strategy_id, event_id, event_slug, title,
                    league, kickoff, minutes_to_ko, home_team_id, away_team_id, home_name, away_name,
                    side, team_name, condition_id, token_id, question, pm_bid, pm_ask, p_home, p_draw,
                    p_away, bid_depth_usd, ask_depth_usd, model_q, gap, bid_price, features)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (strategy_id, token_id) DO NOTHING""", (
                OBS_VERSION, sid, str(f.ev.get("id")), f.ev.get("slug"), f.title, f.league, f.kickoff,
                round(hrs * 60, 1), f.home_id, f.away_id, f.home, f.away, side,
                f.home if side == "home" else f.away, leg["condition_id"], leg["token_id"], leg["question"],
                b["bid"], b["ask"], round(p["home"], 5), round(p["draw"], 5), round(p["away"], 5),
                round(b["bid_depth"], 2), round(b["ask_depth"], 2), round(q[side], 5),
                round(q[side] - p[side], 5), price, json.dumps({k: round(v, 5) for k, v in r["x"].items()})))
            posted += cur.rowcount
    log.info(f"board: {len(fixtures)} fixtures priceable, {len(todo)} new, {posted} bids posted; "
             f"skipped {why}")


def manage_open(conn, sid: int, now: datetime) -> None:
    orders = open_orders(conn, sid)
    if not orders:
        return
    books = fetch_books(sorted({o["token_id"] for o in orders}))
    for o in orders:
        book = books.get(o["token_id"])
        mins = (o["kickoff"] - now).total_seconds() / 60
        if o["status"] == "resting" and mins > 0:
            trades = fetch_trades(o["condition_id"])
            rule, t, touched = fill_from(book, trades, o["token_id"], float(o["bid_price"]),
                                         o["created_at"].timestamp())
            if touched and not o.get("touched_at"):
                with conn.cursor() as cur:
                    cur.execute("UPDATE close_maker_orders SET touched_at = to_timestamp(%s) WHERE id = %s",
                                (touched, o["id"]))
            if rule:
                o["fill_rule"] = rule
                tid = write_trade(conn, sid, o, datetime.fromtimestamp(t, timezone.utc))
                o["status"], o["paper_trade_id"] = "filled", tid
                log.info(f"FILLED #{o['id']} {o['team_name']} @ {float(o['bid_price']):.3f} ({rule}) → pt#{tid}")
        if 0 < mins <= CLOSE_WINDOW_MIN and book is not None:
            with conn.cursor() as cur:
                cur.execute("""UPDATE close_maker_orders SET close_bid = %s, close_ask = %s, close_mid = %s,
                                      close_at = now() WHERE id = %s""",
                            (book["bid"], book["ask"], book["mid"], o["id"]))
                if o.get("paper_trade_id"):
                    cur.execute("""UPDATE paper_trades SET pm_closing_price = %s, pm_closing_bid = %s,
                                          pm_closing_ask = %s, pm_closing_at = now(), clv = %s
                                    WHERE id = %s""",
                                (round(book["mid"], 4), book["bid"], book["ask"],
                                 round(book["mid"] / float(o["bid_price"]) - 1, 5), o["paper_trade_id"]))
        if mins <= 0 and o["status"] == "resting":
            with conn.cursor() as cur:
                cur.execute("""UPDATE close_maker_orders SET status = 'expired', status_at = now()
                                WHERE id = %s AND status = 'resting'""", (o["id"],))


# ── settlement and report ────────────────────────────────────────────────────

def settle() -> int:
    conn = _conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT o.id, o.condition_id, o.token_id, o.paper_trade_id, o.bid_price,
                              pt.stake_units, pt.result
                         FROM close_maker_orders o
                         JOIN strategies s ON s.id = o.strategy_id
                         LEFT JOIN paper_trades pt ON pt.id = o.paper_trade_id
                        WHERE s.name = %s AND o.payout IS NULL
                          AND o.kickoff < now() - interval '3 hours'""", (STRATEGY_NAME,))
        pending = [dict(r) for r in cur.fetchall()]
    verdicts = {cid: clob_winner(cid) for cid in {p["condition_id"] for p in pending}}  # network, no txn
    n = 0
    for p in pending:
        v = verdicts.get(p["condition_id"])
        if not v or p["token_id"] not in v:
            continue
        pay = v[p["token_id"]]
        with db_txn.atomic(conn):
            with conn.cursor() as cur:
                cur.execute("UPDATE close_maker_orders SET payout = %s WHERE id = %s", (pay, p["id"]))
                if p["paper_trade_id"] and p["result"] is None:
                    result = "won" if pay == 1.0 else "lost" if pay == 0.0 else "void"
                    payout = float(p["stake_units"]) * pay / float(p["bid_price"])
                    cur.execute("""UPDATE paper_trades SET result = %s, payout_units = %s, resolved_at = now()
                                    WHERE id = %s AND result IS NULL""", (result, round(payout, 4), p["paper_trade_id"]))
        n += 1
    log.info(f"settled {n} of {len(pending)} orders")
    return n


def report() -> None:
    conn = _conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT o.status, count(*) AS n,
                              avg(o.gap) * 100 AS gap_pp,
                              avg(o.close_mid - o.bid_price) * 100 AS close_minus_bid_pp,
                              avg(o.close_mid - (o.pm_bid + o.pm_ask) / 2) * 100 AS mid_move_pp,
                              count(o.payout) AS settled,
                              avg(o.payout - o.bid_price) * 100 AS result_minus_bid_pp
                         FROM close_maker_orders o JOIN strategies s ON s.id = o.strategy_id
                        WHERE s.name = %s GROUP BY 1 ORDER BY 1""", (STRATEGY_NAME,))
        rows = cur.fetchall()
    print(f"{STRATEGY_NAME} — orders by status (pp of price)")
    for r in rows:
        print(f"  {r['status']:9s} n={r['n']:4d}  gap {r['gap_pp'] or 0:+.2f}  close−bid "
              f"{r['close_minus_bid_pp'] if r['close_minus_bid_pp'] is not None else float('nan'):+.2f}  "
              f"mid move {r['mid_move_pp'] if r['mid_move_pp'] is not None else float('nan'):+.2f}  "
              f"settled {r['settled']}  result−bid "
              f"{r['result_minus_bid_pp'] if r['result_minus_bid_pp'] is not None else float('nan'):+.2f}")
    print("  filled vs expired is the adverse-selection gap: a bid that fills when the forecast is wrong "
          "shows filled rows closing below the expired ones.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.once:
        run_once(dry_run=a.dry_run)
    if a.settle:
        settle()
    if a.report:
        report()
    if not (a.once or a.settle or a.report):
        ap.print_help()


if __name__ == "__main__":
    main()
