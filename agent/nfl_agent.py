"""
nfl_agent.py — the NFL every-game agent. PAPER, no orders.

Every NFL game Polymarket lists gets exactly one bet. Which bet is the agent's
choice, and the choice is the whole strategy: across the game's full-match
moneyline, ~30 spreads and ~40 totals (both sides of each — ~150 tokens), buy the
one whose executable ask is cheapest against the sharp line, net of the taker fee.

Fair value is never ours. It comes from Pinnacle (The Odds API; a median of
other books when Pinnacle has not posted), de-vigged. Where Polymarket's line is
the same half-point Pinnacle quotes, that de-vigged price IS the fair value
(`sharp_exact`). Everywhere else — an alternate line, or any spread where
Pinnacle sits on a whole number — it is read off `nfl_model`, anchored per game
to Pinnacle's spread + moneyline + total and carrying NFL key numbers
(`sharp_model`, docked a haircut that grows with the distance from the sharp
line, because that is where the model can be wrong).

Two kinds of bet, kept apart in every report:
  * EDGE   — net EV at the CLOB ask clears the threshold (1.5% exact, 3% model),
             1 unit flat, from 24h before kick-off.
  * FORCED — the game is inside 40 minutes of kick-off with no bet yet: the best
             EV on the board, whatever its sign, at the same 1 unit. This is
             the price of "every game". It is expected to lose roughly the
             half-spread plus the fee; the report shows what it actually cost.

What the project already knows, and why the rules look like this:
  * PM's pre-match mid sits ON the de-vigged Pinnacle line in soccer
    (+0.10pp, finding_pm_mid_is_pinnacle). If NFL is the same, the typical token
    is worth ~ −1% after fee and edge bets will be rare. The EDGE/FORCED split
    exists so that is measured rather than argued.
  * A stale sharp price fabricates edge in both directions
    (finding_observer_live_price_stale). An EDGE bet needs a snapshot fresh for
    its distance to kick-off; a stale one can only ever produce a FORCED bet.
  * Every large claimed edge in this repo's history was our own bug. Anything
    above IMPLAUSIBLE_EV_PCT is logged and refused, never bought.
  * Sides are read from outcome LABELS mapped through Polymarket's own team
    list — never from title order, and the pricing never needs to know which
    team is at home.

    python nfl_agent.py --once --dry-run   # evaluate the board, write nothing
    python nfl_agent.py --once             # the cron entry: bet, capture closes, settle
    python nfl_agent.py --settle
    python nfl_agent.py --report
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "../ingest/.env"))
sys.path.insert(0, HERE)

import db_txn                    # noqa: E402
import nfl_model as nm           # noqa: E402
from edge_engine import FEE_RATE, taker_fee_pp   # noqa: E402

log = logging.getLogger("nfl_agent")

DATABASE_URL = os.getenv("DATABASE_URL")
ODDS_KEY = os.getenv("THE_ODDS_API_KEY")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"

STRATEGY_NAME = "NFL Every Game"
OBS_VERSION = 1

# ── the sharp feed ───────────────────────────────────────────────────────────
# One request returns every NFL game. Cost = 3 markets × ceil(books / 10) = 3
# credits against a 500/month plan, so the cadence is rationed by how close the
# nearest unbet game is, not by the clock.
BOOKS = ["pinnacle", "betfair_ex_eu", "lowvig", "betonlineag", "draftkings",
         "fanduel", "betmgm", "williamhill_us", "bovada", "betrivers"]
ODDS_CACHE = os.path.join(HERE, ".nfl_odds_cache.json")
RESERVE_CREDITS = 60          # below this, fetch only to force a bet or read a close
HORIZON_MIN = 24 * 60         # edge bets from 24h out
FORCE_MIN = 40                # inside this, an unbet game gets its bet
CLOSE_MIN = 12                # a close is read inside this, for games already bet


def required_age_min(mins_to_ko: float) -> float | None:
    """How old a sharp snapshot may be for a game this far from kick-off."""
    if mins_to_ko <= FORCE_MIN:
        return 20
    if mins_to_ko <= 150:
        return 45
    if mins_to_ko <= HORIZON_MIN:
        return 180
    return None


# ── what may be bought ───────────────────────────────────────────────────────
FAMILIES = ("moneyline", "spreads", "totals")   # full match only: the sharp line
                                                # says nothing about quarters
MAX_SPREAD = 0.03
MIN_LIQUIDITY_USD = 2000
MIN_ASK, MAX_ASK = 0.05, 0.95
VERIFY_N = 5                  # top candidates re-read from the CLOB per game
UNIT_USD = 10.0               # for the depth check only

EDGE_MIN_EXACT = 1.5          # % net EV on cash, fair value = the book's own price
EDGE_MIN_MODEL = 3.0          # % net EV, fair value read off the model
# Model fair values are docked (base + per point from the sharp line, capped), sized
# to the model's MEASURED error against 2012-2025 results (nfl_model tail check,
# 2026-09-13). Totals track the empirical rate within one SE out to ±14.5 points.
# Spreads miss by up to 2-4pp in both directions (a 1-3.5pt favourite covering
# s+3.5: 0.333 real vs 0.364 model; a 6-7.5pt favourite by 15+: 0.307 vs 0.266),
# so their haircut is larger and nothing past MAX_SPREAD_DIST is priced at all.
HAIRCUT_PP = {"spreads": (1.0, 0.35, 4.0), "totals": (0.5, 0.10, 2.0),
              "moneyline": (0.75, 0.0, 0.75)}      # (base, per point, cap)
MAX_SPREAD_DIST = 10.0
MAX_ML_RESID_PP = 1.0         # the model must reproduce the book's own moneyline
IMPLAUSIBLE_EV_PCT = 12.0     # above this it is our bug until proven otherwise

# Every bet is 1 unit, EDGE and FORCED alike, like every other agent on the
# site (the user's call, 2026-09-19). It was quarter-Kelly on 100u for EDGE
# (0.5-3u) and 0.5u for FORCED, which made this agent's P&L half the scale of
# its neighbours'; the 15 trades placed before the change were restated to 1u.
STAKE_U = 1.0


# ── small helpers ────────────────────────────────────────────────────────────

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
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def devig(o1: float, o2: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """((prop1, prop2), (pow1, pow2)). Both, always: proportional overstates
    longshots and would manufacture exactly the pattern we want to see."""
    x, y = 1.0 / o1, 1.0 / o2
    prop = (x / (x + y), y / (x + y))
    lo, hi = 0.2, 5.0
    for _ in range(60):
        k = 0.5 * (lo + hi)
        if x ** k + y ** k > 1:
            lo = k
        else:
            hi = k
    k = 0.5 * (lo + hi)
    return prop, (x ** k, y ** k)


def cost_per_share(ask: float) -> float:
    """What one share costs a taker: the ask plus 0.05·p·(1−p)."""
    return ask * (1.0 + FEE_RATE * (1.0 - ask))


def ev_pct(fair: float, ask: float) -> float:
    return 100.0 * (fair / cost_per_share(ask) - 1.0)


def is_half(x: float) -> bool:
    return abs(abs(x) % 1 - 0.5) < 1e-9


# ── Polymarket ───────────────────────────────────────────────────────────────

SLUG_RE = re.compile(r"^nfl-[a-z]{2,4}-[a-z]{2,4}-\d{4}-\d{2}-\d{2}$")   # the main event
SPREAD_Q = re.compile(r"^Spread:\s*(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)\s*$", re.I)


@dataclass
class Game:
    slug: str
    title: str
    kickoff: datetime
    home: str                  # Polymarket's own designation, full names
    away: str
    names: dict                # _norm(name | alias | abbreviation) -> full name
    markets: list

    def team(self, label: str) -> str | None:
        return self.names.get(_norm(label))

    def other(self, team: str) -> str:
        return self.away if team == self.home else self.home


def game_from_event(ev: dict) -> Game | None:
    slug = ev.get("slug") or ""
    if not SLUG_RE.match(slug):
        return None                       # player props, futures, "- More Markets"
    teams = ev.get("teams") or []
    home = next((t for t in teams if str(t.get("ordering", "")).lower() == "home"), None)
    away = next((t for t in teams if str(t.get("ordering", "")).lower() == "away"), None)
    ko = _ts(ev.get("startTime"))
    if not (home and away and home.get("name") and away.get("name") and ko):
        return None
    names: dict = {}
    for t in (home, away):
        for key in (t.get("name"), t.get("alias"), t.get("abbreviation")):
            if key:
                if _norm(key) in names and names[_norm(key)] != t["name"]:
                    return None           # an alias naming both teams: fail closed
                names[_norm(key)] = t["name"]
    return Game(slug=slug, title=ev.get("title") or slug, kickoff=ko, home=home["name"],
                away=away["name"], names=names, markets=ev.get("markets") or [])


def fetch_pm_games(now: datetime) -> list[Game]:
    """Main NFL game events from yesterday to past the horizon. Bounded by plain
    DATES: a sports event's endDate equals its kick-off, and Gamma's date filters
    misbehave on ISO timestamps (lab_strategy_runner.fetch_upcoming)."""
    params = {"tag_slug": "nfl", "closed": "false", "limit": 100,
              "end_date_min": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
              "end_date_max": (now + timedelta(minutes=HORIZON_MIN, days=1)).strftime("%Y-%m-%d")}
    out: list[Game] = []
    for off in range(0, 800, 100):
        try:
            r = requests.get(f"{GAMMA}/events", params={**params, "offset": off}, timeout=25)
            r.raise_for_status()
            batch = r.json()
        except Exception as exc:                        # noqa: BLE001
            log.warning(f"gamma offset {off}: {exc}")
            break
        if not isinstance(batch, list) or not batch:
            break
        for ev in batch:
            if ev.get("live") or ev.get("ended"):
                continue
            g = game_from_event(ev)
            if g and g.kickoff > now:
                out.append(g)
        if len(batch) < 100:
            break
    uniq = {g.slug: g for g in out}
    return sorted(uniq.values(), key=lambda g: g.kickoff)


def fetch_book(token_id: str) -> dict | None:
    try:
        r = requests.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=10)
        if r.status_code != 200:
            return None
        b = r.json()
    except Exception:                                   # noqa: BLE001
        return None
    bids = sorted(b.get("bids") or [], key=lambda x: -(_f(x.get("price")) or 0))
    asks = sorted(b.get("asks") or [], key=lambda x: _f(x.get("price")) or 1)
    if not bids or not asks:
        return None
    depth = sum((_f(x["price"]) or 0) * (_f(x["size"]) or 0) for x in asks[:5])
    return {"bid": _f(bids[0]["price"]), "ask": _f(asks[0]["price"]), "ask_depth_usd": depth}


# ── the sharp line ───────────────────────────────────────────────────────────

def load_cache() -> dict | None:
    try:
        with open(ODDS_CACHE) as fh:
            c = json.load(fh)
        c["fetched_at_dt"] = _ts(c["fetched_at"])
        return c
    except Exception:                                   # noqa: BLE001
        return None


def fetch_odds() -> dict | None:
    if not ODDS_KEY:
        log.warning("no THE_ODDS_API_KEY")
        return None
    try:
        r = requests.get(ODDS_URL, params={"apiKey": ODDS_KEY, "bookmakers": ",".join(BOOKS),
                                           "markets": "h2h,spreads,totals",
                                           "oddsFormat": "decimal"}, timeout=25)
    except Exception as exc:                            # noqa: BLE001
        log.warning(f"odds api: {exc}")
        return None
    remaining = r.headers.get("x-requests-remaining")
    if r.status_code != 200:
        log.warning(f"odds api {r.status_code}: {r.text[:200]} (remaining {remaining})")
        return None
    c = {"fetched_at": datetime.now(timezone.utc).isoformat(),
         "remaining": int(float(remaining)) if remaining else None, "data": r.json()}
    with open(ODDS_CACHE, "w") as fh:
        json.dump(c, fh)
    log.info(f"odds api: {len(c['data'])} games, {c['remaining']} credits left")
    c["fetched_at_dt"] = _ts(c["fetched_at"])
    return c


@dataclass
class Sharp:
    book: str                  # 'pinnacle' | 'consensus(n)'
    anchor: nm.Anchor          # team A = Game.home
    ml: dict                   # team -> (prop, pow)
    spread: dict               # team -> (point, prop, pow)   pinnacle only
    total: tuple | None        # (point, (prop_o, pow_o), (prop_u, pow_u))   pinnacle only
    model_ok: bool
    snapshot_at: datetime

    def mid(self, pair) -> float:
        return 0.5 * (pair[0] + pair[1])


def _book_view(bk: dict, home: str, away: str) -> dict | None:
    mk = {m["key"]: m for m in bk.get("markets") or []}
    out: dict = {"ml": {}, "spread": {}, "total": None}
    h2h = {o["name"]: o["price"] for o in (mk.get("h2h") or {}).get("outcomes") or []}
    if home in h2h and away in h2h:
        (ph, pa), (wh, wa) = devig(h2h[home], h2h[away])
        out["ml"] = {home: (ph, wh), away: (pa, wa)}
    sp = {o["name"]: o for o in (mk.get("spreads") or {}).get("outcomes") or []}
    if home in sp and away in sp and sp[home].get("point") is not None:
        (ph, pa), (wh, wa) = devig(sp[home]["price"], sp[away]["price"])
        out["spread"] = {home: (float(sp[home]["point"]), ph, wh),
                         away: (float(sp[away]["point"]), pa, wa)}
    tot = {o["name"]: o for o in (mk.get("totals") or {}).get("outcomes") or []}
    if "Over" in tot and "Under" in tot and tot["Over"].get("point") is not None:
        (po, pu), (wo, wu) = devig(tot["Over"]["price"], tot["Under"]["price"])
        out["total"] = (float(tot["Over"]["point"]), (po, wo), (pu, wu))
    return out if (out["ml"] or out["spread"]) else None


def _anchor_of(view: dict, home: str) -> nm.Anchor:
    mid = lambda p: 0.5 * (p[0] + p[1])                  # noqa: E731
    sp = view["spread"].get(home)
    tot = view["total"]
    return nm.anchor(
        give_a=-sp[0] if sp else None, p_cover_a=mid(sp[1:]) if sp else None,
        p_win_a=mid(view["ml"][home]) if view["ml"] else None,
        total_line=tot[0] if tot else None, p_over=mid(tot[1]) if tot else None)


def sharp_for(game: Game, cache: dict) -> Sharp | None:
    """Match the game by its two FULL team names (never title order), within a
    kick-off window, then build the anchor from Pinnacle — or the median of the
    other books when Pinnacle has not posted."""
    want = {_norm(game.home), _norm(game.away)}
    ev = None
    for e in cache.get("data") or []:
        ct = _ts(e.get("commence_time"))
        if {_norm(e.get("home_team")), _norm(e.get("away_team"))} == want and ct \
                and abs((ct - game.kickoff).total_seconds()) <= 3 * 3600:
            ev = e
            break
    if ev is None:
        return None
    if _norm(ev.get("home_team")) != _norm(game.home):
        # harmless for pricing (it is team-relative), logged because it is the
        # bug class that once inverted the NBA scanner
        log.info(f"  {game.slug}: odds feed has {ev.get('home_team')} at home, PM has {game.home}")
    books = {b["key"]: b for b in ev.get("bookmakers") or []}
    snap = cache["fetched_at_dt"]
    if "pinnacle" in books:
        v = _book_view(books["pinnacle"], game.home, game.away)
        if v and v["ml"] and v["spread"]:
            a = _anchor_of(v, game.home)
            ok = a.mu is not None and abs(a.ml_resid_pp) <= MAX_ML_RESID_PP
            return Sharp("pinnacle", a, v["ml"], v["spread"], v["total"], ok, snap)
    views = [v for k, b in books.items() if k != "pinnacle"
             for v in [_book_view(b, game.home, game.away)] if v and v["ml"] and v["spread"]]
    if len(views) < 2:
        return None
    anchors = [_anchor_of(v, game.home) for v in views]
    Ts = [a.T for a in anchors if a.T is not None]
    a = nm.Anchor(mu=median(x.mu for x in anchors), sigma=median(x.sigma for x in anchors),
                  T=median(Ts) if Ts else None,
                  ml_resid_pp=median(x.ml_resid_pp for x in anchors), sigma_solved=True)
    ml = {t: (median(v["ml"][t][0] for v in views), median(v["ml"][t][1] for v in views))
          for t in (game.home, game.away)}
    return Sharp(f"consensus({len(views)})", a, ml, {}, None,
                 abs(a.ml_resid_pp) <= MAX_ML_RESID_PP, snap)


# ── candidates ───────────────────────────────────────────────────────────────

@dataclass
class Cand:
    family: str
    line: float | None         # the token's own line: team -x → -x, total x → x
    side: str                  # full team name, 'Over' or 'Under'
    question: str
    condition_id: str
    token_id: str
    bid: float
    ask: float
    liquidity: float
    fair: float                # after haircut
    fair_raw: float
    source: str                # sharp_exact | sharp_model | pm_mid
    dist: float = 0.0
    ev: float = 0.0
    verified: bool = False
    depth_usd: float | None = None

    @property
    def label(self) -> str:
        if self.family == "moneyline":
            return f"{self.side} ML"
        if self.family == "spreads":
            return f"{self.side} {self.line:+g}"
        return f"{self.side} {self.line:g}"


def _haircut(family: str, dist: float) -> float:
    base, per_pt, cap = HAIRCUT_PP[family]
    return min(base + per_pt * dist, cap) / 100.0


def candidates(game: Game, sh: Sharp | None) -> list[Cand]:
    """Every full-match token whose Gamma book passes the quality gates, priced.
    With no sharp line at all the fair value is PM's own mid — used only to pick
    the cheapest token to hold when a bet is forced."""
    out: list[Cand] = []
    a = sh.anchor if sh else None
    for m in game.markets:
        fam = str(m.get("sportsMarketType") or "").lower()
        if fam not in FAMILIES or m.get("closed") or m.get("active") is False \
                or m.get("acceptingOrders") is False:
            continue
        outs, toks = _jl(m.get("outcomes")), _jl(m.get("clobTokenIds"))
        bb, ba, liq = _f(m.get("bestBid")), _f(m.get("bestAsk")), _f(m.get("liquidityNum")) or 0.0
        if len(outs) != 2 or len(toks) != 2 or bb is None or ba is None:
            continue
        if ba - bb > MAX_SPREAD + 1e-9 or liq < MIN_LIQUIDITY_USD:
            continue
        # outcome 0 trades at the market's bid/ask; outcome 1 is its mirror
        legs = [(outs[0], str(toks[0]), bb, ba), (outs[1], str(toks[1]), 1 - ba, 1 - bb)]
        q = m.get("question") or ""
        priced: list[tuple[str, float | None, float | None, str, float]] = []

        if fam == "moneyline":
            t0, t1 = game.team(outs[0]), game.team(outs[1])
            if {t0, t1} != {game.home, game.away}:
                continue
            for t in (t0, t1):
                if sh and sh.ml.get(t):
                    priced.append((t, None, min(sh.ml[t]), "sharp_exact", 0.0))
                elif sh and sh.model_ok:
                    mu = a.mu if t == game.home else -a.mu
                    priced.append((t, None, nm.win_prob(mu, a.sigma), "sharp_model", 0.0))
                else:
                    priced.append((t, None, None, "pm_mid", 0.0))

        elif fam == "spreads":
            sm = SPREAD_Q.match(q)
            ln = _f(m.get("line"))
            if not sm or ln is None or abs(float(sm.group(2)) - ln) > 1e-9:
                continue
            x = game.team(sm.group(1))
            if x is None or game.team(outs[0]) != x or game.team(outs[1]) != game.other(x):
                continue
            y = game.other(x)
            pin = sh.spread if sh else {}
            if pin.get(x) and is_half(ln) and abs(pin[x][0] - ln) < 1e-9:
                priced += [(x, ln, min(pin[x][1:]), "sharp_exact", 0.0),
                           (y, -ln, min(pin[y][1:]), "sharp_exact", 0.0)]
            elif sh and sh.model_ok:
                mu_x = a.mu if x == game.home else -a.mu
                fx = nm.cover_prob(mu_x, -ln, a.sigma)
                d = abs(ln - pin[x][0]) if pin.get(x) else abs(-ln - mu_x)
                priced += [(x, ln, fx, "sharp_model", d), (y, -ln, 1 - fx, "sharp_model", d)]
            else:
                priced += [(x, ln, None, "pm_mid", 0.0), (y, -ln, None, "pm_mid", 0.0)]

        else:  # totals
            ln = _f(m.get("line"))
            labels = [str(o).strip().lower() for o in outs]
            if ln is None or labels != ["over", "under"] or "O/U" not in q or ":" not in q:
                continue
            if any(w in q.lower() for w in ("1h", "2h", "1q", "2q", "3q", "4q", "team total")):
                continue
            tot = sh.total if sh else None
            if tot and is_half(ln) and abs(tot[0] - ln) < 1e-9:
                priced += [("Over", ln, min(tot[1]), "sharp_exact", 0.0),
                           ("Under", ln, min(tot[2]), "sharp_exact", 0.0)]
            elif sh and a.T is not None:
                fo = nm.over_prob(a.T, ln)
                d = abs(ln - tot[0]) if tot else abs(ln - a.T)
                priced += [("Over", ln, fo, "sharp_model", d), ("Under", ln, 1 - fo, "sharp_model", d)]
            else:
                priced += [("Over", ln, None, "pm_mid", 0.0), ("Under", ln, None, "pm_mid", 0.0)]

        for (side, line, fair, src, dist), (_lbl, tok, bid, ask) in zip(priced, legs):
            if not (MIN_ASK <= ask <= MAX_ASK):
                continue
            if src == "pm_mid":
                fair = 0.5 * (bid + ask)
            if src == "sharp_model" and fam == "spreads" and dist > MAX_SPREAD_DIST:
                continue
            raw = fair
            if src == "sharp_model":
                fair = raw - _haircut(fam, dist)
            c = Cand(fam, line, side, q, m.get("conditionId") or "", tok, bid, ask, liq,
                     fair, raw, src, dist)
            c.ev = ev_pct(c.fair, c.ask)
            out.append(c)
    return out


def verify(cands: list[Cand], n: int = VERIFY_N) -> list[Cand]:
    """Re-read the best few from the CLOB: Gamma's quote lags the book."""
    top = sorted(cands, key=lambda c: -c.ev)[:n]
    for c in top:
        b = fetch_book(c.token_id)
        if not b or b["ask"] is None:
            continue
        c.bid, c.ask, c.depth_usd, c.verified = b["bid"], b["ask"], b["ask_depth_usd"], True
        c.ev = ev_pct(c.fair, c.ask) if MIN_ASK <= c.ask <= MAX_ASK else -99.0
    return [c for c in top if c.verified]


