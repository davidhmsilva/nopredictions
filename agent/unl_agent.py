"""
unl_agent.py — the UEFA Nations League every-game agent. PAPER, no orders.

The NFL agent's structure (nfl_agent.py), moved to soccer. Every Nations League
match Polymarket lists gets exactly one bet, and which bet is the agent's
choice: across the match's full-time markets — the three 1X2 questions (Yes and
No on each), every goals line, every handicap, both teams to score — buy the one
token whose executable ask is cheapest against the sharp line, net of the fee.

Fair value is never ours. It comes from Pinnacle (The Odds API; a median of
other books when Pinnacle has not posted), de-vigged. Where Polymarket asks the
same question Pinnacle prices — the 1X2, the main total, the main handicap when
it is a half line — that de-vigged price IS the fair value (`sharp_exact`).
Everything else is read off `soccer_line_model`, a Dixon-Coles Poisson solved
per match to reproduce Pinnacle's 1X2 AND main total (`sharp_model`, docked a
haircut that grows with the distance from the sharp line).

Two kinds of bet, kept apart in every report:
  * EDGE   — net EV at the CLOB ask clears the threshold (1.5% exact, 3% model),
             1 unit flat, from 24h before kick-off.
  * FORCED — the match is inside 40 minutes of kick-off with no bet yet: the
             best EV on the board, whatever its sign, 1 unit. The price of
             "every game".

What the project already knows, and why the rules look like this:
  * PM's pre-match mid sits ON the de-vigged Pinnacle line in club soccer
    (finding_pm_mid_is_pinnacle), and the pre-match factory found nothing that
    beats the close after PM's costs. Expect FORCED to lose the half-spread plus
    the fee and EDGE to be rare. Internationals are thinner books with more
    casual money — that is the only reason to expect anything different, and
    the EDGE/FORCED split is how it gets measured rather than argued.
  * A stale sharp price fabricates edge; an EDGE bet needs a fresh snapshot.
  * Every large claimed edge in this repo's history was our own bug. Anything
    above IMPLAUSIBLE_EV_PCT is logged and refused.
  * Teams are read from the question TEXT ("Will Spain win…") and matched to
    the sharp feed by name, never from title order. The pricing is
    team-relative, so which side PM calls home never enters it.
  * Only full-time markets. Polymarket splits a fixture across sibling events
    ("- Halftime Result", "- Exact Score", "- Total Corners"); only the main
    event and its "- More Markets" sibling are read, and every market is
    filtered on its exact `sportsMarketType`, never on question text.

    python unl_agent.py --once --dry-run   # evaluate the board, write nothing
    python unl_agent.py --once             # the cron entry: bet, capture closes, settle
    python unl_agent.py --settle
    python unl_agent.py --report
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
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "../ingest/.env"))
sys.path.insert(0, HERE)

import db_txn                         # noqa: E402
import soccer_line_model as sm        # noqa: E402
from edge_engine import FEE_RATE, taker_fee_pp   # noqa: E402

log = logging.getLogger("unl_agent")

DATABASE_URL = os.getenv("DATABASE_URL")
ODDS_KEY = os.getenv("THE_ODDS_API_KEY")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/soccer_uefa_nations_league/odds"
ESPN_UNL = "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/scoreboard"

STRATEGY_NAME = "Nations League Every Game"
OBS_VERSION = 1

# ── the sharp feed ───────────────────────────────────────────────────────────
# One request returns every Nations League match: 3 markets × ceil(books/10) = 3
# credits. The key is SHARED with the NFL agent (500/month on the free plan), so
# the cadence is rationed by the nearest unbet match, exactly as there.
BOOKS = ["pinnacle", "betfair_ex_eu", "matchbook", "marathonbet", "williamhill",
         "unibet_eu", "betsson", "nordicbet", "sport888", "onexbet"]
ODDS_CACHE = os.path.join(HERE, ".unl_odds_cache.json")
RESERVE_CREDITS = 60
HORIZON_MIN = 24 * 60
FORCE_MIN = 40
CLOSE_MIN = 12


def required_age_min(mins_to_ko: float) -> float | None:
    """How old a sharp snapshot may be for a match this far from kick-off."""
    if mins_to_ko <= FORCE_MIN:
        return 20
    if mins_to_ko <= 150:
        return 45
    if mins_to_ko <= HORIZON_MIN:
        return 180
    return None


# ── what may be bought ───────────────────────────────────────────────────────
FAMILIES = ("moneyline", "totals", "spreads", "both_teams_to_score")
MAX_SPREAD = 0.03             # s18's measured break point on a soccer book
MIN_LIQUIDITY_USD = 1000
MIN_ASK, MAX_ASK = 0.05, 0.95
VERIFY_N = 6
UNIT_USD = 10.0

EDGE_MIN_EXACT = 1.5
EDGE_MIN_MODEL = 3.0
# Model haircut: (base pp, pp per goal from the sharp line, cap pp). A Poisson
# solved on the main total is good near it and least checked far from it; the
# handicap needs the margin distribution, which the 1X2 pins only loosely.
HAIRCUT_PP = {"moneyline": (0.75, 0.0, 0.75), "totals": (0.75, 0.75, 2.5),
              "spreads": (1.0, 1.0, 3.0), "both_teams_to_score": (1.5, 0.0, 1.5)}
MAX_LINE_DIST = 2.0           # goals from the sharp line; nothing further is priced
MAX_RESID_PP = 1.0            # the model must reproduce the book's own numbers
IMPLAUSIBLE_EV_PCT = 12.0
STAKE_U = 1.0

DRAW = "Draw"


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


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


# National-team spellings differ across Polymarket, The Odds API and ESPN. Every
# spelling folds to one key; two names match only on EQUAL keys, never a score.
NATION_ALIASES = {
    "czechrepublic": "czechia",
    "turkey": "turkiye",
    "republicofireland": "ireland",
    "irelandrepublic": "ireland",
    "bosnia": "bosniaherzegovina",
    "fyrmacedonia": "northmacedonia",
    "macedonia": "northmacedonia",
    "faroes": "faroeislands",
    "holland": "netherlands",
    "moldovarepublic": "moldova",
    "russianfederation": "russia",
    "unitedkingdomengland": "england",
}


def canon(s: str | None) -> str:
    s = (s or "").replace("ø", "o").replace("Ø", "O").replace("ı", "i").replace("&", " and ")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(and|the)\b", " ", s)          # whole words only: not Iceland, Scotland
    s = re.sub(r"[^a-z0-9]", "", s)
    for suffix in ("nationalteam", "fc"):
        if s.endswith(suffix) and len(s) > len(suffix) + 2:
            s = s[: -len(suffix)]
    return NATION_ALIASES.get(s, s)


def devig(odds: list[float]) -> tuple[list[float], list[float]]:
    """(proportional, power) for any number of outcomes. Both, always."""
    xs = [1.0 / o for o in odds]
    tot = sum(xs)
    prop = [x / tot for x in xs]
    lo, hi = 0.2, 5.0
    for _ in range(60):
        k = 0.5 * (lo + hi)
        if sum(x ** k for x in xs) > 1:
            lo = k
        else:
            hi = k
    k = 0.5 * (lo + hi)
    return prop, [x ** k for x in xs]


def cost_per_share(ask: float) -> float:
    return ask * (1.0 + FEE_RATE * (1.0 - ask))


def ev_pct(fair: float, ask: float) -> float:
    return 100.0 * (fair / cost_per_share(ask) - 1.0)


def is_half(x: float) -> bool:
    return abs(abs(x) % 1 - 0.5) < 1e-9


# ── Polymarket ───────────────────────────────────────────────────────────────

TITLE_RE = re.compile(r"^(.+?)\s+vs\.?\s+(.+?)$", re.I)
WIN_RE = re.compile(r"^will\s+(?P<team>.+?)\s+win(?:\s+on\s+[\d-]+)?\??$", re.I)
DRAW_RE = re.compile(r"\bend in a draw\b", re.I)
SPREAD_Q = re.compile(r"^Spread:\s*(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)\s*$", re.I)
OU_RE = re.compile(r"\bO/U\s*(\d+(?:\.\d+)?)\b", re.I)


def is_unl(ev: dict) -> bool:
    """A UEFA Nations League event, by its tags or its series/slug. CONCACAF's
    competition shares the name and is refused."""
    keys = [ev.get("seriesSlug"), ev.get("slug")]
    for t in ev.get("tags") or []:
        keys += [t.get("slug"), t.get("label")]
    for s in (ev.get("series") or []):
        keys += [s.get("slug"), s.get("title")]
    blob = " ".join(re.sub(r"[^a-z0-9]", "", str(k or "").lower()) for k in keys)
    if "concacaf" in blob:
        return False
    return "nationsleague" in blob or bool(re.match(r"^unl[a-z]*\d?-", str(ev.get("slug") or "")))


def base_title(title: str) -> tuple[str, str | None]:
    """('A vs. B', sibling suffix or None)."""
    m = re.search(r"\s+-\s+(.+)$", title or "")
    return (title[: m.start()].strip(), m.group(1).strip()) if m else ((title or "").strip(), None)


@dataclass
class Game:
    slug: str                  # the main event's slug
    title: str
    kickoff: datetime
    teams: tuple               # (team A, team B) in Polymarket's title order — labels only
    markets: list = field(default_factory=list)

    def team(self, label: str | None) -> str | None:
        k = canon(label)
        hits = [t for t in self.teams if canon(t) == k]
        return hits[0] if len(hits) == 1 else None

    def other(self, team: str) -> str:
        return self.teams[1] if team == self.teams[0] else self.teams[0]


def games_from_events(events: list[dict], now: datetime) -> list[Game]:
    """Group the main event with its "- More Markets" sibling; everything else
    ("- Halftime Result", "- Exact Score", corners…) is not a full-time market
    and is dropped here."""
    groups: dict[tuple, dict] = {}
    for ev in events:
        if not is_unl(ev) or ev.get("live") or ev.get("ended"):
            continue
        base, suffix = base_title(ev.get("title") or "")
        if suffix is not None and suffix.lower() != "more markets":
            continue
        ko = _ts(ev.get("startTime")) or next(
            (_ts(m.get("gameStartTime")) for m in ev.get("markets") or [] if m.get("gameStartTime")), None)
        tm = TITLE_RE.match(base)
        if not ko or not tm or ko <= now:
            continue
        key = (canon(tm.group(1)), canon(tm.group(2)), ko.strftime("%Y-%m-%d"))
        g = groups.setdefault(key, {"main": None, "teams": (tm.group(1).strip(), tm.group(2).strip()),
                                    "ko": ko, "title": base, "markets": [], "slugs": []})
        if suffix is None:
            g["main"] = ev.get("slug")
        g["slugs"].append(ev.get("slug"))
        g["markets"] += ev.get("markets") or []
    out = []
    for g in groups.values():
        if not g["main"]:
            continue                      # a "More Markets" with no main event: skip
        if canon(g["teams"][0]) == canon(g["teams"][1]):
            continue
        out.append(Game(slug=g["main"], title=g["title"], kickoff=g["ko"],
                        teams=g["teams"], markets=g["markets"]))
    return sorted(out, key=lambda x: x.kickoff)


def fetch_pm_games(now: datetime) -> list[Game]:
    """Soccer events kicking off before the horizon, bounded by plain DATES
    (a fixture's endDate is its kick-off; ISO bounds drop negRisk events —
    lab_strategy_runner.fetch_upcoming). Filtered to the Nations League client
    side, so a tag slug we guessed wrong cannot silently empty the board."""
    params = {"tag_slug": "soccer", "closed": "false", "limit": 100,
              "order": "startTime", "ascending": "true",
              "end_date_min": now.strftime("%Y-%m-%d"),
              "end_date_max": (now + timedelta(minutes=HORIZON_MIN, days=1)).strftime("%Y-%m-%d")}
    events: list[dict] = []
    for page in range(25):
        try:
            r = requests.get(f"{GAMMA}/events", params={**params, "offset": page * 100}, timeout=25)
            r.raise_for_status()
            batch = r.json()
        except Exception as exc:                        # noqa: BLE001
            log.warning(f"gamma page {page}: {exc}")
            break
        if not isinstance(batch, list) or not batch:
            break
        events += batch
        if len(batch) < 100:
            break
    games = games_from_events(events, now)
    log.info(f"gamma: {len(events)} soccer events scanned, "
             f"{sum(is_unl(e) for e in events)} Nations League, {len(games)} fixtures")
    return games


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
        log.warning("no THE_ODDS_API_KEY — only FORCED bets at PM's own mid are possible")
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
    log.info(f"odds api: {len(c['data'])} Nations League matches, {c['remaining']} credits left")
    c["fetched_at_dt"] = _ts(c["fetched_at"])
    return c


@dataclass
class Sharp:
    book: str                  # 'pinnacle' | 'consensus(n)'
    home: str                  # OUR team names (Game.teams), oriented by the feed
    away: str
    anchor: sm.Anchor
    x12: dict                  # team | 'Draw' -> (prop, pow)
    spread: dict               # team -> (point, prop, pow)   pinnacle only
    total: tuple | None        # (point, (prop_o, pow_o), (prop_u, pow_u))   pinnacle only
    model_ok: bool
    snapshot_at: datetime

    def is_home(self, team: str) -> bool:
        return team == self.home


def _book_view(bk: dict, fh: str, fa: str) -> dict | None:
    """One book's de-vigged view, keyed by the FEED's own team names."""
    mk = {m["key"]: m for m in bk.get("markets") or []}
    out: dict = {"x12": {}, "spread": {}, "total": None}
    h2h = {o["name"]: o["price"] for o in (mk.get("h2h") or {}).get("outcomes") or []}
    if fh in h2h and fa in h2h and DRAW in h2h:
        prop, pw = devig([h2h[fh], h2h[DRAW], h2h[fa]])
        out["x12"] = {fh: (prop[0], pw[0]), DRAW: (prop[1], pw[1]), fa: (prop[2], pw[2])}
    sp = {o["name"]: o for o in (mk.get("spreads") or {}).get("outcomes") or []}
    if fh in sp and fa in sp and sp[fh].get("point") is not None:
        prop, pw = devig([sp[fh]["price"], sp[fa]["price"]])
        out["spread"] = {fh: (float(sp[fh]["point"]), prop[0], pw[0]),
                         fa: (float(sp[fa]["point"]), prop[1], pw[1])}
    tot = {o["name"]: o for o in (mk.get("totals") or {}).get("outcomes") or []}
    if "Over" in tot and "Under" in tot and tot["Over"].get("point") is not None:
        prop, pw = devig([tot["Over"]["price"], tot["Under"]["price"]])
        out["total"] = (float(tot["Over"]["point"]), (prop[0], pw[0]), (prop[1], pw[1]))
    return out if out["x12"] else None


def _mid(p) -> float:
    return 0.5 * (p[0] + p[1])


def sharp_for(game: Game, cache: dict) -> Sharp | None:
    """Match the fixture by both team names (canonical, set-equal), within a
    kick-off window; orient home/away by the FEED, which is the one that knows."""
    want = {canon(t) for t in game.teams}
    ev = None
    for e in cache.get("data") or []:
        ct = _ts(e.get("commence_time"))
        if {canon(e.get("home_team")), canon(e.get("away_team"))} == want and ct \
                and abs((ct - game.kickoff).total_seconds()) <= 3 * 3600:
            ev = e
            break
    if ev is None:
        return None
    fh, fa = ev["home_team"], ev["away_team"]
    home, away = game.team(fh), game.team(fa)
    if not home or not away or home == away:
        return None
    rename = {fh: home, fa: away, DRAW: DRAW}
    books = {b["key"]: b for b in ev.get("bookmakers") or []}
    snap = cache["fetched_at_dt"]

    def build(book, view):
        # an Asian quarter/whole total (2.25, 3.0) pushes part of the stake, so its
        # de-vigged price is not P(over): it neither anchors the model nor prices exactly
        tot = view["total"] if view["total"] and is_half(view["total"][0]) else None
        a = sm.fit(_mid(view["x12"][fh]), _mid(view["x12"][fa]),
                   tot[0] if tot else None, _mid(tot[1]) if tot else None)
        x12 = {rename[k]: v for k, v in view["x12"].items()}
        spread = {rename[k]: v for k, v in view["spread"].items()}
        return Sharp(book, home, away, a, x12, spread, tot, a.resid_pp <= MAX_RESID_PP, snap)

    if "pinnacle" in books:
        v = _book_view(books["pinnacle"], fh, fa)
        if v:
            return build("pinnacle", v)
    views = [v for k, b in books.items() if k != "pinnacle"
             for v in [_book_view(b, fh, fa)] if v]
    if len(views) < 2:
        return None
    x12 = {k: (median(v["x12"][k][0] for v in views), median(v["x12"][k][1] for v in views))
           for k in (fh, DRAW, fa)}
    # the modal main total, and the median over-price among books quoting it
    lines = [v["total"][0] for v in views if v["total"] and is_half(v["total"][0])]
    tot = None
    if lines:
        L = max(set(lines), key=lines.count)
        at = [v["total"] for v in views if v["total"] and v["total"][0] == L]
        tot = (L, (median(t[1][0] for t in at), median(t[1][1] for t in at)),
               (median(t[2][0] for t in at), median(t[2][1] for t in at)))
    return build(f"consensus({len(views)})", {"x12": x12, "spread": {}, "total": tot})


# ── candidates ───────────────────────────────────────────────────────────────

@dataclass
class Cand:
    family: str                # moneyline | totals | spreads | both_teams_to_score
    subject: str | None        # moneyline: the team or 'Draw'; else None
    line: float | None         # spreads: the side's own line; totals: the line
    side: str                  # 'Yes'/'No' | 'Over'/'Under' | a team name
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
            what = "Draw" if self.subject == DRAW else f"{self.subject} win"
            return f"{what} — {self.side}"
        if self.family == "spreads":
            return f"{self.side} {self.line:+g}"
        if self.family == "totals":
            return f"{self.side} {self.line:g}"
        return f"BTTS — {self.side}"


def _haircut(family: str, dist: float) -> float:
    base, per, cap = HAIRCUT_PP[family]
    return min(base + per * dist, cap) / 100.0


def _yes_no(outs: list) -> list[str] | None:
    labels = [str(o).strip().lower() for o in outs]
    return labels if sorted(labels) == ["no", "yes"] else None


def candidates(game: Game, sh: Sharp | None) -> list[Cand]:
    """Every full-time token whose Gamma book passes the gates, priced. With no
    sharp line the fair value is PM's own mid — only ever used to pick the token
    to hold when a bet is forced."""
    out: list[Cand] = []
    g = sh.anchor.grid() if sh else None
    ok = bool(sh and sh.model_ok)
    main_total = sh.total[0] if sh and sh.total else None
    seen: set[str] = set()
    for m in game.markets:
        fam = str(m.get("sportsMarketType") or "").lower()
        cid = m.get("conditionId") or ""
        if fam not in FAMILIES or cid in seen or m.get("closed") or m.get("active") is False \
                or m.get("acceptingOrders") is False:
            continue
        seen.add(cid)
        outs, toks = _jl(m.get("outcomes")), _jl(m.get("clobTokenIds"))
        bb, ba, liq = _f(m.get("bestBid")), _f(m.get("bestAsk")), _f(m.get("liquidityNum")) or 0.0
        if len(outs) != 2 or len(toks) != 2 or bb is None or ba is None:
            continue
        if ba - bb > MAX_SPREAD + 1e-9 or liq < MIN_LIQUIDITY_USD:
            continue
        legs = [(str(outs[0]), str(toks[0]), bb, ba), (str(outs[1]), str(toks[1]), 1 - ba, 1 - bb)]
        q = (m.get("question") or "").strip()
        rem = q.split(": ", 1)[1] if ": " in q else q
        # per leg: (side, subject, line, fair or None, source, dist)
        priced: dict[str, tuple] = {}

        if fam == "moneyline":
            yn = _yes_no(outs)
            if yn is None:
                continue
            if DRAW_RE.search(rem):
                subj = DRAW
            else:
                w = WIN_RE.match(rem)
                subj = game.team(w.group("team")) if w else None
                if subj is None:
                    continue                      # a team we cannot name: fail closed
            p, src = None, "pm_mid"
            if sh and subj in sh.x12:
                p, src = min(sh.x12[subj]), "sharp_exact"
            for lab in ("yes", "no"):
                f = None if p is None else (p if lab == "yes" else 1 - max(sh.x12[subj]))
                priced[lab] = (lab.capitalize(), subj, None, f, src, 0.0)

        elif fam == "totals":
            mo = OU_RE.search(q)
            ln = _f(m.get("line"))
            ln = ln if ln is not None else (float(mo.group(1)) if mo else None)
            labels = [str(o).strip().lower() for o in outs]
            if ln is None or not is_half(ln) or sorted(labels) != ["over", "under"] or not mo \
                    or abs(float(mo.group(1)) - ln) > 1e-9:
                continue
            if any(w in q.lower() for w in ("corner", "card", "1h", "2h", "half", "shot")):
                continue
            if sh and sh.total and abs(sh.total[0] - ln) < 1e-9:
                po = (min(sh.total[1]), min(sh.total[2]))
                priced["over"] = ("Over", None, ln, po[0], "sharp_exact", 0.0)
                priced["under"] = ("Under", None, ln, po[1], "sharp_exact", 0.0)
            elif ok:
                fo = sm.over_prob(g, ln)
                d = abs(ln - main_total) if main_total is not None else abs(ln - 2.5)
                priced["over"] = ("Over", None, ln, fo, "sharp_model", d)
                priced["under"] = ("Under", None, ln, 1 - fo, "sharp_model", d)
            else:
                priced["over"] = ("Over", None, ln, None, "pm_mid", 0.0)
                priced["under"] = ("Under", None, ln, None, "pm_mid", 0.0)

        elif fam == "spreads":
            smq = SPREAD_Q.match(q)
            ln = _f(m.get("line"))
            if not smq:
                continue
            qln = float(smq.group(2))
            if ln is not None and abs(ln - qln) > 1e-9:
                continue
            ln = qln
            x = game.team(smq.group(1))
            if x is None or not is_half(ln):
                continue
            y = game.other(x)
            if {game.team(o) for o in outs} != {x, y}:
                continue
            pin = sh.spread if sh else {}
            if pin.get(x) and abs(pin[x][0] - ln) < 1e-9:
                fx, fy, src, d = min(pin[x][1:]), min(pin[y][1:]), "sharp_exact", 0.0
            elif ok:
                fx = sm.cover_prob(g, ln, sh.is_home(x))
                fy, src = 1 - fx, "sharp_model"
                d = abs(ln - pin[x][0]) if pin.get(x) else abs(ln + (sh.anchor.lh - sh.anchor.la) * (1 if sh.is_home(x) else -1))
            else:
                fx = fy = None
                src, d = "pm_mid", 0.0
            for o in outs:
                t = game.team(o)
                priced[str(o).strip().lower()] = (t, None, ln if t == x else -ln,
                                                   fx if t == x else fy, src, d)

        else:  # both_teams_to_score
            yn = _yes_no(outs)
            if yn is None or any(w in q.lower() for w in ("half", "1h", "2h")):
                continue
            fb = sm.btts_prob(g) if ok else None
            src = "sharp_model" if ok else "pm_mid"
            priced["yes"] = ("Yes", None, None, fb, src, 0.0)
            priced["no"] = ("No", None, None, None if fb is None else 1 - fb, src, 0.0)

        for lbl, tok, bid, ask in legs:
            p = priced.get(lbl.strip().lower())
            if p is None or not (MIN_ASK <= ask <= MAX_ASK):
                continue
            side, subj, line, fair, src, dist = p
            if src == "pm_mid":
                fair = 0.5 * (bid + ask)
            if src == "sharp_model" and dist > MAX_LINE_DIST + 1e-9:
                continue
            raw = fair
            if src == "sharp_model":
                fair = raw - _haircut(fam, dist)
            c = Cand(fam, subj, line, side, q, cid, tok, bid, ask, liq, fair, raw, src, dist)
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
        if c.source == "pm_mid":
            c.fair = c.fair_raw = 0.5 * (c.bid + c.ask)
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
                "H-UNL-SHARP — one bet on every Nations League match, the best-EV token "
                "against the sharp line",
                "On every Polymarket UEFA Nations League match, the cheapest full-time token "
                "against the de-vigged Pinnacle line (Dixon-Coles Poisson fitted to its 1X2 + "
                "total for alternates, BTTS and handicaps) returns more than the fee. Edge bets "
                "(EV >= 1.5% exact / 3% model) and forced bets are measured separately.",
                "Club-soccer PM mids sit on Pinnacle and no pre-match rule beat the close after "
                "costs. Internationals are the case where casual, patriotic money is largest "
                "relative to the book — the one reason to expect a different answer. Verdict "
                "gate: n >= 200 edge bets, net yield CI clear of zero, positive pm_clv."))
            hyp = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO strategies (hypothesis_id, name, rules, source, run_status, theory)
                VALUES (%s, %s, %s::jsonb, 'agent', 'running', %s) RETURNING id""", (
                hyp, STRATEGY_NAME, json.dumps({
                    "venue": "polymarket", "sport": "soccer",
                    "competition": "UEFA Nations League", "phase": "paper-only",
                    "self_settling": True, "obs_version": OBS_VERSION,
                    "fair_value": "pinnacle de-vigged; soccer_line_model (Dixon-Coles) for alternates",
                    "edge_min_exact_pct": EDGE_MIN_EXACT, "edge_min_model_pct": EDGE_MIN_MODEL,
                    "force_min": FORCE_MIN, "stake_u": STAKE_U,
                }), "Bet every Nations League match on Polymarket, choosing the market with "
                    "the best price against the sharp line."))
            sid = cur.fetchone()[0]
    log.info(f"created strategy '{STRATEGY_NAME}' id={sid}")
    return sid


