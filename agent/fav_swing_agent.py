#!/usr/bin/env python3
"""
Favourite Swing — in on pressure, out after a goal (paper, db/061, H-FAV-SWING).

THE RULE (the user's, 2026-09-29)
---------------------------------
  * A team was 1.30-1.50 to win at kick-off. The price is Polymarket's own
    "Will <team> win?" Yes, RAW (the odds a bettor saw), the last read before
    kick-off.
  * The match is LEVEL — 0-0, 1-1, … — and that team is pressing: its own
    danger index >= 19 and >= 20 above the opponent's (s18's thresholds).
  * Pressure is read over the WHOLE MATCH so far, like Sofascore's match view:
    every shot, corner and the possession since kick-off, as a 15-minute rate
    (ht_pressure_agent.opening_pressure). From obs_version 2 (the user's call,
    2026-09-29) — v1 read a rolling 15-minute window and waited until 15'.
    Entry is allowed from the first poll that carries stats: the agent goes in
    the moment the favourite is pressing, not at a fixed minute.
  * Buy "Will <team> win?" Yes at the CLOB ask, 1u, paper. Once per fixture.
  * ANY goal starts a STABILISE_S clock. Every further score change restarts
    it; a score back at the entry score cancels it (a goal that did not stand —
    the sweep audit saw phantom goals last up to ~4 minutes). When it runs out,
    sell at the CLOB bid.
  * No goal: held to the end and settled by the market's winner.

The P&L is net of Polymarket's taker fee on the way in AND on the way out:
shares = stake / (ask·(1 + f·(1−ask))), a sale pays bid·(1 − f·(1−bid)) a share.

Runs inside pressure_agent.py, on the same api-football poll as the other three
arms. Positions live in fav_swing_positions, so a restart mid-match keeps them.

INSTRUMENTATION (db/078, 2026-10-20 — the rule above is unchanged)
------------------------------------------------------------------
  * fav_swing_ticks: the held token's book on every poll while a position is
    open, so time stops and faster exits can be replayed on our own positions.
  * arm = 'control': the same rule WITHOUT the pressure gate, as a shadow (no
    paper trade). It answers whether the gate beats buying every level band
    favourite. Its pressure reading is still recorded when there are stats.

    python fav_swing_agent.py --report
    python pressure_agent.py --once --dry-run      # drives this arm too
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fav_pressure_agent import _WIN_RE, _yes_price, _yes_token, resolve_side   # noqa: E402
from ht_pressure_agent import opening_pressure                                  # noqa: E402
from late_goals_observer import _fetch_book                                     # noqa: E402
from live_tracker import PressureSignals                                        # noqa: E402
from pressure_agent import match_pm_fixture                                     # noqa: E402

log = logging.getLogger("fav_swing")

STRATEGY_NAME = "Favourite Swing — pressure in, goal out"
OBS_VERSION = 2                  # v2: whole-match pressure, entry from the first stats (09-29)

KO_ODDS_MIN, KO_ODDS_MAX = 1.30, 1.50
ENTRY_MIN_MINUTE = 5             # "the moment it presses" — but one shot at 2' is a 7x rate, not pressure
ENTRY_MAX_MINUTE = 75
MIN_FAV_PRESSURE = 19.0          # s18's gates, unchanged
MIN_DOMINANCE = 20.0

MIN_ASK, MAX_ASK = 0.05, 0.95
MAX_SPREAD = 0.03                # the full-match win book is the deep one
MIN_ASK_DEPTH_USD = 100.0
FEE_RATE = 0.05
STAKE_UNITS = 1.0

STABILISE_S = 300                # 5 minutes after the last score change
EXIT_MAX_SPREAD = 0.06           # a book this wide right after a goal is not a price yet…
EXIT_FORCE_S = 900               # …but 15 minutes after the goal, sell at whatever bid there is

ARMS = ("pressure", "control")   # db/078: control = the same rule without the pressure gate (shadow)
CONTROL_RECHECK_S = 120          # a control-only fixture whose book failed is re-read at most this often
TICK_MAX_AGE_S = 3 * 3600        # stop ticking a position this long after entry (the match is over)

CLOB = "https://clob.polymarket.com"
_SPLIT = re.compile(r"\s+-\s+")


def cost_per_share(ask: float, fee: float = FEE_RATE) -> float:
    return ask * (1.0 + fee * (1.0 - ask))


def sale_per_share(bid: float, fee: float = FEE_RATE) -> float:
    return bid * (1.0 - fee * (1.0 - bid))


def parse_book(book: dict) -> dict:
    """A CLOB /book response -> best bid/ask and top-5 notional per side. Unlike
    late_goals_observer._fetch_book, a one-sided book is kept (the empty side is
    None): a book that pulls after a goal is what the ticks are there to see."""
    def px(x):
        try:
            return float(x["price"])
        except (KeyError, TypeError, ValueError):
            return None

    def notional(side):
        return sum((px(x) or 0.0) * float(x.get("size") or 0) for x in side[:5])

    bids = sorted([x for x in book.get("bids") or [] if px(x) is not None], key=lambda x: -px(x))
    asks = sorted([x for x in book.get("asks") or [] if px(x) is not None], key=px)
    return {"best_bid": px(bids[0]) if bids else None,
            "best_ask": px(asks[0]) if asks else None,
            "bid_depth_usd": notional(bids) if bids else None,
            "ask_depth_usd": notional(asks) if asks else None}


def read_book(token_id: str) -> dict | None:
    """The token's book, one-sided included. None only when it could not be read."""
    try:
        resp = requests.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=10)
        if resp.status_code != 200:
            return None
        return parse_book(resp.json())
    except Exception:                                   # noqa: BLE001
        return None