# ── the database ─────────────────────────────────────────────────────────────

def _conn():
    return db_txn.connect(DATABASE_URL)


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
                "H-NFL-SHARP — one bet on every NFL game, the best-EV token against the sharp line",
                "On every Polymarket NFL game, the cheapest full-match token against the "
                "de-vigged Pinnacle line (key-number model for alternates) returns more than "
                "the fee. Edge bets (EV >= 1.5% exact / 3% model) and forced bets (the rule "
                "that every game is bet) are measured separately.",
                "PM NFL books are deep and one tick wide, so any edge is a lag or an "
                "alternate-line mispricing, not a wide book. Soccer says PM's mid sits on "
                "Pinnacle; this measures whether NFL does too. Verdict gate: n >= 200 edge "
                "bets, net yield CI clear of zero, and positive CLV against the Pinnacle close."))
            hyp = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO strategies (hypothesis_id, name, rules, source, run_status, theory)
                VALUES (%s, %s, %s::jsonb, 'agent', 'running', %s) RETURNING id""", (
                hyp, STRATEGY_NAME, json.dumps({
                    "venue": "polymarket", "sport": "nfl", "phase": "paper-only",
                    "self_settling": True, "obs_version": OBS_VERSION,
                    "fair_value": "pinnacle de-vigged; nfl_model (key numbers) for alternates",
                    "edge_min_exact_pct": EDGE_MIN_EXACT, "edge_min_model_pct": EDGE_MIN_MODEL,
                    "force_min": FORCE_MIN, "stake_u": STAKE_U,
                }), "Bet every NFL game on Polymarket, choosing the market with the best "
                    "price against the sharp line."))
            sid = cur.fetchone()[0]
    log.info(f"created strategy '{STRATEGY_NAME}' id={sid}")
    return sid


def bet_games(conn, sid: int) -> dict[str, dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pm.raw_metadata->>'game_slug' AS slug, pt.id, pt.pm_token_id, pt.entry_price,
                   pt.closing_price, pt.placed_at, pm.resolution_time
              FROM paper_trades pt JOIN pm_markets pm ON pm.id = pt.market_id
             WHERE pt.strategy_id = %s""", (sid,))
        return {r["slug"]: dict(r) for r in cur.fetchall()}


