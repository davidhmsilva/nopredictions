"""
Price Reader — every sport, every market, decided by the price alone (paper).

WHAT IT IS (the operator's brief, 2026-11-06)
---------------------------------------------
An agent that tries to call a result in any Polymarket sports market, in any
sport, by looking at nothing but the price. It is free to buy before the start
or during the event, to hold to the end or to sell. Every day it reviews its own
trades and changes how it trades to do better. Its goal is to make money.

WHAT "ONLY THE PRICE" MEANS HERE
--------------------------------
It reads, for every open market of every game that starts within 3 hours or
started within the last 8: the price (Gamma's best bid / ask, confirmed on the
CLOB book before any entry), how that price has moved (its own minute-by-minute
memory, and Gamma's one-hour change), the depth of the book, and the clock
(minutes to or since the start, and Polymarket's own live / ended flags). It
never reads a score, a team, a statistic or a model. A sport is a label it uses
to keep its record apart, never a reason to bet.

HOW IT DECIDES: APPROACHES ("ARMS")
-----------------------------------
A price can be read in several ways, and which way pays (if any) is what the
agent is there to find out. Each way is an arm, a fixed rule with its own record:

  pre_favourite   Short favourite before the start: a 1.08-1.33 price in the
                  last 90 minutes, held to the end. The favourite-longshot bias.
  pre_steam       Follow the money: a price that rose 4pp+ in the last hour,
                  within 3 hours of the start, held to the end.
  grid_no         Fade one exact score: buy "No" on an exact-score / exact-margin
                  line at 0.88-0.97, held. What the venue's biggest winners do.
  live_leader     Back the clear leader late: in play, 70%+ of the usual length
                  of that sport gone, a 1.05-1.25 price, held to the end.
  live_momentum   Ride a live move: up 10pp+ in 10 minutes, now 0.30-0.80;
                  sold at the bid 15 minutes later.
  live_fade       Buy a live overreaction: down 12pp+ in 10 minutes, now
                  0.15-0.70; sold at the bid 15 minutes later.
  post_sweep      Collect after the whistle: the event has ended and a token
                  still sells at 0.90-0.995; held to the payout.

Each arm also has two SHADOW variants, the same rule with one setting moved.
A shadow position is priced and settled exactly like a real one but books no
paper trade, so it can never reach the agent's record. One position per arm
per event: thirty lines of one match are one opinion, not thirty.

🔑 An arm's record is ALL its positions, booked or shadow. Whether a position
   books a paper trade (the arm is active, the sport is not excluded) changes
   what the agent's tab shows, never what the arm is measured on.

WHAT IT WILL NOT DO
-------------------
  * Buy into a broken book: spread over 4pp, under $50 of ask depth in the top
    five levels, or a CLOB ask outside the arm's band (Gamma's price lags).
  * Use a one-sided or empty book as a price (Gamma quotes those at 0 / 1).
  * Trust a 50-50 placeholder ladder (0.02 / 0.98 on every rung): the depth
    gate refuses it.
  * Count a market that resolved without a winner as a loss: it is void.

Every price it pays is the CLOB ask plus Polymarket's sports taker fee
(5% · p · (1 − p) a share); a sale pays the bid minus the same fee. The odds on
the tab are net of that fee.

The daily review that changes which arms trade is price_reader_review.py.
The daemon is price_reader_agent.py. Tables: db/089.
"""
from __future__ import annotations

import json
import math
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

STRATEGY_NAME = "Price Reader — every sport, price only"

FEE_RATE = 0.05                  # Polymarket sports taker fee rate (pm_fees)
STAKE_UNITS = 1.0

MAX_SPREAD = 0.04                # a wider book is not a price (CLAUDE.md: > 5pp is broken)
MIN_ASK_DEPTH_USD = 50.0         # top five ask levels
MAX_GAMMA_SPREAD = 0.06          # a Gamma quote wider than this is not even looked at
MEMORY_MAX_SPREAD = 0.06         # …nor remembered as a price

PRE_HORIZON_MIN = 180            # the scan looks this far ahead
POST_HORIZON_H = 8               # …and this far back

EXIT_MAX_SPREAD = 0.10           # a flip sells into a book no wider than this
EXIT_GIVE_UP_MIN = 30            # no sellable bid this long after it was due: hold to the end