def two_sided(book: dict | None) -> dict | None:
    """What _fetch_book would have returned: the book only if both sides quote.
    The exit gate reads this, so recording ticks does not change when we sell."""
    if book and book.get("best_bid") is not None and book.get("best_ask") is not None:
        return book
    return None


# ── the kick-off price ───────────────────────────────────────────────────────

def win_legs(fixture: dict) -> dict[str, dict]:
    """{PM team name: {price, token_id, condition_id}} for the two "Will X win?"
    markets. Sides are NOT resolved here: before kick-off the only names are
    Polymarket's own, and its title order is not trusted."""
    out: dict[str, dict] = {}
    for m in fixture.get("markets") or []:
        if m.get("closed"):
            continue
        q = (m.get("question") or "").strip()
        rem = q.split(": ", 1)[1] if ": " in q else q
        w = _WIN_RE.match(rem) or _WIN_RE.match(q)
        if not w:
            continue
        tok = _yes_token(m)
        if tok:
            out[w.group("team").strip()] = {"price": _yes_price(m), "token_id": tok,
                                            "condition_id": m.get("conditionId"),
                                            "question": q}
    return out if len(out) == 2 else {}


@dataclass
class SwingState:
    """PM title -> the win legs as last read BEFORE kick-off. Refreshed every
    universe refresh until the ball is kicked, so 'at kick-off' means the last
    pre-KO read (≤ REFRESH_MARKETS_S old), not the first time the board appeared.
    `control_checked` throttles the book read for fixtures only the control arm
    still wants, so a level match with a wide book is not re-read every poll."""
    ko: dict[str, dict] = field(default_factory=dict)
    control_checked: dict = field(default_factory=dict)

    def capture(self, pm_fixtures: list[dict]) -> None:
        now = datetime.now(timezone.utc)
        for fx in pm_fixtures:
            title, kickoff = fx.get("title"), fx.get("kickoff")
            if not title or not kickoff or now >= kickoff:
                continue
            legs = win_legs(fx)
            if legs:
                self.ko[title] = {"legs": legs, "at": now, "kickoff": kickoff}
        for t in [t for t, v in self.ko.items() if (now - v["kickoff"]).total_seconds() > 4 * 3600]:
            del self.ko[t]