def last_snapshot_written(conn) -> dict[str, datetime]:
    with conn.cursor() as cur:
        cur.execute("""SELECT game_slug, max(odds_snapshot_at) FROM nfl_candidates
                        WHERE observed_at > now() - interval '3 days' GROUP BY 1""")
        return {s: t for s, t in cur.fetchall()}


def write_candidates(conn, game: Game, sh: Sharp | None, cands: list[Cand], now: datetime,
                     chosen: Cand | None = None, trade_id: int | None = None) -> None:
    rows = []
    mins = (game.kickoff - now).total_seconds() / 60
    for c in cands:
        rows.append((OBS_VERSION, game.slug, game.kickoff, round(mins, 1), game.home, game.away,
                     c.family, c.line, c.side, c.condition_id, c.token_id, c.bid, c.ask,
                     c.liquidity, round(c.fair, 5), c.source, sh.book if sh else None,
                     round(sh.anchor.mu, 3) if sh and sh.anchor.mu is not None else None,
                     round(sh.anchor.T, 2) if sh and sh.anchor.T is not None else None,
                     round(sh.anchor.ml_resid_pp, 2) if sh else None,
                     round(taker_fee_pp(c.ask), 3), round(c.ev, 3),
                     sh.snapshot_at if sh else None, c is chosen, trade_id if c is chosen else None))
    if not rows:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO nfl_candidates (obs_version, game_slug, kickoff, minutes_to_ko, home_team,
                away_team, market_type, line, side, condition_id, token_id, pm_bid, pm_ask,
                pm_liquidity, fair, fair_source, fair_book, anchor_mu, anchor_total,
                anchor_disagree, fee_pp, ev_pct, odds_snapshot_at, chosen, paper_trade_id)
            VALUES %s""", rows)


def write_trade(conn, sid: int, game: Game, sh: Sharp | None, c: Cand, kind: str,
                stake: float, now: datetime) -> int:
    mins = (game.kickoff - now).total_seconds() / 60
    a = sh.anchor if sh else None
    meta = {"game_slug": game.slug, "home": game.home, "away": game.away, "token_id": c.token_id,
            "side": c.side, "line": c.line, "family": c.family, "sport": "nfl"}
    src = {"kind": kind, "fair_source": c.source, "book": sh.book if sh else None,
           "fair_raw": round(c.fair_raw, 5), "fair": round(c.fair, 5), "ev_pct": round(c.ev, 3),
           "fee_pp": round(taker_fee_pp(c.ask), 3), "dist_pts": c.dist,
           "mu_home": round(a.mu, 3) if a and a.mu is not None else None,
           "sigma": round(a.sigma, 2) if a else None,
           "total": round(a.T, 2) if a and a.T is not None else None,
           "ml_resid_pp": round(a.ml_resid_pp, 2) if a else None,
           "snapshot_at": sh.snapshot_at.isoformat() if sh else None,
           "minutes_to_ko": round(mins, 1), "obs_version": OBS_VERSION}
    fair_txt = (f"fair {c.fair:.3f} ({1 / c.fair:.2f}) from {c.source}"
                + (f" [{sh.book}]" if sh else " — NO sharp line, PM mid used"))
    reasoning = (
        f"NFL {kind.upper()} — {game.title}, kick-off {game.kickoff:%Y-%m-%d %H:%M}Z "
        f"({mins:.0f} min out). {c.label} [{c.family}]: CLOB ask {c.ask:.3f} ({1 / c.ask:.2f}), "
        f"bid {c.bid:.3f}, depth ${c.depth_usd or 0:,.0f}; {fair_txt}; "
        f"net EV {c.ev:+.2f}% after a {taker_fee_pp(c.ask):.2f}pp fee. {stake}u, PAPER.")
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pm_markets (platform, external_id, title, market_type, category,
                                        resolution_time, status, raw_metadata, ingested_at)
                VALUES ('polymarket', %s, %s, %s, 'nfl', %s, 'active', %s::jsonb, now())
                ON CONFLICT (platform, external_id) DO UPDATE
                   SET title = EXCLUDED.title, resolution_time = EXCLUDED.resolution_time,
                       raw_metadata = COALESCE(pm_markets.raw_metadata, '{}'::jsonb) || EXCLUDED.raw_metadata
                RETURNING id""", (c.condition_id, c.question, f"nfl_{c.family}", game.kickoff,
                                  json.dumps(meta)))
            mid = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO paper_trades (strategy_id, market_id, outcome, entry_price, entry_odds,
                    stake_units, model_probability, sharp_consensus_price, sharp_consensus_sources,
                    expected_edge, confidence, reasoning, pm_token_id, pm_live, placed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, false, now())
                RETURNING id""", (sid, mid, f"{c.label} — {game.title}", c.ask, round(1 / c.ask, 4),
                                  stake, round(c.fair, 5), round(c.fair_raw, 5), json.dumps(src),
                                  round(c.ev / 100, 5), kind, reasoning, c.token_id))
            return cur.fetchone()[0]


# ── the cycle ────────────────────────────────────────────────────────────────

def _odds_needed(games: list[Game], bets: dict, cache: dict | None, now: datetime) -> str | None:
    """Why a fresh snapshot is needed now, or None. The reason decides whether
    the credit reserve may be spent."""
    age = (now - cache["fetched_at_dt"]).total_seconds() / 60 if cache else 1e9
    for g in games:
        mins = (g.kickoff - now).total_seconds() / 60
        if g.slug in bets:
            b = bets[g.slug]
            if b.get("closing_price") is None and 0 < mins <= CLOSE_MIN and age > 8:
                return "close"
            continue
        req = required_age_min(mins)
        if req is not None and age > req:
            return "force" if mins <= FORCE_MIN else "edge"
    return None


def run_once(dry_run: bool = False, allow_fetch: bool = True) -> None:
    now = datetime.now(timezone.utc)
    games = fetch_pm_games(now)
    conn = _conn()
    sid = None if dry_run else ensure_strategy(conn)
    if dry_run:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
            r = cur.fetchone()
            sid = r[0] if r else -1
    bets = bet_games(conn, sid)

    cache = load_cache()
    why = _odds_needed(games, bets, cache, now)
    if why and allow_fetch:
        low = cache and cache.get("remaining") is not None and cache["remaining"] < RESERVE_CREDITS
        if low and why == "edge":
            log.info(f"odds snapshot stale but {cache['remaining']} credits left — saving them for forced bets")
        else:
            cache = fetch_odds() or cache
    age = (now - cache["fetched_at_dt"]).total_seconds() / 60 if cache else None
    snap_txt = "none" if age is None else f"{age:.0f} min old ({cache.get('remaining')} credits)"
    log.info(f"{len(games)} NFL games listed in the next {HORIZON_MIN // 60}h · "
             f"{sum(g.slug in bets for g in games)} already bet · sharp snapshot {snap_txt}")

    written = last_snapshot_written(conn) if not dry_run else {}
    todo: list[tuple] = []          # (game, sharp, cands, chosen, kind, stake)
    closes: list[tuple] = []        # (trade_id, fair, mid, book)
    for g in games:
        mins = (g.kickoff - now).total_seconds() / 60
        req = required_age_min(mins)
        sh = sharp_for(g, cache) if cache else None

        if g.slug in bets:          # read the close for a game already bet
            b = bets[g.slug]
            if b["closing_price"] is None and 0 < mins <= CLOSE_MIN and sh and age is not None and age <= 8:
                hit = [c for c in candidates(g, sh) if c.token_id == b["pm_token_id"]]
                if hit and hit[0].source != "pm_mid":
                    c = hit[0]
                    closes.append((b["id"], c.fair_raw, 0.5 * (c.bid + c.ask), sh.book, float(b["entry_price"])))
            continue
        if req is None:
            continue

        fresh = sh is not None and age is not None and age <= req + 5
        cands = candidates(g, sh if sh else None)
        if not cands:
            log.info(f"  {g.title:<28} {mins:6.0f}m  no book passes the gates")
            continue
        if sh is None and mins > FORCE_MIN:
            log.info(f"  {g.title:<28} {mins:6.0f}m  no sharp line matched yet — waiting")
            continue
        ver = verify(cands)
        sane = [c for c in ver if c.ev <= IMPLAUSIBLE_EV_PCT]
        for c in ver:
            if c.ev > IMPLAUSIBLE_EV_PCT:
                log.warning(f"  {g.title}: {c.label} EV {c.ev:+.1f}% > {IMPLAUSIBLE_EV_PCT}% — "
                            f"refused as a probable bug (fair {c.fair:.3f} {c.source}, ask {c.ask:.3f})")
        best = max(sane, key=lambda c: c.ev) if sane else None
        tag = (f"{sh.book} μ={sh.anchor.mu:+.1f} σ={sh.anchor.sigma:.1f} "
               f"T={sh.anchor.T if sh.anchor.T is None else round(sh.anchor.T, 1)} "
               f"resid={sh.anchor.ml_resid_pp:+.1f}pp{'' if sh.model_ok else ' MODEL-OFF'}"
               if sh else "no sharp")
        if best is None:
            log.info(f"  {g.title:<28} {mins:6.0f}m  nothing verified on the CLOB · {tag}")
            continue

        kind, stake = None, 0.0
        th = EDGE_MIN_EXACT if best.source == "sharp_exact" else EDGE_MIN_MODEL
        if fresh and best.source != "pm_mid" and best.ev >= th:
            kind, stake = "edge", STAKE_U
        elif mins <= FORCE_MIN:
            kind, stake = "forced", STAKE_U
        if kind and (best.depth_usd or 0) < stake * UNIT_USD:
            log.info(f"  {g.title}: {best.label} ask depth ${best.depth_usd or 0:.0f} < stake — skip this cycle")
            kind = None
        log.info(f"  {g.title:<28} {mins:6.0f}m  best {best.label:<26} ask {best.ask:.3f} "
                 f"fair {best.fair:.3f} {best.source:<11} EV {best.ev:+5.2f}%"
                 f"{'  → ' + kind.upper() + f' {stake}u' if kind else ''} · {tag}")
        todo.append((g, sh, cands, best if kind else None, kind, stake,
                     sh is not None and written.get(g.slug) != sh.snapshot_at))

    if dry_run:
        log.info(f"DRY RUN — {sum(1 for t in todo if t[4])} bets would be placed, "
                 f"{len(closes)} closes read; nothing written")
        return

    placed = 0
    for g, sh, cands, chosen, kind, stake, new_snap in todo:
        tid = None
        if chosen:
            tid = write_trade(conn, sid, g, sh, chosen, kind, stake, now)
            placed += 1
            log.info(f"  PLACED pt#{tid} {kind} {chosen.label} — {g.title} @ {chosen.ask:.3f} {stake}u")
        if new_snap:
            write_candidates(conn, g, sh, cands, now, chosen, tid)
        elif chosen:
            write_candidates(conn, g, sh, [chosen], now, chosen, tid)
    for tid, fair, mid, book, entry in closes:
        with conn.cursor() as cur:
            cur.execute("""UPDATE paper_trades SET closing_price = %s, clv = %s, clv_source = %s,
                                  pm_closing_price = %s, pm_closing_at = now(), pm_clv = %s
                            WHERE id = %s AND closing_price IS NULL""",
                        (round(fair, 5), round(fair / entry - 1, 5), f"{book}_close_nfl",
                         round(mid, 4), round(mid / entry - 1, 5), tid))
        log.info(f"  close pt#{tid}: sharp fair {fair:.3f} vs entry {entry:.3f} → CLV {fair / entry - 1:+.2%}")
    log.info(f"placed {placed} · closes {len(closes)}")


# ── settlement ───────────────────────────────────────────────────────────────

def settle() -> int:
    conn = _conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pt.id, pt.pm_token_id, pt.stake_units, pt.entry_price, pm.external_id
              FROM paper_trades pt
              JOIN strategies s ON s.id = pt.strategy_id
              JOIN pm_markets pm ON pm.id = pt.market_id
             WHERE s.name = %s AND pt.result IS NULL
               AND pm.resolution_time < now() - interval '3 hours'""", (STRATEGY_NAME,))
        pending = [dict(r) for r in cur.fetchall()]
    verdicts: dict[str, dict | None] = {}
    for cid in {p["external_id"] for p in pending}:      # network first, no txn open
        try:
            d = requests.get(f"{CLOB}/markets/{cid}", timeout=10).json()
        except Exception:                                # noqa: BLE001
            verdicts[cid] = None
            continue
        toks = d.get("tokens") or []
        if any(t.get("winner") for t in toks):
            verdicts[cid] = {str(t["token_id"]): (1.0 if t.get("winner") else 0.0) for t in toks}
        elif d.get("closed") and toks and all(abs((_f(t.get("price")) or 0) - 0.5) < 0.01 for t in toks):
            verdicts[cid] = {str(t["token_id"]): 0.5 for t in toks}      # a tie resolved 50-50
        else:
            verdicts[cid] = None
    n = 0
    for p in pending:
        v = verdicts.get(p["external_id"])
        if not v or p["pm_token_id"] not in v:
            continue
        pay = v[p["pm_token_id"]]
        result = "won" if pay == 1.0 else "lost" if pay == 0.0 else "void"
        payout = float(p["stake_units"]) * pay / float(p["entry_price"])
        with conn.cursor() as cur:
            cur.execute("""UPDATE paper_trades SET result = %s, payout_units = %s, resolved_at = now()
                            WHERE id = %s AND result IS NULL""", (result, round(payout, 4), p["id"]))
        n += 1
    log.info(f"settled {n} of {len(pending)} finished NFL trades")
    return n