# The usual length of an event, start to final whistle, in minutes. Used for
# one thing only: how late in the event "late" is (live_leader). A sport not
# here is never "late", so live_leader skips it.
DURATION_MIN = {
    "soccer": 115, "basketball": 140, "american-football": 200, "baseball": 190,
    "hockey": 160, "tennis": 150, "esports": 150, "cricket": 240,
    "table-tennis": 50, "volleyball": 120, "handball": 100, "rugby": 100,
}

# Polymarket tags → a sport label. First match wins, so the specific ones lead.
SPORT_TAGS = [
    ("table-tennis", "table-tennis"), ("esports", "esports"), ("counter-strike-2", "esports"),
    ("dota-2", "esports"), ("league-of-legends", "esports"), ("valorant", "esports"),
    ("call-of-duty", "esports"), ("mobile-legends-bang-bang", "esports"),
    ("soccer", "soccer"), ("basketball", "basketball"), ("nba", "basketball"),
    ("wnba", "basketball"), ("ncaa-basketball", "basketball"),
    ("nfl", "american-football"), ("cfb", "american-football"), ("american-football", "american-football"),
    ("mlb", "baseball"), ("baseball", "baseball"), ("kbo", "baseball"), ("npb", "baseball"),
    ("nhl", "hockey"), ("hockey", "hockey"), ("ahl", "hockey"),
    ("tennis", "tennis"), ("atp", "tennis"), ("wta", "tennis"),
    ("cricket", "cricket"), ("volleyball", "volleyball"), ("handball", "handball"),
    ("rugby", "rugby"), ("ufc", "mma"), ("mma", "mma"), ("boxing", "boxing"),
    ("golf", "golf"), ("f1", "motorsport"), ("formula-1", "motorsport"),
]

# Market types that resolve before the event ends (a half, a quarter, a map, a
# first corner). They are excluded from live_leader, whose "late" is the clock
# of the whole event.
_PERIOD_HINTS = ("first_half", "second_half", "halftime", "q1_", "q2_", "q3_", "q4_",
                 "_game_", "_map_", "first_corner", "first_to_score", "1st", "2nd")


# ── the arms ─────────────────────────────────────────────────────────────────

# key → (label, params). The base arm trades from day one; its two variants
# start in shadow. Params are never edited in place once an arm has a record:
# a change is a new arm (a variant), promoted only on evidence.
BASE_ARMS: dict[str, tuple[str, dict]] = {
    "pre_favourite": ("Short favourite before the start", {
        "phase": "pre", "max_min_to_start": 90, "families": ["winner", "totals", "spreads"],
        "no_period": True, "ask_min": 0.75, "ask_max": 0.93, "trigger": "level", "exit": "hold"}),
    "pre_steam": ("Follow the money before the start", {
        "phase": "pre", "max_min_to_start": 180, "families": ["winner", "totals", "spreads"],
        "no_period": True, "ask_min": 0.20, "ask_max": 0.80, "trigger": "rise", "move_pp": 4, "move_window_min": 60,
        "exit": "hold"}),
    "grid_no": ("Fade one exact score", {
        "phase": "pre", "max_min_to_start": 180, "families": ["exact"], "outcome": "No",
        "ask_min": 0.88, "ask_max": 0.97, "trigger": "level", "exit": "hold"}),
    "live_leader": ("Back the clear leader late", {
        "phase": "live", "min_frac": 0.70, "families": ["winner", "totals", "spreads"],
        "no_period": True, "ask_min": 0.80, "ask_max": 0.95, "trigger": "level", "exit": "hold"}),
    "live_momentum": ("Ride a live move", {
        "phase": "live", "families": ["winner", "totals", "spreads"],
        "ask_min": 0.30, "ask_max": 0.80, "trigger": "rise", "move_pp": 10, "move_window_min": 10,
        "exit": "flip", "hold_min": 15}),
    "live_fade": ("Buy a live overreaction", {
        "phase": "live", "families": ["winner", "totals", "spreads"],
        "ask_min": 0.15, "ask_max": 0.70, "trigger": "drop", "move_pp": 12, "move_window_min": 10,
        "exit": "flip", "hold_min": 15}),
    "post_sweep": ("Collect after the final whistle", {
        "phase": "post", "families": ["*"], "ask_min": 0.90, "ask_max": 0.995,
        "trigger": "level", "exit": "hold"}),
}