def favourite_at_ko(capture: dict, home: str, away: str) -> dict | None:
    """The team priced 1.30-1.50 at kick-off, resolved to api-football's side.
    Both names must resolve to opposite sides, or nothing — a side error inverts
    this bet."""
    by_side = {}
    for name, leg in capture["legs"].items():
        side = resolve_side(name, home, away)
        if side is None or side in by_side:
            return None
        by_side[side] = leg
    if set(by_side) != {"home", "away"}:
        return None
    for side, leg in by_side.items():
        p = leg["price"]
        if p and KO_ODDS_MIN <= 1.0 / p <= KO_ODDS_MAX:
            return {"side": side, "team": home if side == "home" else away,
                    "ko_price": p, "ko_at": capture["at"], **leg}
    return None


# ── entry ────────────────────────────────────────────────────────────────────

def is_pressing(fav_p: float | None, dog_p: float | None) -> bool:
    return fav_p is not None and dog_p is not None and \
        fav_p >= MIN_FAV_PRESSURE and fav_p - dog_p >= MIN_DOMINANCE


def entry_candidates(signals: dict[int, PressureSignals], pm_fixtures: list[dict],
                     state: SwingState, held: dict[str, set]) -> list[dict]:
    """Every (fixture, arm) that clears the rule this poll. `held[arm]` is the set
    of fixture keys that arm already entered, so each arm buys a fixture once.
    The two arms share every gate but one: 'control' does not need the favourite
    to be pressing."""
    state.capture(pm_fixtures)
    now_t = datetime.now(timezone.utc)
    out = []
    for sig in signals.values():
        if not (ENTRY_MIN_MINUTE <= sig.minute <= ENTRY_MAX_MINUTE):
            continue
        if sig.home_goals != sig.away_goals:
            continue
        want = [a for a in ARMS
                if sig.fixture_id not in held.get(a, ()) and (sig.home, sig.away) not in held.get(a, ())]
        if not want:
            continue
        fx = match_pm_fixture(sig, pm_fixtures)
        if fx is None:
            continue
        cap = state.ko.get(fx["title"])
        fav = favourite_at_ko(cap, sig.home, sig.away) if cap else None
        if fav is None:
            continue
        fav_p = dog_p = None
        if sig.has_stats:
            _, home_p, away_p = opening_pressure(sig)
            fav_p = home_p if fav["side"] == "home" else away_p
            dog_p = away_p if fav["side"] == "home" else home_p
        arms = [a for a in want if a == "control" or is_pressing(fav_p, dog_p)]
        if not arms:
            continue
        key = (sig.home, sig.away)
        if arms == ["control"]:
            last = state.control_checked.get(key)
            if last and (now_t - last).total_seconds() < CONTROL_RECHECK_S:
                continue
            state.control_checked[key] = now_t
        book = _fetch_book({"token_id": fav["token_id"]})
        why = entry_book_verdict(book)
        if why:
            if "pressure" in arms:
                log.info(f"[swing] {fav['team']} {sig.home_goals}-{sig.away_goals} {sig.minute}': skip — {why}")
            continue
        for arm in arms:
            out.append({"arm": arm, "sig": sig, "fx": fx, "fav": fav, "book": book,
                        "fav_pressure": fav_p, "dog_pressure": dog_p,
                        "source": "match" if fav_p is not None else None})
    for k in [k for k, t in state.control_checked.items() if (now_t - t).total_seconds() > 4 * 3600]:
        del state.control_checked[k]
    return out