# ── report ───────────────────────────────────────────────────────────────────

def report() -> None:
    conn = _conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pt.id, pt.placed_at, pt.outcome, pt.entry_price, pt.stake_units, pt.result,
                   pt.payout_units, pt.confidence AS kind, pt.clv, pt.pm_clv,
                   (pt.sharp_consensus_sources->>'ev_pct')::float AS ev,
                   pt.sharp_consensus_sources->>'fair_source' AS src
              FROM paper_trades pt JOIN strategies s ON s.id = pt.strategy_id
             WHERE s.name = %s ORDER BY pt.placed_at""", (STRATEGY_NAME,))
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        print("no NFL trades yet")
        return
    print(f"{'':8}{'n':>4}{'settled':>9}{'won':>5}{'stake':>8}{'net P&L':>9}{'yield':>8}"
          f"{'avg EV':>8}{'avg CLV':>9}")
    for kind in ("edge", "forced", None):
        sub = [r for r in rows if kind is None or r["kind"] == kind]
        if not sub:
            continue
        st = [r for r in sub if r["result"]]
        stake = sum(float(r["stake_units"]) for r in st)
        # payout_units is GROSS; the taker fee is stake·0.05·(1−ask)
        fee = sum(float(r["stake_units"]) * FEE_RATE * (1 - float(r["entry_price"])) for r in st)
        pnl = sum(float(r["payout_units"] or 0) - float(r["stake_units"]) for r in st) - fee
        evs = [r["ev"] for r in sub if r["ev"] is not None]
        clvs = [float(r["clv"]) for r in sub if r["clv"] is not None]
        print(f"{(kind or 'ALL'):<8}{len(sub):>4}{len(st):>9}{sum(r['result'] == 'won' for r in st):>5}"
              f"{stake:>8.1f}{pnl:>+9.2f}{(100 * pnl / stake if stake else 0):>+7.1f}%"
              f"{(sum(evs) / len(evs) if evs else 0):>+7.2f}%"
              f"{(100 * sum(clvs) / len(clvs) if clvs else float('nan')):>+8.2f}%")
    print("\nCLV vs the sharp close says whether EDGE bets beat the line (tens of bets suffice); "
          "a P&L CI at ~2.0 odds needs thousands. FORCED measures the cost of TAKING, not a price.")
    print("\nlast 20:")
    for r in rows[-20:]:
        clv_txt = "" if r["clv"] is None else "CLV {:+.2%}".format(float(r["clv"]))
        ev = r["ev"] if r["ev"] is not None else 0.0
        print(f"  pt#{r['id']:<6} {r['placed_at']:%m-%d %H:%M} {r['kind']:<6} {r['src'] or '':<11} "
              f"EV {ev:+5.2f}%  {float(r['stake_units']):.2f}u @ "
              f"{float(r['entry_price']):.3f}  {r['result'] or 'open':<5} {clv_txt}  {r['outcome'][:60]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="one cycle: bet, read closes, settle")
    ap.add_argument("--dry-run", action="store_true", help="evaluate, write nothing, spend no credits")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [nfl] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", force=True)
    if args.report:
        report()
        return
    lock = open(os.path.join(HERE, ".nfl_agent.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("another nfl_agent run holds the lock — exiting")
        return
    if args.settle:
        settle()
        return
    run_once(dry_run=args.dry_run, allow_fetch=not args.dry_run)
    if not args.dry_run:
        settle()


if __name__ == "__main__":
    main()