# One setting moved each way. The review promotes a variant over its base arm
# only when it is clearly better on the same days (price_reader_review).
VARIANTS: dict[str, list[dict]] = {
    "pre_favourite": [{"ask_min": 0.65}, {"ask_min": 0.85}],
    "pre_steam": [{"move_pp": 2}, {"move_pp": 7}],
    "grid_no": [{"ask_min": 0.80}, {"ask_min": 0.94}],
    "live_leader": [{"min_frac": 0.50}, {"min_frac": 0.85}],
    "live_momentum": [{"hold_min": 5}, {"exit": "hold"}],
    "live_fade": [{"hold_min": 5}, {"hold_min": 30}],
    "post_sweep": [{"ask_max": 0.98}, {"ask_min": 0.95}],
}


def variant_key(base: str, change: dict) -> str:
    return base + "@" + ",".join(f"{k}={change[k]}" for k in sorted(change))


def seed_arms() -> list[dict]:
    """Every arm the agent starts with: the base arms active, the variants in
    shadow. Idempotent by key (price_reader_agent.ensure_arms)."""
    out = []
    for key, (label, params) in BASE_ARMS.items():
        out.append({"key": key, "base_key": key, "label": label, "params": dict(params),
                    "mode": "active"})
        for change in VARIANTS.get(key, []):
            desc = ", ".join(f"{k} {v}" for k, v in sorted(change.items()))
            out.append({"key": variant_key(key, change), "base_key": key,
                        "label": f"{label} ({desc})", "params": {**params, **change},
                        "mode": "shadow"})
    return out


# ── reading an event ─────────────────────────────────────────────────────────

