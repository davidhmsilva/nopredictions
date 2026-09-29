#!/usr/bin/env python3
"""
lab_inplay_runner.py — paper-trade the LIVE rules users build in the Lab.

A live rule (strategies.source='lab', spec.kind='inplay', db/064) is a trigger
on a match in progress plus an exit. The field meanings are defined once, in
site/app/lib/inplaySpec.ts — ⚠️ change a field there and change it here.

    entry   the FIRST poll where every condition holds: minute window, score
            state, pressure, the team's kick-off odds band, the price paid,
            league — then 1u at the CLOB ask, once per match per rule.
    exit    hold        → settled by the market's winner
            after_goal  → a qualifying goal (any / the team's / the
                          opponent's) starts a clock; a further score change
                          restarts it, the score going back cancels it (a
                          goal that did not stand); when it runs out, sell at
                          the bid. With no such goal, held to the end.
            at_minute   → sell at the bid at that minute.

The P&L is net of the taker fee both ways (fav_swing_agent.cost_per_share /
sale_per_share). Positions live in lab_inplay_positions.

Runs inside pressure_agent.py on the same api-football poll as the operator's
arms — a second process would double the live calls, and the quota is what
has broken this pipeline before. Settlement of held positions rides
pressure_agent --settle.

    python lab_inplay_runner.py --report
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fav_pressure_agent import _yes_token, resolve_side          # noqa: E402
from fav_swing_agent import (                                     # noqa: E402
    SwingState,
    _winner,
    cost_per_share,
    sale_per_share,
    win_legs,
)
from ht_pressure_agent import current_pressure, opening_pressure  # noqa: E402
from late_goals_observer import _fetch_book, ladder_of            # noqa: E402
from live_tracker import PressureSignals                          # noqa: E402
from pressure_agent import match_pm_fixture                       # noqa: E402

log = logging.getLogger("lab_inplay")

# ⚠️ Mirrored from site/app/lib/inplaySpec.ts (PRESSURE_GATES).
PRESSURE_GATES = {
    "pressing": {"own": 19.0, "gap": 20.0, "match": 19.0},
    "dominating": {"own": 29.0, "gap": 28.0, "match": 25.0},
}
FEE_RATE = 0.05
STAKE_UNITS = 1.0
MIN_ASK, MAX_ASK = 0.02, 0.98
MAX_SPREAD = 0.05
MIN_ASK_DEPTH_USD = 50.0
EXIT_MAX_SPREAD = 0.06
EXIT_FORCE_S = 900          # 15 minutes past the moment it was due, sell at any bid
ONE_ENTRY_WINDOW_H = 6

_DRAW_Q = re.compile(r"end\s+in\s+a\s+draw\?$", re.I)


# ── reading a fixture ────────────────────────────────────────────────────────

def team_side(spec: dict, ko: dict | None, home: str, away: str) -> tuple[str | None, float | None]:
    """(side, kick-off Yes price) of the team the rule is about.

    favourite / underdog need both kick-off legs resolved to OPPOSITE sides
    (fav_swing_agent's rule — a side error inverts the bet); home / away are
    api-football's own sides, never Polymarket's title order."""
    team = spec.get("team")
    if not team:
        return None, None
    prices: dict[str, float] = {}
    if ko:
        for name, leg in ko["legs"].items():
            side = resolve_side(name, home, away)
            if side is None or side in prices or not leg.get("price"):
                prices = {}
                break
            prices[side] = leg["price"]
    if team in ("home", "away"):
        return team, prices.get(team)
    if set(prices) != {"home", "away"} or prices["home"] == prices["away"]:
        return None, None
    fav = "home" if prices["home"] > prices["away"] else "away"
    side = fav if team == "favourite" else ("away" if fav == "home" else "home")
    return side, prices[side]


def score_ok(state: str, side: str | None, hg: int, ag: int) -> bool:
    if state == "any":
        return True
    if state == "level":
        return hg == ag
    if state == "goalless":
        return hg == 0 and ag == 0
    if side is None:
        return False
    mine, theirs = (hg, ag) if side == "home" else (ag, hg)
    return mine > theirs if state == "team_ahead" else mine < theirs


def pressure_reading(spec: dict, sig: PressureSignals) -> tuple[float, float] | None:
    """(home, away) on the chosen window, or None when there is no reading."""
    if not sig.has_stats:
        return None
    if spec.get("pressure_window") == "last15":
        now = current_pressure(sig)
        return None if now is None else (now[1], now[2])
    _, h, a = opening_pressure(sig)
    return h, a


def pressure_ok(spec: dict, side: str | None, reading: tuple[float, float] | None) -> bool:
    kind = spec.get("pressure", "none")
    if kind == "none":
        return True
    if reading is None:
        return False
    g = PRESSURE_GATES.get(spec.get("pressure_level"), PRESSURE_GATES["pressing"])
    h, a = reading
    if kind == "match":
        return (h + a) / 2.0 >= g["match"]

    def dominant(own: float, other: float) -> bool:
        return own >= g["own"] and own - other >= g["gap"]
    if kind == "team":
        if side is None:
            return False
        return dominant(h, a) if side == "home" else dominant(a, h)
    return dominant(h, a) or dominant(a, h)          # either


def odds_in(lo, hi, price: float | None) -> bool:
    """A decimal-odds band on a probability price; None bounds are open."""
    if lo is None and hi is None:
        return True
    if not price or price <= 0:
        return False
    odds = 1.0 / price
    return (lo is None or odds >= lo - 1e-9) and (hi is None or odds <= hi + 1e-9)


def market_token(spec: dict, fx: dict, side: str | None, home: str, away: str,
                 goals: int) -> dict | None:
    """{token_id, condition_id, question, target_line?} for what the rule buys."""
    market = spec["market"]
    if market == "win":
        for name, leg in win_legs(fx).items():
            if resolve_side(name, home, away) == side:
                return {"token_id": leg["token_id"], "condition_id": leg.get("condition_id"),
                        "question": leg.get("question")}
        return None
    if market == "draw":
        for m in fx.get("markets") or []:
            if m.get("closed"):
                continue
            q = (m.get("question") or "").strip()
            if _DRAW_Q.search(q) and "half" not in q.lower():
                tok = _yes_token(m)
                if tok:
                    return {"token_id": tok, "condition_id": m.get("conditionId"), "question": q}
        return None
    line = goals + 0.5                                          # next_goal
    cell = ladder_of(fx).get(line)
    if not cell or not cell.get("token_id"):
        return None
    return {"token_id": cell["token_id"], "condition_id": cell.get("condition_id"),
            "question": cell.get("question"), "target_line": line}


def book_verdict(book: dict | None, spec: dict) -> str | None:
    if not book or book.get("best_bid") is None or book.get("best_ask") is None:
        return "no two-sided book"
    ask, bid = book["best_ask"], book["best_bid"]
    if not MIN_ASK <= ask <= MAX_ASK:
        return f"ask {ask:.3f} out of range"
    if ask - bid > MAX_SPREAD:
        return f"spread {ask - bid:.3f}"
    if (book.get("ask_depth_usd") or 0) < MIN_ASK_DEPTH_USD:
        return f"ask depth ${book.get('ask_depth_usd') or 0:.0f}"
    if not odds_in(spec.get("odds_min"), spec.get("odds_max"), ask):
        return f"price {1 / ask:.2f} outside the band"
    return None


def pre_book_checks(spec: dict, sig: PressureSignals, fx: dict, ko: dict | None,
                    code: str | None) -> tuple[bool, str | None, float | None, tuple | None]:
    """Everything the rule asks that costs no request: (ok, side, ko_price, reading)."""
    if not spec["minute_min"] <= sig.minute <= spec["minute_max"]:
        return False, None, None, None
    if spec.get("leagues") and code not in spec["leagues"]:
        return False, None, None, None
    side, ko_price = team_side(spec, ko, sig.home, sig.away)
    if spec.get("team") and side is None:
        return False, None, None, None
    if (spec.get("ko_odds_min") or spec.get("ko_odds_max")) and not odds_in(
            spec.get("ko_odds_min"), spec.get("ko_odds_max"), ko_price):
        return False, side, ko_price, None
    if not score_ok(spec.get("score", "any"), side, sig.home_goals, sig.away_goals):
        return False, side, ko_price, None
    reading = pressure_reading(spec, sig)
    if not pressure_ok(spec, side, reading):
        return False, side, ko_price, reading
    return True, side, ko_price, reading


# ── the exit clock (pure) ────────────────────────────────────────────────────

def _triggered(pos: dict, hg: int, ag: int) -> bool:
    eh, ea = pos["entry_home_goals"], pos["entry_away_goals"]
    which = pos["spec"].get("exit_goal", "any")
    if which == "any" or not pos.get("team_side"):
        return (hg, ag) != (eh, ea)
    home_team = pos["team_side"] == "home"
    mine, mine0 = (hg, eh) if home_team else (ag, ea)
    theirs, theirs0 = (ag, ea) if home_team else (hg, eh)
    return mine > mine0 if which == "team" else theirs > theirs0


def step(pos: dict, hg: int, ag: int, minute: int, now: datetime) -> str:
    """Advance a position by one poll: 'hold' | 'goal' | 'regoal' | 'reversed' | 'sell_due'."""
    spec = pos["spec"]
    pos["last_seen_at"], pos["last_minute"] = now, minute
    exit_kind = spec.get("exit", "hold")
    if exit_kind == "hold":
        return "hold"
    if exit_kind == "at_minute":
        if minute >= (spec.get("exit_minute") or 999):
            if pos.get("goal_seen_at") is None:          # when the sale became due
                pos["goal_seen_at"] = now
            return "sell_due"
        return "hold"
    trig = _triggered(pos, hg, ag)
    if pos["status"] == "open":
        if not trig:
            return "hold"
        pos.update(status="goal_pending", goal_seen_at=now, goal_minute=minute,
                   goal_home_goals=hg, goal_away_goals=ag)
        return "goal"
    if not trig:
        pos.update(status="open", goal_seen_at=None, goal_minute=None, goal_home_goals=None,
                   goal_away_goals=None, goals_reversed=pos.get("goals_reversed", 0) + 1)
        return "reversed"
    if (hg, ag) != (pos["goal_home_goals"], pos["goal_away_goals"]):
        pos.update(goal_seen_at=now, goal_minute=minute, goal_home_goals=hg, goal_away_goals=ag)
        return "regoal"
    wait = 60 * int(spec.get("exit_wait_min") or 5)
    return "sell_due" if (now - pos["goal_seen_at"]).total_seconds() >= wait else "hold"


def exit_book_ok(book: dict | None, waited_s: float) -> bool:
    if not book or not book.get("best_bid"):
        return False
    if book.get("best_ask") is None:
        return waited_s >= EXIT_FORCE_S
    return book["best_ask"] - book["best_bid"] <= EXIT_MAX_SPREAD or waited_s >= EXIT_FORCE_S


# ── the database ─────────────────────────────────────────────────────────────

def running_rules(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, name, spec FROM strategies
                        WHERE source = 'lab' AND run_status = 'running' AND retired_at IS NULL
                          AND spec->>'kind' = 'inplay'""")
        out = []
        for r in cur.fetchall():
            spec = r["spec"] if isinstance(r["spec"], dict) else json.loads(r["spec"] or "{}")
            out.append({"id": r["id"], "name": r["name"], "spec": spec})
        return out