def bet_games(conn, sid: int) -> dict[str, dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pm.raw_metadata->>'game_slug' AS slug, pt.id, pt.pm_token_id, pt.entry_price,
                   pt.closing_price, pt.placed_at
              FROM paper_trades pt JOIN pm_markets pm ON pm.id = pt.market_id
             WHERE pt.strategy_id = %s""", (sid,))
        return {r["slug"]: dict(r) for r in cur.fetchall()}


def last_snapshot_written(conn) -> dict[str, datetime]:
    with conn.cursor() as cur:
        cur.execute("""SELECT game_slug, max(odds_snapshot_at) FROM unl_candidates
                        WHERE observed_at > now() - interval '3 days' GROUP BY 1""")
        return {s: t for s, t in cur.fetchall()}


def _r(x, n):
    return None if x is None else round(x, n)


def write_candidates(conn, game: Game, sh: Sharp | None, cands: list[Cand], now: datetime,
                     chosen: Cand | None = None, trade_id: int | None = None) -> None:
    mins = (game.kickoff - now).total_seconds() / 60
    a = sh.anchor if sh else None
    rows = [(OBS_VERSION, game.slug, game.kickoff, round(mins, 1),
             sh.home if sh else game.teams[0], sh.away if sh else game.teams[1],
             c.family, c.subject, c.line, c.side, c.condition_id, c.token_id, c.bid, c.ask,
             c.liquidity, round(c.fair, 5), round(c.fair_raw, 5), c.source,
             sh.book if sh else None, _r(a.lh if a else None, 3), _r(a.la if a else None, 3),
             _r(a.rho if a else None, 3), _r(a.resid_pp if a else None, 3),
             sh.total[0] if sh and sh.total else None,
             round(taker_fee_pp(c.ask), 3), round(c.ev, 3),
             sh.snapshot_at if sh else None, c is chosen, trade_id if c is chosen else None)
            for c in cands]
    if not rows:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO unl_candidates (obs_version, game_slug, kickoff, minutes_to_ko, home_team,
                away_team, market_type, subject, line, side, condition_id, token_id, pm_bid,
                pm_ask, pm_liquidity, fair, fair_raw, fair_source, fair_book, anchor_lh,
                anchor_la, anchor_rho, anchor_resid_pp, sharp_total_line, fee_pp, ev_pct,
                odds_snapshot_at, chosen, paper_trade_id)
            VALUES %s""", rows)