def entry_book_verdict(book: dict | None) -> str | None:
    if not book or book.get("best_bid") is None or book.get("best_ask") is None:
        return "no two-sided book"
    ask, bid = book["best_ask"], book["best_bid"]
    if not MIN_ASK <= ask <= MAX_ASK:
        return f"ask {ask:.3f} out of range"
    if ask - bid > MAX_SPREAD:
        return f"spread {ask - bid:.3f} > {MAX_SPREAD}"
    if (book.get("ask_depth_usd") or 0) < MIN_ASK_DEPTH_USD:
        return f"ask depth ${book.get('ask_depth_usd') or 0:.0f}"
    return None


# ── the goal clock (pure: the tests drive it) ────────────────────────────────

def step(pos: dict, home_goals: int, away_goals: int, minute: int, now: datetime) -> str:
    """Advance one open position by one poll. Mutates `pos`; returns the action:
    'hold', 'goal', 'reversed', 'regoal' or 'sell_due'."""
    entry = (pos["entry_home_goals"], pos["entry_away_goals"])
    score = (home_goals, away_goals)
    pos["last_seen_at"], pos["last_minute"] = now, minute
    if pos["status"] == "open":
        if score == entry:
            return "hold"
        pos.update(status="goal_pending", goal_seen_at=now, goal_minute=minute,
                   goal_home_goals=home_goals, goal_away_goals=away_goals)
        return "goal"
    # goal_pending
    if score == entry:
        pos.update(status="open", goal_seen_at=None, goal_minute=None,
                   goal_home_goals=None, goal_away_goals=None,
                   goals_reversed=pos.get("goals_reversed", 0) + 1)
        return "reversed"
    if score != (pos["goal_home_goals"], pos["goal_away_goals"]):
        pos.update(goal_seen_at=now, goal_minute=minute,
                   goal_home_goals=home_goals, goal_away_goals=away_goals)
        return "regoal"
    return "sell_due" if (now - pos["goal_seen_at"]).total_seconds() >= STABILISE_S else "hold"


def exit_book_ok(book: dict | None, waited_s: float) -> bool:
    if not book or book.get("best_bid") is None or book["best_bid"] <= 0:
        return False
    if book.get("best_ask") is None:
        return waited_s >= EXIT_FORCE_S
    return (book["best_ask"] - book["best_bid"]) <= EXIT_MAX_SPREAD or waited_s >= EXIT_FORCE_S


# ── the database ─────────────────────────────────────────────────────────────

def strategy_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
        got = cur.fetchone()
    if not got:
        raise SystemExit(f"strategy '{STRATEGY_NAME}' missing — apply db/061")
    return got[0]