def _ts(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _jl(x) -> list:
    if isinstance(x, list):
        return x
    try:
        v = json.loads(x or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def sport_of(event: dict) -> str:
    slugs = [str(t.get("slug") or "").lower() for t in event.get("tags") or [] if isinstance(t, dict)]
    for tag, sport in SPORT_TAGS:
        if tag in slugs:
            return sport
    return "other"


def league_of(event: dict) -> str | None:
    return event.get("seriesSlug") or None


def match_key(event: dict) -> str:
    """One key per game. Polymarket splits a game into several events ("X vs Y",
    "X vs Y - More Markets", "- Exact Score", "- Halftime Result"…) that share a
    `gameId`; without one, the title before " - " and the start stand in. An arm
    takes one position per GAME: thirty lines of one match are one opinion."""
    if event.get("gameId"):
        return f"g:{event['gameId']}"
    title = (event.get("title") or "").split(" - ")[0].strip().lower()
    return f"t:{title}|{event.get('startTime') or ''}"


_WIN_Q = re.compile(r"\bwin\b|\bdraw\b|\bbeat\b", re.I)


def family_of(market: dict) -> tuple[str, bool]:
    """(family, resolves_before_the_end). From `sportsMarketType`, which is exact
    where the question text is not (gamma_api_traps); the question only for the
    markets that carry no type."""
    t = (market.get("sportsMarketType") or "").lower()
    q = (market.get("question") or "").lower()
    period = any(h in t for h in _PERIOD_HINTS) if t else any(
        h in q for h in ("1st half", "2nd half", "first half", "second half", "halftime", "map 1", "game 1"))
    if not t:
        return ("winner" if _WIN_Q.search(q) else "other"), period
    if "corner" in t:
        return "corners", period
    if t in ("soccer_exact_score", "exact_margin") or "exact" in t:
        return "exact", period
    if "moneyline" in t or t in ("soccer_halftime_result", "soccer_second_half_result"):
        return "winner", period
    if "spread" in t or "handicap" in t:
        return "spreads", period
    if "total" in t or "over_under" in t:
        return "totals", period
    if t.startswith(("football_", "cricket_", "points", "rebounds", "assists", "threes")):
        return "props", period
    return "other", period


@dataclass
class Phase:
    phase: str                     # 'pre' | 'live' | 'post' | 'unknown'
    minutes_from_start: float      # negative before the start
    frac: float | None = None      # share of the usual length gone (live only)


def event_phase(event: dict, sport: str, now: datetime) -> Phase:
    """Where the event is, from the clock and Polymarket's own flags. The clock
    may only rule out: a start in the past with no live flag is 'live' for at
    most 1.5x the usual length (4h when unknown), then 'unknown'. 'post' needs
    Polymarket's `ended` flag, never the clock alone."""
    start = _ts(event.get("startTime"))
    if start is None:
        return Phase("unknown", 0.0)
    mins = (now - start).total_seconds() / 60.0
    if event.get("ended"):
        return Phase("post", mins)
    dur = DURATION_MIN.get(sport)
    if mins < 0:
        return Phase("pre" if not event.get("live") else "unknown", mins)
    frac = mins / dur if dur else None
    if event.get("live") or mins <= (dur * 1.5 if dur else 240):
        return Phase("live", mins, frac)
    return Phase("unknown", mins, frac)


@dataclass
class Quote:
    """One outcome token, priced from Gamma's listing. Gamma's bestBid/bestAsk
    are the FIRST outcome's; the second is its complement."""
    event_id: str                   # the GAME (match_key), not Gamma's sub-event
    event_title: str
    event_slug: str | None
    event_start: datetime
    sport: str
    league: str | None
    condition_id: str
    token_id: str
    question: str
    outcome: str
    label: str
    family: str
    period: bool
    market_type: str | None
    bid: float | None
    ask: float | None
    liquidity: float
    one_hour_change: float | None   # Gamma's, on this token's side

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None or self.ask - self.bid > MEMORY_MAX_SPREAD:
            return None
        return (self.bid + self.ask) / 2.0


_SCORE = re.compile(r"(\d+)\s*-\s*(\d+)")


def pick_label(market: dict, outcome: str, family: str = "") -> str:
    """What the bet reads as on the tab: "Everton to win", "Not a draw",
    "Not 2-1 (exact score)", "Over · O/U 2.5", "Sabres · Spread -1.5"."""
    item = (market.get("groupItemTitle") or "").strip()
    q = (market.get("question") or "").strip()
    if ": " in q and not item:
        item = q.split(": ", 1)[1]
    item = (item or q).rstrip("?")
    yes = outcome.lower() == "yes"
    if outcome.lower() in ("yes", "no"):
        if family == "exact":
            sc = _SCORE.search(item)
            if sc:
                score = f"{sc.group(1)}-{sc.group(2)}"
                return f"Exact score {score}" if yes else f"Not {score} (exact score)"
        if "draw" in item.lower():
            return "Draw" if yes else "Not a draw"
        if family == "winner" and re.search(r"\bwin\b", q, re.I):
            return f"{item} to win" if yes else f"{item} not to win"
        return f"{outcome} · {item}"
    if item and item.lower() != outcome.lower() and item != q:
        return f"{outcome} · {item}"
    return outcome


def quotes_of(event: dict) -> list[Quote]:
    """Every open token of every market of one event, priced from the listing.
    A market not accepting orders, closed, or with no two tokens is skipped."""
    start = _ts(event.get("startTime"))
    if start is None:
        return []
    sport = sport_of(event)
    out = []
    for m in event.get("markets") or []:
        if m.get("closed") or m.get("acceptingOrders") is False or not m.get("conditionId"):
            continue
        toks, outs = _jl(m.get("clobTokenIds")), _jl(m.get("outcomes"))
        if len(toks) != 2 or len(outs) != 2:
            continue
        fam, period = family_of(m)
        bb, ba = m.get("bestBid"), m.get("bestAsk")
        bb = float(bb) if bb not in (None, "") else None
        ba = float(ba) if ba not in (None, "") else None
        # An empty side is quoted as 0 / 1 by Gamma: not a price.
        if bb is not None and bb <= 0:
            bb = None
        if ba is not None and ba >= 1:
            ba = None
        ch = m.get("oneHourPriceChange")
        ch = float(ch) if ch not in (None, "") else None
        sides = [(toks[0], outs[0], bb, ba, ch),
                 (toks[1], outs[1], (1 - ba) if ba is not None else None,
                  (1 - bb) if bb is not None else None, (-ch) if ch is not None else None)]
        for tok, outcome, bid, ask, change in sides:
            out.append(Quote(
                event_id=match_key(event), event_title=event.get("title") or "",
                event_slug=event.get("slug"), event_start=start, sport=sport,
                league=league_of(event), condition_id=m["conditionId"], token_id=str(tok),
                question=m.get("question") or "", outcome=str(outcome),
                label=pick_label(m, str(outcome), fam), family=fam, period=period,
                market_type=m.get("sportsMarketType"), bid=bid, ask=ask,
                liquidity=float(m.get("liquidityNum") or 0.0), one_hour_change=change))
    return out


# ── its memory of prices ─────────────────────────────────────────────────────

@dataclass
class PriceMemory:
    """A mid per token per cycle, for the last 90 minutes. In memory only: after
    a restart the move arms wait until the window has filled again."""
    keep_min: int = 90
    series: dict = field(default_factory=dict)

    def observe(self, quotes: list[Quote], now: datetime) -> None:
        cutoff = now - timedelta(minutes=self.keep_min)
        for q in quotes:
            m = q.mid
            if m is None:
                continue
            d = self.series.setdefault(q.token_id, deque())
            d.append((now, m))
        for tok in list(self.series):
            d = self.series[tok]
            while d and d[0][0] < cutoff:
                d.popleft()
            if not d:
                del self.series[tok]

    def move(self, token_id: str, now: datetime, window_min: float) -> float | None:
        """Latest mid minus the mid `window_min` ago (the point nearest to then,
        within a quarter of the window). None when the memory does not reach back."""
        d = self.series.get(token_id)
        if not d or len(d) < 2:
            return None
        target = now - timedelta(minutes=window_min)
        tol = timedelta(minutes=max(2.0, window_min / 4.0))
        best = min(d, key=lambda p: abs((p[0] - target).total_seconds()))
        if abs(best[0] - target) > tol:
            return None
        return d[-1][1] - best[1]


# ── which token each arm would buy ───────────────────────────────────────────

@dataclass
class Candidate:
    quote: Quote
    phase: Phase
    move_pp: float | None
    why: str


def _phase_ok(p: dict, ph: Phase) -> bool:
    if ph.phase != p["phase"]:
        return False
    if ph.phase == "pre":
        return -ph.minutes_from_start <= p.get("max_min_to_start", PRE_HORIZON_MIN)
    if ph.phase == "live" and p.get("min_frac") is not None:
        return ph.frac is not None and ph.frac >= p["min_frac"]
    return True


def _family_ok(p: dict, q: Quote) -> bool:
    fams = p.get("families") or ["*"]
    if "*" not in fams and q.family not in fams:
        return False
    if p.get("no_period") and q.period:
        return False
    if p.get("outcome") and q.outcome.lower() != str(p["outcome"]).lower():
        return False
    return True


def arm_candidates(params: dict, events: list[dict], memory: PriceMemory, now: datetime,
                   quotes_by_event: dict | None = None) -> list[Candidate]:
    """At most ONE candidate per game for this arm: the token that fits the
    rule on Gamma's listing price, the most liquid market first (or, for the
    move arms, the biggest move), across all of the game's sub-events. Nothing
    here touches the network; the CLOB book confirms the price before anything
    is written (entry_verdict)."""
    per_game: dict[str, tuple] = {}
    for ev in events:
        qs = quotes_by_event.get(str(ev.get("id"))) if quotes_by_event is not None else quotes_of(ev)
        if not qs:
            continue
        ph = event_phase(ev, qs[0].sport, now)
        if not _phase_ok(params, ph):
            continue
        best: tuple | None = None
        for q in qs:
            if q.ask is None or q.bid is None or q.ask - q.bid > MAX_GAMMA_SPREAD:
                continue
            if not (params["ask_min"] <= q.ask <= params["ask_max"]):
                continue
            if not _family_ok(params, q):
                continue
            mv = None
            trig = params.get("trigger", "level")
            if trig in ("rise", "drop"):
                w = params.get("move_window_min", 10)
                mv = memory.move(q.token_id, now, w)
                if mv is None and w >= 60 and q.one_hour_change is not None:
                    mv = q.one_hour_change                 # Gamma's own, until the memory reaches back
                if mv is None:
                    continue
                need = params.get("move_pp", 10) / 100.0
                if (trig == "rise" and mv < need) or (trig == "drop" and mv > -need):
                    continue
                score = abs(mv)
            else:
                score = q.liquidity - (q.ask - q.bid) * 1e-6
            if best is None or score > best[0]:
                best = (score, q, mv)
        if best:
            prev = per_game.get(best[1].event_id)
            if prev is None or best[0] > prev[0]:
                per_game[best[1].event_id] = (best[0], best[1], best[2], ph)
    out = []
    for _, q, mv, ph in per_game.values():
        why = f"{q.label} at {q.ask:.3f}"
        if mv is not None:
            why += f", moved {mv * 100:+.1f}pp in {params.get('move_window_min')} min"
        out.append(Candidate(q, ph, mv, why))
    return out


# ── the book, fees, and the verdict ──────────────────────────────────────────

def parse_book(book: dict) -> dict:
    """A CLOB /book → best bid / ask and top-5 notional per side. A missing
    side is None."""
    def px(x):
        try:
            return float(x["price"])
        except (KeyError, TypeError, ValueError):
            return None

    def notional(side):
        return sum((px(x) or 0.0) * float(x.get("size") or 0) for x in side[:5])

    bids = sorted([x for x in book.get("bids") or [] if px(x) is not None], key=lambda x: -px(x))
    asks = sorted([x for x in book.get("asks") or [] if px(x) is not None], key=px)
    return {"best_bid": px(bids[0]) if bids else None, "best_ask": px(asks[0]) if asks else None,
            "bid_depth_usd": notional(bids) if bids else 0.0,
            "ask_depth_usd": notional(asks) if asks else 0.0}


def cost_per_share(ask: float, fee: float = FEE_RATE) -> float:
    return ask * (1.0 + fee * (1.0 - ask))


def sale_per_share(bid: float, fee: float = FEE_RATE) -> float:
    return bid * (1.0 - fee * (1.0 - bid))


def entry_verdict(params: dict, book: dict | None) -> str | None:
    """None when the book is one a bettor could actually take at the arm's
    price, else why not."""
    if book is None:
        return "no book"
    ask, bid = book.get("best_ask"), book.get("best_bid")
    if ask is None or bid is None:
        return "one-sided book"
    if ask - bid > MAX_SPREAD + 1e-9:
        return f"spread {(ask - bid) * 100:.1f}pp"
    if (book.get("ask_depth_usd") or 0) < MIN_ASK_DEPTH_USD:
        return f"ask depth ${book.get('ask_depth_usd') or 0:.0f}"
    if not (params["ask_min"] <= ask <= params["ask_max"]):
        return f"ask {ask:.3f} outside {params['ask_min']}-{params['ask_max']}"
    return None


def shares_for(stake: float, ask: float, fee: float = FEE_RATE) -> float:
    return stake / cost_per_share(ask, fee)


def net_odds(ask: float, fee: float = FEE_RATE) -> float:
    """Decimal odds of a 1u bet at this ask, net of the taker fee."""
    return 1.0 / cost_per_share(ask, fee)


def exit_due(params: dict, now: datetime) -> datetime | None:
    if params.get("exit") == "flip":
        return now + timedelta(minutes=float(params.get("hold_min", 15)))
    return None


def sale_verdict(book: dict | None) -> str | None:
    if book is None:
        return "no book"
    bid, ask = book.get("best_bid"), book.get("best_ask")
    if bid is None or bid <= 0:
        return "no bid"
    if ask is not None and ask - bid > EXIT_MAX_SPREAD + 1e-9:
        return f"spread {(ask - bid) * 100:.1f}pp"
    return None


def settle_payout(shares: float, won: bool) -> float:
    return shares if won else 0.0


# ── statistics the review and the report share ───────────────────────────────

def record(pls: list[float], stakes: list[float]) -> dict:
    """n, P&L, yield and a 90% interval on the yield (normal on per-bet
    returns). With fewer than two bets there is no interval."""
    n = len(pls)
    staked = sum(stakes)
    pl = sum(pls)
    out = {"n": n, "staked": round(staked, 4), "pl": round(pl, 4),
           "wins": sum(1 for x in pls if x > 0),
           "yield": (pl / staked) if staked else None, "lo90": None, "hi90": None, "se": None}
    if n >= 2 and staked:
        rs = [p / s for p, s in zip(pls, stakes) if s]
        mean = sum(rs) / len(rs)
        var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
        se = math.sqrt(var / len(rs))
        out.update(se=se, lo90=mean - 1.645 * se, hi90=mean + 1.645 * se)
    return out


def prob_positive(rec: dict) -> float | None:
    """P(edge > 0) under a normal reading of the record, None with no interval."""
    if rec.get("se") is None or rec.get("yield") is None:
        return None
    if rec["se"] == 0:
        return 1.0 if rec["yield"] > 0 else 0.0
    z = rec["yield"] / rec["se"]
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