def write_trade(conn, sid: int, game: Game, sh: Sharp | None, c: Cand, kind: str,
                stake: float, now: datetime) -> int:
    mins = (game.kickoff - now).total_seconds() / 60
    a = sh.anchor if sh else None
    home, away = (sh.home, sh.away) if sh else game.teams
    meta = {"game_slug": game.slug, "home": home, "away": away, "home_verified": sh is not None,
            "token_id": c.token_id, "family": c.family, "subject": c.subject, "side": c.side,
            "line": c.line, "sport": "soccer", "competition": "UEFA Nations League"}
    src = {"kind": kind, "fair_source": c.source, "book": sh.book if sh else None,
           "fair_raw": round(c.fair_raw, 5), "fair": round(c.fair, 5), "ev_pct": round(c.ev, 3),
           "fee_pp": round(taker_fee_pp(c.ask), 3), "dist_goals": c.dist,
           "lambda_home": _r(a.lh if a else None, 3), "lambda_away": _r(a.la if a else None, 3),
           "rho": _r(a.rho if a else None, 3), "resid_pp": _r(a.resid_pp if a else None, 3),
           "sharp_total": sh.total[0] if sh and sh.total else None,
           "snapshot_at": sh.snapshot_at.isoformat() if sh else None,
           "minutes_to_ko": round(mins, 1), "obs_version": OBS_VERSION}
    fair_txt = (f"fair {c.fair:.3f} ({1 / c.fair:.2f}) from {c.source}"
                + (f" [{sh.book}, λ {a.lh:.2f}-{a.la:.2f}]" if sh else " — NO sharp line, PM mid used"))
    reasoning = (
        f"NATIONS LEAGUE {kind.upper()} — {game.title}, kick-off {game.kickoff:%Y-%m-%d %H:%M}Z "
        f"({mins:.0f} min out). {c.label} [{c.family}]: CLOB ask {c.ask:.3f} ({1 / c.ask:.2f}), "
        f"bid {c.bid:.3f}, depth ${c.depth_usd or 0:,.0f}; {fair_txt}; "
        f"net EV {c.ev:+.2f}% after a {taker_fee_pp(c.ask):.2f}pp fee. {stake}u, PAPER.")
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pm_markets (platform, external_id, title, market_type, category,
                                        resolution_time, status, raw_metadata, ingested_at)
                VALUES ('polymarket', %s, %s, %s, 'soccer', %s, 'active', %s::jsonb, now())
                ON CONFLICT (platform, external_id) DO UPDATE
                   SET title = EXCLUDED.title, resolution_time = EXCLUDED.resolution_time,
                       raw_metadata = COALESCE(pm_markets.raw_metadata, '{}'::jsonb) || EXCLUDED.raw_metadata
                RETURNING id""", (c.condition_id, c.question, f"unl_{c.family}", game.kickoff,
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
    age = (now - cache["fetched_at_dt"]).total_seconds() / 60 if cache else 1e9
    for g in games:
        mins = (g.kickoff - now).total_seconds() / 60
        if g.slug in bets:
            if bets[g.slug].get("closing_price") is None and 0 < mins <= CLOSE_MIN and age > 8:
                return "close"
            continue
        req = required_age_min(mins)
        if req is not None and age > req:
            return "force" if mins <= FORCE_MIN else "edge"
    return None


def decide(best: Cand | None, fresh: bool, mins: float) -> str | None:
    """'edge', 'forced' or None — the rule, apart from the I/O, so it is testable."""
    if best is None:
        return None
    th = EDGE_MIN_EXACT if best.source == "sharp_exact" else EDGE_MIN_MODEL
    kind = None
    if fresh and best.source != "pm_mid" and best.ev >= th:
        kind = "edge"
    elif mins <= FORCE_MIN:
        kind = "forced"
    if kind and (best.depth_usd or 0) < STAKE_U * UNIT_USD:
        return None
    return kind


def run_once(dry_run: bool = False, allow_fetch: bool = True) -> None:
    now = datetime.now(timezone.utc)
    games = fetch_pm_games(now)
    conn = _conn()
    if dry_run:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
            r = cur.fetchone()
            sid = r[0] if r else -1
    else:
        sid = ensure_strategy(conn)
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
    log.info(f"{len(games)} Nations League matches listed · "
             f"{sum(g.slug in bets for g in games)} already bet · sharp snapshot {snap_txt}")

    written = last_snapshot_written(conn) if not dry_run else {}
    todo: list[tuple] = []
    closes: list[tuple] = []
    for g in games:
        mins = (g.kickoff - now).total_seconds() / 60
        req = required_age_min(mins)
        sh = sharp_for(g, cache) if cache else None

        if g.slug in bets:
            b = bets[g.slug]
            if b["closing_price"] is None and 0 < mins <= CLOSE_MIN and sh and age is not None and age <= 8:
                hit = [c for c in candidates(g, sh) if c.token_id == b["pm_token_id"]]
                if hit and hit[0].source != "pm_mid":
                    c = hit[0]
                    closes.append((b["id"], c.fair_raw, 0.5 * (c.bid + c.ask), sh.book,
                                   float(b["entry_price"])))
            continue
        if req is None:
            continue

        fresh = sh is not None and age is not None and age <= req + 5
        cands = candidates(g, sh)
        if not cands:
            log.info(f"  {g.title:<32} {mins:6.0f}m  no book passes the gates")
            continue
        if sh is None and mins > FORCE_MIN:
            log.info(f"  {g.title:<32} {mins:6.0f}m  no sharp line matched yet — waiting")
            continue
        ver = verify(cands)
        for c in ver:
            if c.ev > IMPLAUSIBLE_EV_PCT:
                log.warning(f"  {g.title}: {c.label} EV {c.ev:+.1f}% > {IMPLAUSIBLE_EV_PCT}% — "
                            f"refused as a probable bug (fair {c.fair:.3f} {c.source}, ask {c.ask:.3f})")
        sane = [c for c in ver if c.ev <= IMPLAUSIBLE_EV_PCT]
        best = max(sane, key=lambda c: c.ev) if sane else None
        tag = (f"{sh.book} λ {sh.anchor.lh:.2f}-{sh.anchor.la:.2f} ρ {sh.anchor.rho:+.2f} "
               f"resid {sh.anchor.resid_pp:.2f}pp{'' if sh.model_ok else ' MODEL-OFF'}"
               if sh else "no sharp")
        if best is None:
            log.info(f"  {g.title:<32} {mins:6.0f}m  nothing verified on the CLOB · {tag}")
            continue
        kind = decide(best, fresh, mins)
        log.info(f"  {g.title:<32} {mins:6.0f}m  best {best.label:<30} ask {best.ask:.3f} "
                 f"fair {best.fair:.3f} {best.source:<11} EV {best.ev:+5.2f}%"
                 f"{'  → ' + kind.upper() + f' {STAKE_U}u' if kind else ''} · {tag}")
        todo.append((g, sh, cands, best if kind else None, kind,
                     sh is not None and written.get(g.slug) != sh.snapshot_at))

    if dry_run:
        log.info(f"DRY RUN — {sum(1 for t in todo if t[4])} bets would be placed, "
                 f"{len(closes)} closes read; nothing written")
        return

    placed = 0
    for g, sh, cands, chosen, kind, new_snap in todo:
        tid = None
        if chosen:
            tid = write_trade(conn, sid, g, sh, chosen, kind, STAKE_U, now)
            placed += 1
            log.info(f"  PLACED pt#{tid} {kind} {chosen.label} — {g.title} @ {chosen.ask:.3f} {STAKE_U}u")
        if new_snap:
            write_candidates(conn, g, sh, cands, now, chosen, tid)
        elif chosen:
            write_candidates(conn, g, sh, [chosen], now, chosen, tid)
    for tid, fair, mid, book, entry in closes:
        with conn.cursor() as cur:
            cur.execute("""UPDATE paper_trades SET closing_price = %s, clv = %s, clv_source = %s,
                                  pm_closing_price = %s, pm_closing_at = now(), pm_clv = %s
                            WHERE id = %s AND closing_price IS NULL""",
                        (round(fair, 5), round(fair / entry - 1, 5), f"{book}_close_unl",
                         round(mid, 4), round(mid / entry - 1, 5), tid))
        log.info(f"  close pt#{tid}: sharp fair {fair:.3f} vs entry {entry:.3f} · "
                 f"PM mid {mid:.3f} → pm_clv {mid / entry - 1:+.2%}")
    log.info(f"placed {placed} · closes {len(closes)}")


# ── settlement ───────────────────────────────────────────────────────────────

FINAL_STATES = {"STATUS_FINAL", "STATUS_FULL_TIME"}   # never AET / PEN: 90 minutes only


def _espn_finals() -> dict[frozenset, dict[str, int]]:
    """{frozenset(canon names): {canon name: goals}} for FINISHED matches only.
    The match STATE gates it, never a clock. ⚠️ DO NOT SET A USER-AGENT."""
    try:
        d = requests.get(ESPN_UNL, timeout=15).json()
    except Exception as exc:                                        # noqa: BLE001
        log.warning(f"espn scoreboard unavailable ({exc}) — no provisional grading this run")
        return {}
    out: dict[frozenset, dict[str, int]] = {}
    for ev in d.get("events") or []:
        for c in ev.get("competitions") or []:
            if ((c.get("status") or {}).get("type") or {}).get("name") not in FINAL_STATES:
                continue
            sc = {}
            for t in c.get("competitors") or []:
                nm_ = (t.get("team") or {}).get("displayName")
                try:
                    sc[canon(nm_)] = int(t.get("score"))
                except (TypeError, ValueError):
                    continue
            if len(sc) == 2:
                out[frozenset(sc)] = sc
    return out


def grade(family: str, subject: str | None, line, side: str, team_a: str, team_b: str,
          goals: dict[str, int]) -> tuple[str, str] | None:
    """(result, basis) from a 90-minute score, or None when it cannot be graded.
    `goals` is keyed by canon(team). Fails closed on any name it cannot place."""
    ga, gb = goals.get(canon(team_a)), goals.get(canon(team_b))
    if ga is None or gb is None:
        return None
    score = f"{team_a} {ga}-{gb} {team_b}"
    total = ga + gb

    def yn(truth: bool):
        if side not in ("Yes", "No"):
            return None
        return ("won" if truth == (side == "Yes") else "lost", score)

    if family == "moneyline":
        if subject == DRAW:
            return yn(ga == gb)
        if canon(subject) == canon(team_a):
            return yn(ga > gb)
        if canon(subject) == canon(team_b):
            return yn(gb > ga)
        return None
    if family == "both_teams_to_score":
        return yn(ga > 0 and gb > 0)
    if family == "totals":
        if line is None or side not in ("Over", "Under"):
            return None
        won = total > float(line) if side == "Over" else total < float(line)
        return ("won" if won else "lost", f"{score} — total {total} vs {float(line):g}")
    if family == "spreads":
        if line is None:
            return None
        if canon(side) == canon(team_a):
            mine, theirs = ga, gb
        elif canon(side) == canon(team_b):
            mine, theirs = gb, ga
        else:
            return None
        margin = mine - theirs + float(line)
        res = "won" if margin > 0 else "lost" if margin < 0 else "void"
        return (res, f"{score}, {side} {float(line):+g} → {margin:+g}")
    return None


def provisional(conn, pending: list[dict], verdicts: dict) -> int:
    """Grade from the SCORE the trades the venue has not resolved. Display only:
    `result` and `payout_units` are never touched. Where the venue HAS resolved,
    a disagreement with our grading is logged loudly."""
    unresolved = [p for p in pending if not verdicts.get(p["external_id"])
                  and p.get("family") and p.get("provisional_result") is None]
    resolved = [p for p in pending if verdicts.get(p["external_id"]) and p.get("family")]
    if not (unresolved or resolved):
        return 0
    finals = _espn_finals()
    if not finals:
        return 0

    def graded(p):
        sc = finals.get(frozenset({canon(p["home"]), canon(p["away"])}))
        if not sc:
            return None
        line = _f(p.get("line"))
        return grade(p["family"], p.get("subject"), line, p["side"], p["home"], p["away"], sc)

    for p in resolved:
        g = graded(p)
        v = verdicts[p["external_id"]]
        if not g or p["pm_token_id"] not in v:
            continue
        pay = v[p["pm_token_id"]]
        theirs = "won" if pay == 1.0 else "lost" if pay == 0.0 else "void"
        if g[0] != theirs:
            log.warning(f"  ⚠️ pt#{p['id']} GRADING DISAGREES with the venue: "
                        f"we say {g[0]}, Polymarket resolved {theirs} — {g[1]}")
    n = 0
    for p in unresolved:
        g = graded(p)
        if not g:
            continue
        with conn.cursor() as cur:
            cur.execute("""UPDATE paper_trades
                              SET provisional_result = %s, provisional_detail = %s,
                                  provisional_source = 'espn_final', provisional_at = now()
                            WHERE id = %s AND result IS NULL""", (g[0], g[1], p["id"]))
        log.info(f"  provisional pt#{p['id']}: {g[0].upper()} — {g[1]} (venue has not resolved)")
        n += 1
    return n


def settle() -> int:
    conn = _conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pt.id, pt.pm_token_id, pt.stake_units, pt.entry_price, pm.external_id,
                   pt.provisional_result,
                   pm.raw_metadata->>'family' AS family, pm.raw_metadata->>'subject' AS subject,
                   pm.raw_metadata->>'side' AS side, pm.raw_metadata->>'line' AS line,
                   pm.raw_metadata->>'home' AS home, pm.raw_metadata->>'away' AS away
              FROM paper_trades pt
              JOIN strategies s ON s.id = pt.strategy_id
              JOIN pm_markets pm ON pm.id = pt.market_id
             WHERE s.name = %s AND pt.result IS NULL
               AND pm.resolution_time < now() - interval '2 hours'""", (STRATEGY_NAME,))
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
            verdicts[cid] = {str(t["token_id"]): 0.5 for t in toks}
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
    log.info(f"settled {n} of {len(pending)} finished Nations League trades")
    provisional(conn, pending, verdicts)
    return n