def open_positions(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM fav_swing_positions
                        WHERE status IN ('open', 'goal_pending')
                          AND entered_at > now() - interval '12 hours'""")
        return [dict(r) for r in cur.fetchall()]


def held_keys(conn) -> dict[str, set]:
    """Per arm, the fixtures entered in the last 6 hours, by id AND by team
    names — an api-football → ESPN fallback re-namespaces the id mid-match."""
    with conn.cursor() as cur:
        cur.execute("""SELECT arm, fixture_id, home, away FROM fav_swing_positions
                        WHERE entered_at > now() - interval '6 hours'""")
        out: dict[str, set] = {a: set() for a in ARMS}
        for arm, fid, h, a in cur.fetchall():
            out.setdefault(arm, set()).update({fid, (h, a)})
        return out


def enter(conn, sid: int, c: dict) -> tuple[int, int | None]:
    """Open a position. The pressure arm books a paper trade; the control arm is
    a shadow and books none. Returns (position id, paper trade id or None)."""
    sig, fav, book, arm = c["sig"], c["fav"], c["book"], c["arm"]
    ask = float(book["best_ask"])
    shares = STAKE_UNITS / cost_per_share(ask)
    title = c["fx"].get("title") or f"{sig.home} vs {sig.away}"
    fav_p, dog_p = c["fav_pressure"], c["dog_pressure"]
    dominance = fav_p - dog_p if fav_p is not None and dog_p is not None else None
    with conn.cursor() as cur:
        ptid = None
        if arm == "pressure":
            reasoning = (
                f"{sig.home} {sig.home_goals}-{sig.away_goals} {sig.away} {sig.minute}' — "
                f"{fav['team']} to win at {ask:.3f} ({1 / ask:.2f}). It was "
                f"{1 / fav['ko_price']:.2f} at kick-off, the score is level, and it is pressing: "
                f"{fav_p:.0f} vs {dog_p:.0f} ({c['source']}). "
                f"EXIT: sold at the bid {STABILISE_S // 60} minutes after any goal; held to the "
                f"end if none. 1u, PAPER."
            )
            cur.execute("""INSERT INTO paper_trades (strategy_id, outcome, entry_price, entry_odds,
                                                     stake_units, reasoning, confidence, pm_token_id, pm_live)
                           VALUES (%s,%s,%s,%s,%s,%s,'paper',%s,false) RETURNING id""",
                        (sid, f"{fav['team']} to win (swing) — {title}", ask, 1 / ask, STAKE_UNITS,
                         reasoning, fav["token_id"]))
            ptid = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO fav_swing_positions
              (obs_version, arm, paper_trade_id, fixture_id, league, home, away, event_title,
               fav_side, fav_team, ko_price, ko_price_at, token_id, condition_id,
               entry_minute, entry_home_goals, entry_away_goals, entry_ask, entry_bid,
               entry_ask_depth, fav_pressure, dog_pressure, dominance, pressure_source,
               stats_source, shares, fee_rate, last_seen_at, last_minute)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
            RETURNING id""",
                    (OBS_VERSION, arm, ptid, sig.fixture_id, sig.league, sig.home, sig.away, title,
                     fav["side"], fav["team"], fav["ko_price"], fav["ko_at"], fav["token_id"],
                     fav.get("condition_id"), sig.minute, sig.home_goals, sig.away_goals, ask,
                     book["best_bid"], book.get("ask_depth_usd"), fav_p, dog_p,
                     dominance, c["source"], sig.stats_source,
                     shares, FEE_RATE, sig.minute))
        pos_id = cur.fetchone()[0]
    conn.commit()
    return pos_id, ptid


def record_tick(conn, pos: dict, sig: PressureSignals | None, book: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO fav_swing_ticks
                         (position_id, minute, home_goals, away_goals, status,
                          best_bid, best_ask, bid_depth_usd, ask_depth_usd)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pos["id"], sig.minute if sig else None, sig.home_goals if sig else None,
                     sig.away_goals if sig else None, pos["status"], book["best_bid"],
                     book["best_ask"], book["bid_depth_usd"], book["ask_depth_usd"]))
    conn.commit()


_POS_COLS = ("status", "goal_seen_at", "goal_minute", "goal_home_goals", "goal_away_goals",
             "goals_reversed", "last_seen_at", "last_minute")