def entered_keys(conn) -> set:
    with conn.cursor() as cur:
        cur.execute("""SELECT strategy_id, fixture_id, home, away FROM lab_inplay_positions
                        WHERE entered_at > now() - make_interval(hours => %s)""", (ONE_ENTRY_WINDOW_H,))
        keys = set()
        for sid, fid, h, a in cur.fetchall():
            keys.add((sid, fid))
            keys.add((sid, h, a))
        return keys


def open_positions(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM lab_inplay_positions
                        WHERE status IN ('open', 'goal_pending')
                          AND entered_at > now() - interval '12 hours'""")
        return [dict(r) for r in cur.fetchall()]


def enter(conn, rule: dict, sig: PressureSignals, fx: dict, side: str | None, ko_price,
          reading, tok: dict, book: dict) -> int:
    spec = rule["spec"]
    ask = float(book["best_ask"])
    shares = STAKE_UNITS / cost_per_share(ask, FEE_RATE)
    title = fx.get("title") or f"{sig.home} vs {sig.away}"
    team = (sig.home if side == "home" else sig.away) if side else None
    what = {"win": f"{team} to win", "draw": "Draw",
            "next_goal": f"Over {tok.get('target_line')} goals"}[spec["market"]]
    reasoning = (
        f"Lab live rule \"{rule['name'][:120]}\". {sig.home} {sig.home_goals}-{sig.away_goals} "
        f"{sig.away} {sig.minute}'. Bought {what} at the ask {ask:.3f} ({1 / ask:.2f})"
        + (f"; {team} was {1 / ko_price:.2f} at kick-off" if ko_price else "")
        + (f"; pressure {reading[0]:.0f}-{reading[1]:.0f}" if reading else "")
        + ". 1u, PAPER."
    )
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO paper_trades (strategy_id, outcome, entry_price, entry_odds,
                                                 stake_units, reasoning, confidence, pm_token_id, pm_live)
                       VALUES (%s,%s,%s,%s,%s,%s,'paper',%s,false) RETURNING id""",
                    (rule["id"], f"{what} — {title}", ask, 1 / ask, STAKE_UNITS, reasoning, tok["token_id"]))
        ptid = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO lab_inplay_positions
              (strategy_id, paper_trade_id, spec, fixture_id, league, home, away, event_title,
               market, team_side, team_name, ko_price, token_id, condition_id, target_line,
               entry_minute, entry_home_goals, entry_away_goals, entry_ask, entry_bid,
               home_pressure, away_pressure, stats_source, shares, fee_rate, last_seen_at, last_minute)
            VALUES (%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)""",
                    (rule["id"], ptid, json.dumps(spec), sig.fixture_id, sig.league, sig.home, sig.away,
                     title, spec["market"], side, team, ko_price, tok["token_id"], tok.get("condition_id"),
                     tok.get("target_line"), sig.minute, sig.home_goals, sig.away_goals, ask,
                     book["best_bid"], reading[0] if reading else None, reading[1] if reading else None,
                     sig.stats_source, shares, FEE_RATE, sig.minute))
    conn.commit()
    return ptid


_SAVE = ("status", "goal_seen_at", "goal_minute", "goal_home_goals", "goal_away_goals",
         "goals_reversed", "last_seen_at", "last_minute")


def save(conn, pos: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(f"UPDATE lab_inplay_positions SET {', '.join(f'{c} = %s' for c in _SAVE)}, "
                    f"updated_at = now() WHERE id = %s", [pos[c] for c in _SAVE] + [pos["id"]])
    conn.commit()


def close(conn, pos: dict, *, reason: str, payout: float, minute=None, bid=None, ask=None) -> None:
    with conn.cursor() as cur:
        cur.execute("""UPDATE lab_inplay_positions
                          SET status = %s, exit_at = now(), exit_minute = %s, exit_bid = %s, exit_ask = %s,
                              exit_reason = %s, payout_units = %s, updated_at = now()
                        WHERE id = %s""",
                    ("sold" if reason.startswith("sold") else "settled", minute, bid, ask, reason,
                     payout, pos["id"]))
        cur.execute("""UPDATE paper_trades SET result = %s, payout_units = %s, resolved_at = now(),
                              closing_price = %s
                        WHERE id = %s AND result IS NULL""",
                    ("won" if payout > STAKE_UNITS else "lost", payout, bid, pos["paper_trade_id"]))
    conn.commit()


# ── one cycle ────────────────────────────────────────────────────────────────

def _find(signals: dict, pos: dict) -> PressureSignals | None:
    s = signals.get(pos["fixture_id"])
    if s is not None:
        return s
    return next((s for s in signals.values() if s.home == pos["home"] and s.away == pos["away"]), None)


def manage(conn, signals: dict, dry_run: bool = False) -> dict:
    counts = {"held": 0, "sold": 0}
    now = datetime.now(timezone.utc)
    for pos in open_positions(conn):
        sig = _find(signals, pos)
        if sig is None:
            continue
        act = step(pos, sig.home_goals, sig.away_goals, sig.minute, now)
        if act == "sell_due":
            waited = (now - pos["goal_seen_at"]).total_seconds()
            book = _fetch_book({"token_id": pos["token_id"]})
            if exit_book_ok(book, waited):
                bid = float(book["best_bid"])
                payout = float(pos["shares"]) * sale_per_share(bid, float(pos["fee_rate"]))
                reason = "sold_at_minute" if pos["spec"].get("exit") == "at_minute" else "sold_after_goal"
                if not dry_run:
                    close(conn, pos, reason=reason, payout=payout, minute=sig.minute, bid=bid,
                          ask=book.get("best_ask"))
                counts["sold"] += 1
                log.info(f"[lab-live] #{pos['strategy_id']} SELL {pos['home']} {sig.home_goals}-"
                         f"{sig.away_goals} {pos['away']} {sig.minute}' at {bid:.3f} → "
                         f"{payout - STAKE_UNITS:+.2f}u  pt#{pos['paper_trade_id']}")
                continue
        counts["held"] += 1
        if not dry_run:
            save(conn, pos)
    return counts


def cycle(conn, signals: dict, pm_fixtures: list[dict], state: SwingState,
          dry_run: bool = False) -> dict:
    """Manage what is held, then look for entries. Returns counts for the log."""
    if conn is None:
        return {}
    rules = running_rules(conn)
    counts = manage(conn, signals, dry_run)
    counts.update(rules=len(rules), entered=0)
    state.capture(pm_fixtures)                     # kick-off prices, every fixture, before KO
    if not rules:
        return counts
    from lab_strategy_runner import competition_code
    done = entered_keys(conn)
    for sig in signals.values():
        fx = match_pm_fixture(sig, pm_fixtures)
        if fx is None:
            continue
        code = competition_code(fx)
        ko = state.ko.get(fx["title"])
        for rule in rules:
            if (rule["id"], sig.fixture_id) in done or (rule["id"], sig.home, sig.away) in done:
                continue
            spec = rule["spec"]
            ok, side, ko_price, reading = pre_book_checks(spec, sig, fx, ko, code)
            if not ok:
                continue
            tok = market_token(spec, fx, side, sig.home, sig.away, sig.home_goals + sig.away_goals)
            if tok is None:
                continue
            book = _fetch_book({"token_id": tok["token_id"]})
            why = book_verdict(book, spec)
            if why:
                log.info(f"[lab-live] #{rule['id']} {sig.home} v {sig.away} {sig.minute}': skip — {why}")
                continue
            if dry_run:
                log.info(f"[lab-live] DRY #{rule['id']} would buy at {book['best_ask']:.3f}")
                continue
            ptid = enter(conn, rule, sig, fx, side, ko_price, reading, tok, book)
            done.update({(rule["id"], sig.fixture_id), (rule["id"], sig.home, sig.away)})
            counts["entered"] += 1
            log.info(f"  ENTER  [LAB #{rule['id']}] {sig.home} {sig.home_goals}-{sig.away_goals} "
                     f"{sig.away} {sig.minute}' {spec['market']} ask={book['best_ask']:.3f}  pt#{ptid}")
    return counts


def settle(conn) -> int:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM lab_inplay_positions
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
        close(conn, p, reason="held_won" if won else "held_lost", payout=float(p["shares"]) if won else 0.0)
        n += 1
    return n


def report(conn) -> None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT s.id, s.name, count(p.*) n, count(*) FILTER (WHERE p.status IN ('open','goal_pending')) open_n,
                              sum(p.payout_units - 1) pl
                         FROM strategies s LEFT JOIN lab_inplay_positions p ON p.strategy_id = s.id
                        WHERE s.source = 'lab' AND s.spec->>'kind' = 'inplay'
                        GROUP BY 1, 2 ORDER BY 1""")
        rows = cur.fetchall()
    print("\n=== Lab live rules ===")
    for r in rows:
        print(f"  #{r['id']:<5} n={r['n']:3d} open={r['open_n']:2d} P&L={float(r['pl'] or 0):+.2f}u  {r['name'][:70]}")


def main() -> None:
    import db_txn
    ap = argparse.ArgumentParser(description="Lab live rules (paper). The live loop is pressure_agent.py.")
    ap.add_argument("--settle", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S", force=True)
    conn = db_txn.connect(os.getenv("DATABASE_URL"))
    if args.settle:
        log.info(f"settled {settle(conn)} held positions")
    report(conn)


if __name__ == "__main__":
    main()