# ── report ───────────────────────────────────────────────────────────────────

def report() -> None:
    conn = _conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pt.id, pt.placed_at, pt.outcome, pt.entry_price, pt.stake_units, pt.result,
                   pt.provisional_result, pt.payout_units, pt.confidence AS kind, pt.pm_clv,
                   (pt.sharp_consensus_sources->>'ev_pct')::float AS ev,
                   pt.sharp_consensus_sources->>'fair_source' AS src
              FROM paper_trades pt JOIN strategies s ON s.id = pt.strategy_id
             WHERE s.name = %s ORDER BY pt.placed_at""", (STRATEGY_NAME,))
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        print("no Nations League trades yet")
        return
    print(f"{'':8}{'n':>4}{'settled':>9}{'won':>5}{'stake':>8}{'net P&L':>9}{'yield':>8}"
          f"{'avg EV':>8}{'pm_clv':>9}")
    for kind in ("edge", "forced", None):
        sub = [r for r in rows if kind is None or r["kind"] == kind]
        if not sub:
            continue
        st = [r for r in sub if r["result"]]
        stake = sum(float(r["stake_units"]) for r in st)
        fee = sum(float(r["stake_units"]) * FEE_RATE * (1 - float(r["entry_price"])) for r in st)
        pnl = sum(float(r["payout_units"] or 0) - float(r["stake_units"]) for r in st) - fee
        evs = [r["ev"] for r in sub if r["ev"] is not None]
        clvs = [float(r["pm_clv"]) for r in sub if r["pm_clv"] is not None]
        print(f"{(kind or 'ALL'):<8}{len(sub):>4}{len(st):>9}{sum(r['result'] == 'won' for r in st):>5}"
              f"{stake:>8.1f}{pnl:>+9.2f}{(100 * pnl / stake if stake else 0):>+7.1f}%"
              f"{(sum(evs) / len(evs) if evs else 0):>+7.2f}%"
              f"{(100 * sum(clvs) / len(clvs) if clvs else float('nan')):>+8.2f}%")
    print("\npm_clv = PM's own mid at the close against the entry ask — price against price. "
          "A yield CI at ~2.0 odds needs thousands of bets; this is a record, not a verdict.")
    print("\nlast 20:")
    for r in rows[-20:]:
        res = r["result"] or (f"~{r['provisional_result']}" if r["provisional_result"] else "open")
        print(f"  pt#{r['id']:<6} {r['placed_at']:%m-%d %H:%M} {r['kind']:<6} {r['src'] or '':<11} "
              f"EV {(r['ev'] or 0):+5.2f}%  {float(r['stake_units']):.2f}u @ "
              f"{float(r['entry_price']):.3f}  {res:<6} {r['outcome'][:70]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="one cycle: bet, read closes, settle")
    ap.add_argument("--dry-run", action="store_true", help="evaluate, write nothing, spend no credits")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [unl] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", force=True)
    if args.report:
        report()
        return
    lock = open(os.path.join(HERE, ".unl_agent.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("another unl_agent run holds the lock — exiting")
        return
    if args.settle:
        settle()
        return
    run_once(dry_run=args.dry_run, allow_fetch=not args.dry_run)
    if not args.dry_run:
        settle()


if __name__ == "__main__":
    main()