def save(conn, pos: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(f"UPDATE fav_swing_positions SET {', '.join(f'{c} = %s' for c in _POS_COLS)}, "
                    f"updated_at = now() WHERE id = %s", [pos[c] for c in _POS_COLS] + [pos["id"]])
    conn.commit()


def close(conn, pos: dict, *, reason: str, payout: float, minute=None, bid=None, ask=None) -> None:
    stake = STAKE_UNITS
    with conn.cursor() as cur:
        cur.execute("""UPDATE fav_swing_positions
                          SET status = %s, exit_at = now(), exit_minute = %s, exit_bid = %s,
                              exit_ask = %s, exit_reason = %s, payout_units = %s, updated_at = now()
                        WHERE id = %s""",
                    ("sold" if reason == "sold_after_goal" else "settled", minute, bid, ask,
                     reason, payout, pos["id"]))
        cur.execute("""UPDATE paper_trades SET result = %s, payout_units = %s, resolved_at = now(),
                              closing_price = %s
                        WHERE id = %s AND result IS NULL""",
                    ("won" if payout > stake else "lost", payout, bid, pos["paper_trade_id"]))
    conn.commit()


# ── one cycle ────────────────────────────────────────────────────────────────

def _find(signals: dict, pos: dict) -> PressureSignals | None:
    s = signals.get(pos["fixture_id"])
    if s is not None:
        return s
    for s in signals.values():
        if s.home == pos["home"] and s.away == pos["away"]:
            return s
    return None


def manage(conn, signals: dict[int, PressureSignals], dry_run: bool = False) -> dict:
    """Advance every open position; sell the ones whose clock has run out. Every
    open position's book is read and recorded each poll (db/078), including polls
    where the fixture is missing from the feed — the exit decision itself reads
    only a two-sided book, exactly as before."""
    counts = {"open": 0, "goal": 0, "sold": 0, "ticks": 0}
    if conn is None:
        return counts
    now = datetime.now(timezone.utc)
    for pos in open_positions(conn):
        sig = _find(signals, pos)
        fresh = (now - pos["entered_at"]).total_seconds() <= TICK_MAX_AGE_S
        book = read_book(pos["token_id"]) if fresh else None

        def tick(status: str) -> None:
            if book is not None and not dry_run:
                record_tick(conn, {**pos, "status": status}, sig, book)
                counts["ticks"] += 1

        if sig is None:
            tick(pos["status"])
            continue                          # not live this poll (half time gap, FT, feed) — settle() finishes it
        act = step(pos, sig.home_goals, sig.away_goals, sig.minute, now)
        if act in ("goal", "regoal", "reversed"):
            log.info(f"[swing:{pos['arm']}] {pos['fav_team']} {sig.home_goals}-{sig.away_goals} "
                     f"{sig.minute}': {act} (entered {pos['entry_home_goals']}-{pos['entry_away_goals']})")
        if act == "sell_due":
            waited = (now - pos["goal_seen_at"]).total_seconds()
            exit_book = two_sided(book if fresh else read_book(pos["token_id"]))
            if exit_book_ok(exit_book, waited):
                payout = float(pos["shares"]) * sale_per_share(float(exit_book["best_bid"]), float(pos["fee_rate"]))
                tick("sold")
                if not dry_run:
                    close(conn, pos, reason="sold_after_goal", payout=payout, minute=sig.minute,
                          bid=exit_book["best_bid"], ask=exit_book.get("best_ask"))
                counts["sold"] += 1
                log.info(f"[swing:{pos['arm']}] SELL {pos['fav_team']} {sig.home_goals}-{sig.away_goals} "
                         f"{sig.minute}' at bid {exit_book['best_bid']:.3f} (in at {float(pos['entry_ask']):.3f}) "
                         f"→ {payout - STAKE_UNITS:+.2f}u  pt#{pos['paper_trade_id']}")
                continue
            quote = "no book" if not exit_book else f"{exit_book.get('best_bid')}/{exit_book.get('best_ask')}"
            log.info(f"[swing:{pos['arm']}] {pos['fav_team']}: sale due, book not clean yet ({quote})")
        tick(pos["status"])
        counts["goal" if pos["status"] == "goal_pending" else "open"] += 1
        if not dry_run:
            save(conn, pos)
    return counts


def cycle(conn, sid: int | None, signals: dict, pm_fixtures: list[dict],
          state: SwingState, dry_run: bool = False) -> dict:
    counts = manage(conn, signals, dry_run)
    held = held_keys(conn) if conn is not None else {a: set() for a in ARMS}
    entered = {a: 0 for a in ARMS}
    for c in entry_candidates(signals, pm_fixtures, state, held):
        sig, arm = c["sig"], c["arm"]
        press = (f"press={c['fav_pressure']:.0f} dom={c['fav_pressure'] - c['dog_pressure']:+.0f}"
                 if c["fav_pressure"] is not None else "press=n/a")
        if dry_run or conn is None:
            log.info(f"[swing:{arm}] DRY would buy {c['fav']['team']} at {c['book']['best_ask']:.3f} "
                     f"{sig.home_goals}-{sig.away_goals} {sig.minute}' {press}")
            held.setdefault(arm, set()).update({sig.fixture_id, (sig.home, sig.away)})
            continue
        pos_id, ptid = enter(conn, sid, c)
        held.setdefault(arm, set()).update({sig.fixture_id, (sig.home, sig.away)})
        entered[arm] += 1
        tag = "SWING" if arm == "pressure" else "SWING-CTL"
        log.info(f"  ENTER  [{tag}] {c['fav']['team'][:20]:20} {sig.home_goals}-{sig.away_goals} "
                 f"{sig.minute}' ask={c['book']['best_ask']:.3f} ({1 / c['book']['best_ask']:.2f}) "
                 f"ko={1 / c['fav']['ko_price']:.2f} {press}  pos#{pos_id}"
                 + (f" pt#{ptid}" if ptid else " (shadow)"))
    counts["entered"] = entered["pressure"]
    counts["control_entered"] = entered["control"]
    return counts


# ── settlement: positions still held when the market resolved ────────────────

def _winner(condition_id: str) -> dict[str, bool] | None:
    try:
        d = requests.get(f"{CLOB}/markets/{condition_id}", timeout=10).json()
    except Exception:                                   # noqa: BLE001
        return None
    toks = d.get("tokens") or []
    if not toks or not any(t.get("winner") for t in toks):
        return None
    return {str(t.get("token_id")): bool(t.get("winner")) for t in toks}


def settle(conn) -> int:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM fav_swing_positions
                        WHERE status IN ('open', 'goal_pending')
                          AND entered_at < now() - interval '2 hours'""")
        pending = [dict(r) for r in cur.fetchall()]
    winners = {p["condition_id"]: _winner(p["condition_id"]) for p in pending if p["condition_id"]}
    n = 0
    for p in pending:
        w = winners.get(p["condition_id"])
        if not w or p["token_id"] not in w:
            continue
        won = w[p["token_id"]]
        close(conn, p, reason="held_won" if won else "held_lost",
              payout=float(p["shares"]) if won else 0.0)
        n += 1
    return n


def report(conn) -> None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT arm, exit_reason, count(*) n, sum(payout_units - 1) pl,
                              avg(entry_ask) ask, avg(exit_bid) bid, avg(entry_minute) m
                         FROM fav_swing_positions GROUP BY 1, 2 ORDER BY 1 DESC, 2 NULLS FIRST""")
        rows = cur.fetchall()
        cur.execute("""SELECT p.arm, count(DISTINCT t.position_id) pos, count(*) ticks
                         FROM fav_swing_ticks t JOIN fav_swing_positions p ON p.id = t.position_id
                        GROUP BY 1 ORDER BY 1 DESC""")
        ticks = cur.fetchall()
    print("\n=== Favourite Swing — positions (control = no pressure gate, shadow) ===")
    for r in rows:
        print(f"  {r['arm']:8} {r['exit_reason'] or 'open':24} n={r['n']:3d}  P&L={float(r['pl'] or 0):+.2f}u  "
              f"entry ask={float(r['ask'] or 0):.3f}  exit bid={float(r['bid'] or 0):.3f}  "
              f"entry minute={float(r['m'] or 0):.0f}")
    for t in ticks:
        print(f"  ticks    {t['arm']:8} {t['ticks']:6d} book reads over {t['pos']} positions")


def main() -> None:
    import db_txn
    ap = argparse.ArgumentParser(description="Favourite Swing (paper). The live loop is pressure_agent.py.")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S", force=True)
    conn = db_txn.connect(os.getenv("DATABASE_URL"))
    if args.settle:
        log.info(f"settled {settle(conn)} held positions")
    report(conn)


if __name__ == "__main__":
    main()
