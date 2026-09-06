"""
In-play CONVERGENCE trader (shadow phase).

Thesis: late in a match, a state-driven outcome (the DRAW while the score is
level, or the LEADER's moneyline while they're ahead) has a fair probability
that rises mechanically as time runs out. When Polymarket lags that rise, we
BUY the underpriced token and SELL on convergence — a flip, not a
hold-to-resolution bet. Pinnacle / sharp lines do not apply in-play, so this
path is deliberately NOT gated by the sharp-anchored edge engine.

This module runs in SHADOW mode: it logs simulated entries and exits to the
`convergence_shadow` table and measures realized flip P&L (both per-unit % and
fill-weighted $) over a real sample. No real money is placed until that track
record shows positive expectancy.

Mechanics per cycle:
  1. Fetch live football state (score, minute, reds, fixture id) from api-football.
  2. For each live match in the DC model, run the MC sim from the current state
     to get fair probs.
  3. EXIT pass — for every OPEN shadow position:
       • recompute fair from current state;
       • if the match is still live and (fair - best_bid) <= EXIT_BUFFER, the
         edge has converged/evaporated → close at the bid (this captures both a
         winning flip AND an automatic loss-cut when a goal kills the thesis);
       • if the match is no longer live, settle exactly from the final score.
  4. ENTRY pass — for each eligible convergence outcome (tied→draw, leader→ML)
     whose (fair - best_ask) >= ENTRY_THRESHOLD, open a shadow position at the ask
     (one open position per token).

Usage:
    cd agent && source ../ingest/.venv/bin/activate
    python convergence_trader.py --once               # one cycle (shadow)
    python convergence_trader.py --once --dry-run      # no DB writes, just print
    python convergence_trader.py --cycles 24 --interval 300   # ~2h session
    python convergence_trader.py --report              # print shadow P&L summary
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

sys.path.insert(0, os.path.dirname(__file__))
from dixon_coles import DixonColesModel  # noqa: E402
from dc_scanner import _norm, _find_team, _fetch_pm_events, _pm_token_id  # noqa: E402
from sim_scanner import _classify_market  # noqa: E402
from inplay_sim_scanner import _group_pm_events_for_inplay  # noqa: E402
from sim.simulator import simulate, SimConfig  # noqa: E402
from sim.state import MatchState  # noqa: E402
from sim.pricer import price_markets  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
PARAMS_PATH = os.path.join(os.path.dirname(__file__), "dc_model_params.json")

# ── Knobs ─────────────────────────────────────────────────────────────────────
ENTRY_THRESHOLD_PP = float(os.environ.get("CONV_ENTRY_PP", "8.0"))   # fair - ask
EXIT_BUFFER_PP = float(os.environ.get("CONV_EXIT_PP", "3.0"))        # fair - bid to close
MIN_ENTRY_MINUTE = int(os.environ.get("CONV_MIN_MIN", "60"))
POST_GOAL_MIN_MINUTE = int(os.environ.get("CONV_GOAL_MIN", "30"))    # lower threshold when post-goal
MAX_ENTRY_MINUTE = int(os.environ.get("CONV_MAX_MIN", "88"))         # too late: no liquidity to flip
SHADOW_STAKE_USD = float(os.environ.get("CONV_STAKE_USD", "10.0"))
CONV_LIVE_MODE = os.environ.get("CONV_LIVE_MODE", "0") == "1"
CONV_LIVE_STAKE_USD = float(os.environ.get("CONV_LIVE_STAKE_USD", "1.0"))
PM_MIN_SHARES = 5                                                     # Polymarket minimum order size
CONV_ENTRY_RETRIES = int(os.environ.get("CONV_ENTRY_RETRIES", "2"))  # max extra attempts after first miss
CONV_RETRY_WAIT = int(os.environ.get("CONV_RETRY_WAIT", "10"))       # seconds between retry attempts
TARGET_EXIT_PCT = float(os.environ.get("CONV_TARGET_PCT", "0.20"))   # exit rule 4: +20% default
PRICE_BAND = (0.05, 0.95)                                            # sane entry-ask band
# Stale-feed guards: a huge in-play "edge" usually means PM repriced a goal that
# api-football hasn't reported yet (PM watches the broadcast; our feed lags 1-3min).
# Above the cap we still record the SHADOW entry (could be a genuinely thin book)
# but never commit real money to it.
MAX_LIVE_EDGE_PP = float(os.environ.get("CONV_MAX_LIVE_PP", "20.0"))  # live-money edge cap
FLICKER_WINDOW_S = int(os.environ.get("CONV_FLICKER_WINDOW", "180"))  # 2nd score change within this = flicker
FLICKER_HOLD_POLLS = 1                                                # stable polls to confirm after a flicker
DECREASE_HOLD_POLLS = int(os.environ.get("CONV_DECREASE_HOLD", "3"))  # stable polls after a score DECREASE (VAR/feed fix)
SIM_N = 30_000
HOURS_WINDOW = 4
GOAL_POLL_INTERVAL = int(os.environ.get("CONV_GOAL_POLL", "60"))     # seconds between score polls
FULL_CYCLE_INTERVAL = int(os.environ.get("CONV_FULL_INTERVAL", "300"))  # seconds between full cycles

# ── Time bomb zones (PM probability = yes price) ──────────────────────────────
# Based on the Betfair odds classification from "Football Trading: Time Bombs".
# In these bands, prices compress faster as time passes — better entry/exit timing.
_TB_ZONES = [
    ("fast_1", 0.45, 0.56),   # Betfair odds ≈ 1.79–2.22
    ("fast_2", 0.33, 0.40),   # Betfair odds ≈ 2.50–3.03
]
# Upper boundary of each fast zone — price crosses this when exiting into the slow zone above.
_TB_EXIT_UPPER = {"fast_1": 0.56, "fast_2": 0.40}


def _time_bomb_zone(price: float) -> Optional[str]:
    """Return the fast-zone name if price sits in one, else None."""
    for name, lo, hi in _TB_ZONES:
        if lo <= price <= hi:
            return name
    return None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [conv] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("conv")


# ── Live state (local fetch: we need fixture_id + status for settlement) ────────

def _fetch_live(norm_idx, team_names) -> dict[tuple[str, str], dict]:
    if not FOOTBALL_API_KEY:
        log.warning("FOOTBALL_API_KEY missing — no live state")
        return {}
    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"live": "all"},
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning(f"api-football HTTP {resp.status_code}")
            return {}
        data = resp.json()
    except Exception as exc:
        log.warning(f"api-football error: {exc}")
        return {}

    out: dict[tuple[str, str], dict] = {}
    for fix in data.get("response", []):
        # Skip women's fixtures — DC model has no women's data
        league_name = fix.get("league", {}).get("name", "")
        if "women" in league_name.lower():
            continue
        teams = fix.get("teams", {})
        home = teams.get("home", {}).get("name", "")
        away = teams.get("away", {}).get("name", "")
        if home.endswith(" W") or away.endswith(" W"):
            continue
        goals = fix.get("goals", {})
        status = fix.get("fixture", {}).get("status", {})
        hg, ag, elapsed = goals.get("home"), goals.get("away"), status.get("elapsed")
        if not home or not away or hg is None or ag is None or elapsed is None:
            continue
        h_idx = _find_team(home, norm_idx)
        a_idx = _find_team(away, norm_idx)
        if h_idx is None or a_idx is None:
            continue
        reds_h = reds_a = 0
        for ev in fix.get("events", []):
            if ev.get("type") == "Card" and ev.get("detail") == "Red Card":
                tn = ev.get("team", {}).get("name", "")
                if tn == home:
                    reds_h += 1
                elif tn == away:
                    reds_a += 1
        out[(team_names[h_idx], team_names[a_idx])] = {
            "fixture_id": fix.get("fixture", {}).get("id"),
            "minute": int(elapsed),
            "home_score": int(hg),
            "away_score": int(ag),
            "home_reds": reds_h,
            "away_reds": reds_a,
        }
    return out


def _fetch_final_result(fixture_id) -> Optional[str]:
    """Return 'draw'/'home_win'/'away_win' once a fixture is finished, else None."""
    if not fixture_id or not FOOTBALL_API_KEY:
        return None
    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"id": fixture_id},
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            timeout=10,
        )
        arr = resp.json().get("response", [])
        if not arr:
            return None
        fix = arr[0]
        short = fix.get("fixture", {}).get("status", {}).get("short")
        if short not in ("FT", "AET", "PEN"):
            return None
        hg = fix.get("goals", {}).get("home")
        ag = fix.get("goals", {}).get("away")
        if hg is None or ag is None:
            return None
        if hg == ag:
            return "draw"
        return "home_win" if hg > ag else "away_win"
    except Exception as exc:
        log.warning(f"final-result fetch failed (fixture {fixture_id}): {exc}")
        return None


# ── Convergence-play eligibility ────────────────────────────────────────────────

def _eligible_outcome(home_score: int, away_score: int) -> tuple[str, str]:
    """The single state-driven convergence outcome for the current score."""
    if home_score == away_score:
        return "draw", "draw_tied"
    if home_score > away_score:
        return "home_win", "leader_ml"
    return "away_win", "leader_ml"


# ── Sim fair for a match's current state ────────────────────────────────────────

def _sim_fair(model, home, away, ls) -> Optional[dict]:
    try:
        pred = model.predict(home, away)
        lh, la = pred["lambda_home"], pred["lambda_away"]
    except Exception:
        return None
    initial = MatchState.at(
        n_sims=SIM_N,
        minute=ls["minute"],
        home=ls["home_score"],
        away=ls["away_score"],
        red_h=ls["home_reds"],
        red_a=ls["away_reds"],
    )
    res = simulate(lh, la, initial_state=initial, config=SimConfig(n_sims=SIM_N, seed=None))
    return price_markets(res)


# ── DB ──────────────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DATABASE_URL)


def _tracking_positions(conn) -> list[dict]:
    """Every entry whose hold-to-end settlement is not yet recorded — we keep
    observing its price path and peak EVEN AFTER the strategy's converged-exit
    fired, so we can replay alternative exit rules offline."""
    cur = conn.cursor()
    cur.execute(
        """SELECT id, token_id, home, away, outcome_key, play_type, fixture_id,
                  entry_price, entry_fair, entry_minute, size_shares, status, peak_bid,
                  in_time_bomb, time_bomb_zone,
                  exit_at_fair_price, exit_at_target_price, exit_tb_out_price,
                  pm_live, pm_live_size, pm_live_stake_usd, pm_live_pnl_usd
           FROM convergence_shadow WHERE settle_result IS NULL"""
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _record_path(conn, pos_id, minute, fair, bid, ask):
    in_fast = bool(_time_bomb_zone(float(bid))) if bid is not None else None
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO convergence_path (position_id, minute, fair, bid, ask, in_fast_zone)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (pos_id, minute, round(fair, 4) if fair is not None else None, bid, ask, in_fast),
    )
    conn.commit()


def _update_peak(conn, pos_id, bid, minute, prev_peak):
    if bid is None:
        return
    if prev_peak is not None and float(bid) <= float(prev_peak):
        return
    cur = conn.cursor()
    cur.execute(
        "UPDATE convergence_shadow SET peak_bid=%s, peak_minute=%s WHERE id=%s",
        (bid, minute, pos_id),
    )
    conn.commit()


def _settle(conn, pos, result):
    """Fill the hold-to-resolution counterfactual for an entry. If the strategy
    never exited (still open), this also becomes its actual exit."""
    won = (result == pos["outcome_key"])
    settle_price = 1.0 if won else 0.0
    entry, size = float(pos["entry_price"]), float(pos["size_shares"])
    settle_pnl = round((settle_price - entry) * size, 4)
    cur = conn.cursor()
    cur.execute(
        """UPDATE convergence_shadow SET
              settle_result=%s, settle_price=%s, settle_pnl_usd=%s, settled_at=NOW(),
              updated_at=NOW() WHERE id=%s""",
        (result, settle_price, settle_pnl, pos["id"]),
    )
    conn.commit()
    # Real-money leg: shares still held at resolution redeem at $1 (win) / $0 (loss).
    # Only when no live SELL ever filled (pm_live_pnl_usd still NULL) — a filled
    # SELL already realized the live P&L and the shares are gone.
    if pos.get("pm_live") and pos.get("pm_live_pnl_usd") is None and pos.get("pm_live_size"):
        live_pnl = settle_price * float(pos["pm_live_size"]) - float(pos["pm_live_stake_usd"] or 0)
        _update_live_exit(conn, pos["id"], None, settle_price, live_pnl)
        log.info(f"    [LIVE] settled {'WIN' if won else 'LOSS'} | "
                 f"{float(pos['pm_live_size']):.2f}sh redeem @ {settle_price:.0f} | live_pnl=${live_pnl:+.2f}")
    actual = None
    if pos["status"] == "open":
        actual = _close_position(
            conn, pos["id"], settle_price,
            "settled_win" if won else "settled_loss",
            None, entry, size,
            fill_verified=True,  # shares redeem at $1/$0 on resolution — a real fill
        )
    return won, settle_pnl, actual


def _close_position(conn, pos_id, exit_price, reason, exit_minute, entry_price, size_shares,
                    *, fill_verified: bool):
    """Book an exit. `fill_verified` is keyword-only and required: every caller
    has to state whether `exit_price` was actually executable. Pass True only
    for a matched live SELL, a bid ladder with real depth for the full size, or
    a settlement redemption. See db/028."""
    realized = (float(exit_price) - float(entry_price)) * float(size_shares)
    pct = (float(exit_price) / float(entry_price) - 1.0) if entry_price else None
    cur = conn.cursor()
    cur.execute(
        """UPDATE convergence_shadow SET
              status='closed', exit_at=NOW(), exit_price=%s, exit_reason=%s,
              exit_minute=%s, realized_pnl_usd=%s, realized_pct=%s,
              exit_fill_verified=%s, updated_at=NOW()
           WHERE id=%s""",
        (exit_price, reason, exit_minute, round(realized, 4),
         round(pct, 4) if pct is not None else None, fill_verified, pos_id),
    )
    conn.commit()
    return realized, pct


def _record_exit_signal(conn, pos_id, bid, minute) -> None:
    """First time the convergence condition fires, stash the minute and the
    quoted bid. Counterfactual only — this is the number the pre-028 code
    booked as realized P&L. Keeping it lets us keep measuring the gap between
    what the quote promised and what actually filled."""
    cur = conn.cursor()
    cur.execute(
        """UPDATE convergence_shadow SET exit_signal_bid=%s, exit_signal_minute=%s
           WHERE id=%s AND exit_signal_minute IS NULL""",
        (bid, minute, pos_id),
    )
    conn.commit()


def _touch_position(conn, pos_id, fair, bid, minute):
    cur = conn.cursor()
    cur.execute(
        """UPDATE convergence_shadow SET last_fair=%s, last_bid=%s,
              last_checked_at=NOW(), exit_minute=%s, updated_at=NOW() WHERE id=%s""",
        (round(fair, 4), bid, minute, pos_id),
    )
    conn.commit()


def _check_exit_triggers(conn, pos: dict, bid: float, minute: int) -> None:
    """Record the first time each exit rule's threshold is crossed.
    Called every cycle for every tracked position (open or already closed)
    so we can replay alternative exit rules in the backtest.
    Does NOT actually close the position — convergence rule stays in charge."""
    if bid is None:
        return

    entry = float(pos["entry_price"])
    entry_fair = float(pos["entry_fair"])
    cur = conn.cursor()
    changed = False

    # Exit rule 3: at model fair — first time bid >= entry_fair
    if pos.get("exit_at_fair_price") is None and bid >= entry_fair:
        cur.execute(
            "UPDATE convergence_shadow SET exit_at_fair_price=%s, exit_at_fair_minute=%s WHERE id=%s",
            (bid, minute, pos["id"]),
        )
        changed = True

    # Exit rule 4: at target % gain — first time bid >= entry * (1 + TARGET_EXIT_PCT)
    target_price = entry * (1.0 + TARGET_EXIT_PCT)
    if pos.get("exit_at_target_price") is None and bid >= target_price:
        cur.execute(
            """UPDATE convergence_shadow SET exit_at_target_price=%s,
               exit_at_target_pct=%s, exit_at_target_minute=%s WHERE id=%s""",
            (bid, TARGET_EXIT_PCT, minute, pos["id"]),
        )
        changed = True

    # Exit rule 5: time bomb exit — bid crosses the upper boundary of the entry fast zone
    tb_zone = pos.get("time_bomb_zone")
    if tb_zone and pos.get("exit_tb_out_price") is None:
        upper = _TB_EXIT_UPPER.get(tb_zone)
        if upper is not None and bid > upper:
            cur.execute(
                "UPDATE convergence_shadow SET exit_tb_out_price=%s, exit_tb_out_minute=%s WHERE id=%s",
                (bid, minute, pos["id"]),
            )
            changed = True

    if changed:
        conn.commit()


def _run_pm(args: list, timeout: int = 30) -> dict:
    """Subprocess call to polymarket_client.py inside .venv-pm."""
    import subprocess, json as _json
    venv_py = os.path.join(os.path.dirname(__file__), "../.venv-pm/bin/python")
    client_py = os.path.join(os.path.dirname(__file__), "polymarket_client.py")
    result = subprocess.run(
        [venv_py, client_py] + [str(a) for a in args],
        capture_output=True, text=True, timeout=timeout,
        cwd=os.path.dirname(__file__),
    )
    try:
        return _json.loads(result.stdout)
    except Exception:
        return {"ok": False, "error": result.stdout.strip() or result.stderr.strip()}


def _conv_place(token_id: str, side: str, price: float, size: float) -> dict:
    """Submit a BUY or SELL to Polymarket for a convergence position."""
    resp = _run_pm(["place_json", token_id, side.upper(), str(round(price, 4)), str(round(size, 2))])
    ok = resp.get("ok", False)
    inner = resp.get("resp") or {}
    order_id = inner.get("orderID") or inner.get("order_id") or inner.get("id")
    error = resp.get("error") or resp.get("message") if not ok else None
    notional = round(price * size, 2)
    if ok:
        log.info(f"    [LIVE] {side.upper()} {size:.2f}sh @ {price:.3f} ≈ ${notional:.2f} | order={order_id}")
    else:
        log.warning(f"    [LIVE] {side.upper()} FAILED: {error or resp}")
    return {"ok": ok, "order_id": order_id, "notional": notional, "error": error}


def _fetch_token_ask(token_id: str) -> Optional[float]:
    """Lightweight CLOB call to get the current best ask for a single token."""
    try:
        resp = requests.get(
            "https://clob.polymarket.com/price",
            params={"token_id": token_id, "side": "BUY"},
            timeout=6,
        )
        if resp.ok:
            price = resp.json().get("price")
            return float(price) if price is not None else None
    except Exception:
        pass
    return None


def _executable_exit(token_id: str, size_shares: float) -> Optional[tuple[float, float]]:
    """Volume-weighted price we could ACTUALLY sell `size_shares` into, walking
    the real CLOB bid ladder.

    The top-of-book bid is a quote, not a fill — booking an exit at it credits
    P&L for size the book never had. Returns (vwap, shares_available);
    shares_available < size_shares means the ladder cannot absorb the position.
    Returns None if the book is unreachable, in which case the caller must NOT
    close — an unverifiable exit is worse than a late one.
    """
    try:
        resp = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout=8,
        )
        if not resp.ok:
            return None
        bids = resp.json().get("bids") or []
    except Exception:
        return None
    if not bids:
        return 0.0, 0.0
    # CLOB returns the bid ladder ascending (best bid last) — sort defensively.
    levels = sorted(
        ((float(b["price"]), float(b["size"])) for b in bids), key=lambda x: -x[0]
    )
    want = float(size_shares)
    need, notional, got = want, 0.0, 0.0
    for price, avail in levels:
        if need <= 0:
            break
        take = min(need, avail)
        notional += take * price
        got += take
        need -= take
    if got <= 0:
        return 0.0, 0.0
    # Summing many levels drifts `got` a hair below `want` even on a book that
    # fully covers us; without this the caller reads a full fill as partial and
    # blocks a legitimate exit.
    if need <= want * 1e-9:
        got = want
    return notional / got, got


# Phrases that mean a market is NOT a plain 1X2 — sibling markets on the same
# event (totals, spreads, BTTS, score-first, half markets, tournament props).
# Word-boundary regex so team names like Hannover / Cardiff don't false-match.
_NOT_1X2_RE = re.compile(
    r"\b(o/u|over|under|spread|both teams|btts|score first|to score|half|"
    r"halftime|clean sheet|exact|corner|cards?|group|world cup|advance|"
    r"qualify|champion|tournament)\b"
)


def _verify_live_market(token_id: str, condition_id: str, outcome_key: str,
                        home: str, away: str) -> tuple[bool, str]:
    """Last line of defense before real money leaves the wallet.

    Fetch the market from the CLOB by condition_id and confirm that
    (a) the resolved question really is the 1X2 market the play intends
        (leader moneyline or draw — not a sibling O/U / BTTS / score-first
        market that the classifier might have mislabeled), and
    (b) token_id is that market's YES token.
    Any doubt → (False, reason): the live order is blocked, shadow row stays.
    """
    try:
        resp = requests.get(f"https://clob.polymarket.com/markets/{condition_id}",
                            timeout=8)
        if not resp.ok:
            return False, f"CLOB market fetch HTTP {resp.status_code}"
        mkt = resp.json()
    except Exception as exc:
        return False, f"CLOB market fetch failed: {exc}"

    q = (mkt.get("question") or "").lower()
    if not q:
        return False, "market has no question"
    if _NOT_1X2_RE.search(q):
        return False, f"not a 1X2 market: '{q}'"

    qw = set(_norm(q).split())
    h_words = {w for w in _norm(home).split() if len(w) >= 4}
    a_words = {w for w in _norm(away).split() if len(w) >= 4}
    if outcome_key == "draw":
        if "draw" not in q:
            return False, f"expected a draw market, got '{q}'"
    elif outcome_key in ("home_win", "away_win"):
        want, other = (h_words, a_words) if outcome_key == "home_win" else (a_words, h_words)
        if "win" not in qw and "beat" not in qw:
            return False, f"expected a moneyline question, got '{q}'"
        if not want or not (want & qw):
            return False, f"question does not name the {outcome_key} team: '{q}'"
        if other & qw:
            return False, f"question names both teams — ambiguous ML: '{q}'"
    else:
        return False, f"unsupported outcome_key '{outcome_key}' for live"

    tokens = mkt.get("tokens") or []
    tok = next((t for t in tokens if str(t.get("token_id")) == str(token_id)), None)
    if tok is None:
        return False, "token_id not found in market tokens"
    if str(tok.get("outcome", "")).strip().lower() != "yes":
        return False, f"token outcome is '{tok.get('outcome')}', expected 'Yes'"
    return True, "ok"


def _attempt_live_buy(conn, shadow_id: int, token_id: str, fair: float, ask: float) -> bool:
    """Place a live BUY, wait 8s, confirm fill. Returns True if filled.
    On success updates convergence_shadow with pm_live=TRUE and real matched size/cost.
    On failure cancels the order and returns False.
    """
    # Execute AT the ask (±1¢ for queue), never up to model fair: a limit at
    # fair sweeps thin books — positions 124/126 paid an 0.80 limit for a
    # $0.07 token. Cap by fair-minus-buffer as a sanity bound.
    buy_limit = round(min(ask + 0.01, fair - EXIT_BUFFER_PP / 100.0, 0.97), 4)
    if buy_limit < ask:
        log.info(f"    [LIVE] skip — fair-buffer limit {buy_limit:.3f} below ask {ask:.3f}")
        return False
    live_size = max(PM_MIN_SHARES, round(CONV_LIVE_STAKE_USD / ask, 2))
    actual_notional = round(live_size * ask, 2)

    buy = _conv_place(token_id, "BUY", buy_limit, live_size)
    if not buy["ok"] or not buy.get("order_id"):
        if buy["ok"]:
            log.warning(f"    → live BUY accepted but no order_id — treating as paper")
        return False

    time.sleep(8)
    status = _run_pm(["get_order_json", buy["order_id"]])
    order_info = status.get("order") or {}
    size_matched = float(order_info.get("size_matched") or 0)

    if size_matched > 0:
        matched_notional = round(size_matched * float(order_info.get("price", ask)), 2)
        cur = conn.cursor()
        cur.execute(
            """UPDATE convergence_shadow SET
                   pm_live=TRUE, pm_order_id_entry=%s,
                   pm_live_size=%s, pm_live_stake_usd=%s
               WHERE id=%s""",
            (buy["order_id"], size_matched, matched_notional, shadow_id),
        )
        conn.commit()
        log.info(f"    → FILLED {size_matched:.2f}sh @ ${matched_notional:.2f} (limit={buy_limit:.3f})")
        return True

    _run_pm(["cancel_json", buy["order_id"]])
    log.warning(f"    → not filled after 8s — cancelled (order={buy['order_id']})")
    return False


def _retry_live_buy(conn, shadow_id: int, token_id: str,
                    match_key: tuple, outcome_key: str, entry_minute: int,
                    fair_fn) -> None:
    """Retry a live BUY up to CONV_ENTRY_RETRIES times after the first attempt missed.
    Each attempt re-fetches the current ask from the CLOB and re-runs the sim from
    the current live state to confirm the edge is still there before placing.
    fair_fn: run_once's fair_for closure (sim fair probs for a match key).
    """
    for attempt in range(1, CONV_ENTRY_RETRIES + 1):
        time.sleep(CONV_RETRY_WAIT)

        # Re-fetch current ask directly from CLOB (lightweight, no full PM event scan).
        fresh_ask = _fetch_token_ask(token_id)
        if fresh_ask is None:
            log.warning(f"    → retry {attempt}: could not fetch current ask — aborting")
            return
        if not (PRICE_BAND[0] <= fresh_ask <= PRICE_BAND[1]):
            log.info(f"    → retry {attempt}: ask {fresh_ask:.3f} outside price band — aborting")
            return

        # Re-run sim from current live state to get a fresh fair value.
        fresh_sim = fair_fn(*match_key)
        if not fresh_sim:
            log.warning(f"    → retry {attempt}: match no longer live in DC model — aborting")
            return
        fresh_fair = float(fresh_sim.get(outcome_key, 0.0))
        fresh_edge = round((fresh_fair - fresh_ask) * 100, 1)

        if fresh_edge < ENTRY_THRESHOLD_PP:
            log.info(
                f"    → retry {attempt}: edge gone (ask={fresh_ask:.3f} fair={fresh_fair:.3f} "
                f"edge={fresh_edge:.1f}pp < {ENTRY_THRESHOLD_PP}pp) — aborting"
            )
            return

        log.info(
            f"    → retry {attempt}/{CONV_ENTRY_RETRIES}: ask={fresh_ask:.3f} "
            f"fair={fresh_fair:.3f} edge=+{fresh_edge:.1f}pp — placing order"
        )
        filled = _attempt_live_buy(conn, shadow_id, token_id, fresh_fair, fresh_ask)
        if filled:
            return  # success

    log.warning(f"    → all {CONV_ENTRY_RETRIES} retries exhausted for shadow #{shadow_id} — stays paper-only")


def _update_live_exit(conn, pos_id: int, order_id, exit_price: float, live_pnl: float):
    cur = conn.cursor()
    cur.execute(
        """UPDATE convergence_shadow SET
               pm_exit_order_id=%s, pm_exit_price_actual=%s, pm_live_pnl_usd=%s, updated_at=NOW()
           WHERE id=%s""",
        (order_id, round(exit_price, 4), round(live_pnl, 4), pos_id),
    )
    conn.commit()


def _attempt_live_sell(conn, pos: dict, bid: float) -> Optional[float]:
    """Place a live SELL at the current bid, wait 8s, confirm fill on the CLOB.
    Records real P&L only on a confirmed fill (size_matched > 0) — never from
    shadow prices. Returns the actual fill price when filled, else None;
    unfilled orders are cancelled so the position stays whole and can be
    retried next cycle or settle at resolution.
    """
    live_size = float(pos["pm_live_size"])
    sell = _conv_place(pos["token_id"], "SELL", bid, live_size)
    if not sell["ok"] or not sell.get("order_id"):
        log.warning(f"    [LIVE] SELL failed — will retry next cycle or settle at resolution")
        return None
    time.sleep(8)
    status = _run_pm(["get_order_json", sell["order_id"]])
    order_info = status.get("order") or {}
    size_matched = float(order_info.get("size_matched") or 0)
    if size_matched > 0:
        sell_price = float(order_info.get("price", bid))
        stake = float(pos.get("pm_live_stake_usd") or 0)
        avg_cost = stake / live_size if live_size else float(pos["entry_price"])
        live_pnl = (sell_price - avg_cost) * size_matched
        _update_live_exit(conn, pos["id"], sell["order_id"], sell_price, live_pnl)
        log.info(f"    [LIVE] SELL filled {size_matched:.2f}sh @ {sell_price:.3f} | live_pnl=${live_pnl:+.2f}")
        return sell_price
    _run_pm(["cancel_json", sell["order_id"]])
    log.warning(f"    [LIVE] SELL not filled after 8s — cancelled. Retry next cycle / settle at resolution.")
    return None


def _open_shadow(conn, *, token_id, condition_id, question, home, away, outcome_key,
                 play_type, fixture_id, ask, fair, edge_pp, minute, score,
                 post_goal=False, pre_goal_score=None, goal_detected_minute=None):
    size = round(SHADOW_STAKE_USD / ask, 4) if ask else 0.0
    tb_zone = _time_bomb_zone(ask)
    in_tb = tb_zone is not None
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO convergence_shadow
                 (token_id, condition_id, question, home, away, outcome_key, play_type,
                  fixture_id, entry_price, entry_fair, entry_edge_pp, entry_minute,
                  entry_score, stake_usd, size_shares, last_fair, last_bid, last_checked_at,
                  in_time_bomb, time_bomb_zone,
                  post_goal, pre_goal_score, goal_detected_minute)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s)
               RETURNING id""",
            (token_id, condition_id, question, home, away, outcome_key, play_type,
             fixture_id, ask, round(fair, 4), edge_pp, minute, score,
             SHADOW_STAKE_USD, size, round(fair, 4), None, in_tb, tb_zone,
             post_goal, pre_goal_score, goal_detected_minute),
        )
        rid = cur.fetchone()[0]
        conn.commit()
        tb_tag = f" [TIME BOMB {tb_zone}]" if in_tb else ""
        goal_tag = " [POST-GOAL]" if post_goal else ""
        log.info(f"    → shadow position #{rid} opened{tb_tag}{goal_tag}")
        return rid
    except psycopg2.errors.UniqueViolation:
        conn.rollback()  # already have an open position on this token
        return None


# ── Cycle ───────────────────────────────────────────────────────────────────────

def run_once(dry_run: bool = False,
             post_goal_keys: Optional[set] = None,
             prev_scores: Optional[dict] = None,
             entry_frozen_keys: Optional[set] = None) -> dict:
    """One scan cycle.
    post_goal_keys: set of (home, away) tuples where a goal was just detected —
                    these get a lower MIN_ENTRY_MINUTE and are flagged post_goal=True.
    prev_scores: dict (home, away) → (hg, ag) score before the last change, used to
                 record the pre-goal score on new entries.
    entry_frozen_keys: matches whose score is unstable (VAR reversal / feed flicker) —
                       NO entries, shadow included; exits and settlement unaffected.
    """
    if post_goal_keys is None:
        post_goal_keys = set()
    if prev_scores is None:
        prev_scores = {}
    if entry_frozen_keys is None:
        entry_frozen_keys = set()
    model = DixonColesModel.load(PARAMS_PATH)
    norm_idx = {_norm(t): i for i, t in enumerate(model.teams)}

    live = _fetch_live(norm_idx, model.teams)
    log.info(f"Live in DC model: {len(live)} match(es)")

    conn = None if dry_run else _conn()
    tracking = _tracking_positions(conn) if conn is not None else []

    # Fetch + group PM events once; used by BOTH the exit pass (for bids) and the
    # entry pass. Skip the fetch only when there is nothing to do at all.
    markets_by_key: dict[tuple[str, str], list[dict]] = {}
    by_match: dict[tuple[str, str], list[dict]] = {}
    if live or tracking:
        events = _fetch_pm_events(max(1, (HOURS_WINDOW + 23) // 24))
        by_match, _, _ = _group_pm_events_for_inplay(events, norm_idx, model.teams, HOURS_WINDOW)
        markets_by_key = {
            key: [m for ev in evs for m in ev.get("markets", [])]
            for key, evs in by_match.items()
        }

    fair_cache: dict[tuple[str, str], Optional[dict]] = {}

    def fair_for(home, away):
        key = (home, away)
        if key not in fair_cache:
            fair_cache[key] = _sim_fair(model, home, away, live[key]) if key in live else None
        return fair_cache[key]

    n_exits = n_settled = n_entries = 0

    # ── EXIT + measurement pass ──
    # For EVERY tracked entry (open or already-converged): record the price path
    # and peak this cycle, run the converged-exit rule (open only), and settle
    # exactly when the match ends. Settlement fills the hold-to-end counterfactual
    # for every entry so exit rules can be compared offline.
    for pos in tracking:
        key = (pos["home"], pos["away"])
        ls = live.get(key)

        if ls is None:
            # Match no longer live → settle exactly from final score.
            result = _fetch_final_result(pos["fixture_id"])
            if result is None:
                continue  # not finished yet (HT gap / between cycles) — hold
            won, settle_pnl, actual = _settle(conn, pos, result)
            n_settled += 1
            tag = "WIN" if won else "LOSS"
            if actual is not None:  # strategy never converged — this is its exit too
                n_exits += 1
                log.info(f"  SETTLE+EXIT {tag} | {key[0]} v {key[1]} {pos['outcome_key']} "
                         f"| entry {float(pos['entry_price']):.3f}→{1.0 if won else 0.0:.2f} "
                         f"| hold-PnL ${settle_pnl:+.2f}")
            else:  # already exited on convergence — just records hold-to-end
                log.info(f"  SETTLE {tag} (already exited) | {key[0]} v {key[1]} "
                         f"{pos['outcome_key']} | hold-PnL ${settle_pnl:+.2f}")
            continue

        sim_p = fair_for(*key)
        fair = float(sim_p.get(pos["outcome_key"], 0.0)) if sim_p else None
        mkt = _market_for_token(markets_by_key.get(key, []), pos["token_id"])
        bid = float(mkt["bestBid"]) if mkt and mkt.get("bestBid") is not None else None
        ask = float(mkt["bestAsk"]) if mkt and mkt.get("bestAsk") is not None else None

        # Record path + peak for every tracked position (even closed ones).
        _record_path(conn, pos["id"], ls["minute"], fair, bid, ask)
        _update_peak(conn, pos["id"], bid, ls["minute"], pos.get("peak_bid"))
        # Track which exit rules would have fired this cycle (backtest data).
        _check_exit_triggers(conn, pos, bid, ls["minute"])

        if pos["status"] != "open":
            # Shadow already exited, but the real shares may still be on the books
            # (SELL missed at convergence). Keep trying to sell at the current bid
            # while the match is live; if it never fills, _settle records the
            # redemption P&L at resolution.
            if (pos.get("pm_live") and pos.get("pm_live_size")
                    and pos.get("pm_live_pnl_usd") is None and bid is not None and bid > 0.02):
                log.info(f"  [LIVE] retry SELL | {key[0]} v {key[1]} {pos['outcome_key']} "
                         f"@{ls['minute']}' bid {bid:.3f}")
                _attempt_live_sell(conn, pos, bid)
            continue  # measurement only
        if fair is None:
            continue  # no fair this cycle
        if bid is None:
            _touch_position(conn, pos["id"], fair, None, ls["minute"])
            continue
        _touch_position(conn, pos["id"], fair, bid, ls["minute"])
        # Convergence / edge-gone: market bid has caught up to (or passed) fair.
        if (fair - bid) > EXIT_BUFFER_PP / 100.0:
            continue

        # Signal fired. Stash the quoted bid as a counterfactual, then find out
        # what we could actually get for it — a quote is not a fill (db/028).
        _record_exit_signal(conn, pos["id"], bid, ls["minute"])
        pos_tag = f"{key[0]} v {key[1]} {pos['outcome_key']} @{ls['minute']}'"

        if pos.get("pm_live") and pos.get("pm_live_size"):
            # Real money: the only proof of executability is a matched SELL.
            exec_px = _attempt_live_sell(conn, pos, bid)
            if exec_px is None:
                log.info(f"  EXIT signalled, SELL unfilled | {pos_tag} | quoted bid "
                         f"{bid:.3f} — position HELD, retry next cycle")
                continue
        else:
            # Paper: the bid ladder must really hold the whole position.
            book = _executable_exit(pos["token_id"], pos["size_shares"])
            if book is None:
                log.warning(f"  EXIT signalled, book unreachable | {pos_tag} — position HELD")
                continue
            exec_px, avail = book
            if avail < float(pos["size_shares"]) or exec_px <= 0:
                log.info(f"  EXIT signalled, book too thin | {pos_tag} | quoted bid "
                         f"{bid:.3f} but only {avail:.1f}/{float(pos['size_shares']):.1f} "
                         f"shares bid — position HELD")
                continue

        pnl, pct = _close_position(
            conn, pos["id"], exec_px, "converged", ls["minute"],
            pos["entry_price"], pos["size_shares"], fill_verified=True,
        )
        n_exits += 1
        slip = (bid - exec_px) * 100.0
        log.info(f"  EXIT converged | {pos_tag} | entry {float(pos['entry_price']):.3f}"
                 f"→fill {exec_px:.3f} (quoted {bid:.3f}, slip {slip:+.1f}pp) "
                 f"| fair {fair:.3f} | PnL ${pnl:+.2f} ({pct*100:+.1f}%)")

    # ── ENTRY pass ──
    if not live:
        if conn:
            conn.close()
        return {"live": 0, "exits": n_exits, "settled": n_settled,
                "entries": 0, "dry_run": dry_run}

    for key, match_events in by_match.items():
        ls = live.get(key)
        if not ls:
            continue
        if key in entry_frozen_keys:
            log.info(f"  entries FROZEN (score unstable) — {key[0]} v {key[1]} "
                     f"{ls['home_score']}-{ls['away_score']} @{ls['minute']}'")
            continue
        minute = ls["minute"]
        is_post_goal = key in post_goal_keys
        min_minute = POST_GOAL_MIN_MINUTE if is_post_goal else MIN_ENTRY_MINUTE
        if minute < min_minute or minute > MAX_ENTRY_MINUTE:
            continue
        outcome_key, play_type = _eligible_outcome(ls["home_score"], ls["away_score"])
        sim_p = fair_for(*key)
        if not sim_p:
            continue
        fair = float(sim_p.get(outcome_key, 0.0))
        home, away = key

        for ev in match_events:
            for mkt in ev.get("markets", []):
                if not mkt.get("active") or mkt.get("closed"):
                    continue
                ask = mkt.get("bestAsk")
                if ask is None:
                    continue
                ask = float(ask)
                if not (PRICE_BAND[0] <= ask <= PRICE_BAND[1]):
                    continue
                question = mkt.get("question", "")
                if _classify_market(question, home, away) != outcome_key:
                    continue
                # _classify_market maps "X to win the second half?" onto plain
                # home_win/away_win, so 14 second-half markets reached the paper
                # ledger and were then settled against the FULL-TIME result.
                # The live path already screens these in _verify_live_market;
                # the shadow path needs the same screen or its P&L is fiction.
                if _NOT_1X2_RE.search(question.lower()):
                    log.info(f"  SKIP non-1X2 sibling market | {question[:60]}")
                    continue
                edge_pp = round((fair - ask) * 100, 1)
                if edge_pp < ENTRY_THRESHOLD_PP:
                    continue
                token_id = _pm_token_id(mkt, "yes")
                if not token_id:
                    continue
                # PM disagreeing with our state by this much usually means PM
                # already priced a goal our feed hasn't seen → shadow-only.
                stale_suspect = edge_pp > MAX_LIVE_EDGE_PP
                score = f"{ls['home_score']}-{ls['away_score']}"
                tb_zone = _time_bomb_zone(ask)
                tb_tag = f" [{tb_zone.upper()}]" if tb_zone else ""
                pg_tag = " [POST-GOAL]" if is_post_goal else ""
                if stale_suspect:
                    log.warning(f"  STALE-FEED SUSPECT +{edge_pp}pp > {MAX_LIVE_EDGE_PP}pp cap | "
                                f"{home} {score} {away} @{minute}' | {outcome_key} | "
                                f"ask {ask:.3f} fair {fair:.3f} — shadow only, no live order")
                log.info(f"  ENTRY signal +{edge_pp}pp | {home} {score} {away} @{minute}' | "
                         f"{outcome_key} | ask {ask:.3f} (@{1/ask:.2f}) fair {fair:.3f} (@{1/fair:.2f}){tb_tag}{pg_tag}")
                n_entries += 1
                if dry_run:
                    continue
                pre_goal = prev_scores.get(key)
                pre_goal_score = f"{pre_goal[0]}-{pre_goal[1]}" if pre_goal and is_post_goal else None
                condition_id = str(mkt.get("conditionId") or mkt.get("id") or "")
                rid = _open_shadow(
                    conn, token_id=token_id,
                    condition_id=condition_id,
                    question=mkt.get("question", ""), home=home, away=away,
                    outcome_key=outcome_key, play_type=play_type,
                    fixture_id=ls["fixture_id"], ask=ask, fair=fair, edge_pp=edge_pp,
                    minute=minute, score=score,
                    post_goal=is_post_goal, pre_goal_score=pre_goal_score,
                    goal_detected_minute=minute if is_post_goal else None,
                )
                if rid and CONV_LIVE_MODE and not stale_suspect:
                    # Hard guard: confirm on the CLOB that this condition_id
                    # really is the intended 1X2 market and the token is its
                    # YES side, BEFORE any real order (incl. retries).
                    live_ok, why = _verify_live_market(
                        token_id, condition_id, outcome_key, home, away)
                    if not live_ok:
                        log.warning(f"    [LIVE] BLOCKED by wrong-market guard: {why} "
                                    f"— position #{rid} stays shadow-only")
                    else:
                        filled = _attempt_live_buy(conn, rid, token_id, fair, ask)
                        if not filled:
                            _retry_live_buy(conn, rid, token_id, key, outcome_key,
                                            minute, fair_for)
                break  # one market per match per cycle

    if conn:
        conn.close()
    log.info(f"Cycle done — {len(live)} live | {n_exits} exits | "
             f"{n_settled} settled | {n_entries} entry signals")
    return {"live": len(live), "exits": n_exits, "settled": n_settled,
            "entries": n_entries, "dry_run": dry_run}


class _ScoreState:
    """Per-match score stability across polls.

    A clean goal (single increase, no recent change) triggers an immediate
    post-goal cycle, same speed as before. Two patterns freeze ENTRIES for the
    match until the score holds for N consecutive polls (exits never freeze):
      • score DECREASE — VAR reversal or feed correction (DECREASE_HOLD_POLLS);
      • flicker — a 2nd change within FLICKER_WINDOW_S (FLICKER_HOLD_POLLS).
    """

    def __init__(self):
        self.prev_scores: dict[tuple[str, str], tuple[int, int]] = {}
        self.pre_change_scores: dict[tuple[str, str], tuple[int, int]] = {}
        self.last_change_ts: dict[tuple[str, str], float] = {}
        self.hold: dict[tuple[str, str], int] = {}

    def update(self, live: dict, now: float) -> tuple[set, set, set]:
        """Ingest one poll. Returns (goal_keys, released_keys, frozen_keys)."""
        goal_keys: set[tuple[str, str]] = set()
        released_keys: set[tuple[str, str]] = set()
        for key, ls in live.items():
            score = (ls["home_score"], ls["away_score"])
            prev = self.prev_scores.get(key)
            if prev is not None and score != prev:
                home, away = key
                change = (f"{home} v {away} {prev[0]}-{prev[1]} → "
                          f"{score[0]}-{score[1]} @{ls['minute']}'")
                decreased = score[0] < prev[0] or score[1] < prev[1]
                last_change = self.last_change_ts.get(key)
                flicker = (last_change is not None
                           and (now - last_change) < FLICKER_WINDOW_S)
                if decreased:
                    self.hold[key] = max(self.hold.get(key, 0), DECREASE_HOLD_POLLS)
                    log.warning(f"  🔄 SCORE CORRECTION (VAR/feed?): {change} — "
                                f"entries frozen until stable {DECREASE_HOLD_POLLS} polls")
                elif flicker:
                    self.hold[key] = max(self.hold.get(key, 0), FLICKER_HOLD_POLLS)
                    log.warning(f"  ⚠️ SCORE FLICKER (2nd change <{FLICKER_WINDOW_S}s): {change} — "
                                f"entries need {FLICKER_HOLD_POLLS} confirming poll(s)")
                else:
                    log.info(f"  ⚽ GOAL: {change}")
                goal_keys.add(key)
                self.pre_change_scores[key] = prev
                self.last_change_ts[key] = now
            elif prev is not None and self.hold.get(key, 0) > 0:
                self.hold[key] -= 1
                if self.hold[key] <= 0:
                    del self.hold[key]
                    released_keys.add(key)
                    log.info(f"  ✅ score stable — entries unfrozen: {key[0]} v {key[1]} "
                             f"{score[0]}-{score[1]} @{ls['minute']}'")
            self.prev_scores[key] = score
        frozen = {k for k, v in self.hold.items() if v > 0}
        return goal_keys, released_keys, frozen


def run_forever(dry_run: bool = False) -> None:
    """Run indefinitely:
    - Poll api-football every GOAL_POLL_INTERVAL seconds (default 60s) for live scores.
    - On score change (goal detected): immediately run a full scan cycle.
    - Score decreases / rapid flickers freeze entries for that match until the
      score is stable (exits still run every cycle); a release triggers a cycle.
    - Also run a full cycle every FULL_CYCLE_INTERVAL seconds (default 300s) regardless.
    Uses ~660 api-football requests/day during an 11-hour match window.
    """
    model = DixonColesModel.load(PARAMS_PATH)
    norm_idx = {_norm(t): i for i, t in enumerate(model.teams)}

    state = _ScoreState()
    last_full_cycle = 0.0
    cycle_n = 0

    log.info(f"Forever mode — goal poll every {GOAL_POLL_INTERVAL}s, "
             f"full cycle every {FULL_CYCLE_INTERVAL}s")

    while True:
        now = time.time()

        # Always fetch live state (1 api-football call per poll)
        live = _fetch_live(norm_idx, model.teams)
        goal_keys, released_keys, frozen = state.update(live, now)

        # Run full cycle on goal/release OR on schedule
        trigger = goal_keys | released_keys
        if trigger or (now - last_full_cycle >= FULL_CYCLE_INTERVAL):
            cycle_n += 1
            if trigger:
                names = ", ".join(f"{k[0]} v {k[1]}" for k in trigger)
                log.info(f"── cycle {cycle_n} [GOAL: {names}] ──")
            else:
                log.info(f"── cycle {cycle_n} [scheduled] ──")
            try:
                run_once(dry_run=dry_run, post_goal_keys=trigger,
                         prev_scores=state.pre_change_scores,
                         entry_frozen_keys=frozen)
            except Exception as exc:
                log.error(f"cycle error: {exc}", exc_info=True)
            last_full_cycle = now

        time.sleep(GOAL_POLL_INTERVAL)


def _market_for_token(markets: list[dict], token_id: str) -> Optional[dict]:
    for m in markets:
        if _pm_token_id(m, "yes") == token_id:
            return m
    return None


# ── Report ──────────────────────────────────────────────────────────────────────

def _fill_verification_note(cur, staked) -> None:
    """Rule 1's P&L is only meaningful for exits that could actually be executed.
    Pre-028 rows booked the quoted bid with no fill check — paired against real
    money they overstated by 137.6pp (db/028). Never print the blended number
    without this split."""
    cur.execute(
        """SELECT count(*), COALESCE(sum(realized_pnl_usd), 0)
             FROM convergence_shadow
            WHERE settle_result IS NOT NULL AND realized_pnl_usd IS NOT NULL
              AND market_is_1x2 AND exit_reason = 'converged'
              AND exit_fill_verified IS NOT TRUE"""
    )
    n_unver, pnl_unver = cur.fetchone()
    cur.execute(
        """SELECT count(*), COALESCE(sum(realized_pnl_usd), 0)
             FROM convergence_shadow
            WHERE settle_result IS NOT NULL AND realized_pnl_usd IS NOT NULL
              AND market_is_1x2
              AND exit_reason = 'converged' AND exit_fill_verified IS TRUE"""
    )
    n_ver, pnl_ver = cur.fetchone()
    cur.execute(
        """SELECT count(*), COALESCE(sum(realized_pnl_usd), 0)
             FROM convergence_shadow
            WHERE settle_result IS NOT NULL AND realized_pnl_usd IS NOT NULL
              AND market_is_1x2 AND exit_reason <> 'converged'"""
    )
    n_hold, pnl_hold = cur.fetchone()

    def _sub(label, pnl, cnt, note):
        yld = float(pnl) / (cnt * SHADOW_STAKE_USD) * 100 if cnt else 0.0
        print(f"  {label:<28}{float(pnl):>+9.2f}{yld:>+8.1f}%{cnt:>5}  {note}")

    # Rule 1 blends two different populations — split them or it reads as edge.
    _sub("└ flips, VERIFIED fill", pnl_ver, n_ver,
         "executable" if n_ver else "⚠ none yet — thesis untested")
    if n_unver:
        _sub("└ flips, UNVERIFIED", pnl_unver, n_unver, "⚠ quoted bid, never filled")
    _sub("└ never flipped (redeemed)", pnl_hold, n_hold, "settlement, not convergence")


def report():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM convergence_shadow WHERE status='open' AND market_is_1x2")
    n_open = cur.fetchone()[0]

    print(f"\n=== Convergence shadow ledger ===")
    print(f"Open positions:   {n_open}")

    # ── Exit-rule comparison ──────────────────────────────────────────────────
    # Scored over entries that have BOTH a converge-exit AND a settlement, so
    # all five rules are evaluated on the same set of bets.
    cur.execute(
        """SELECT count(*),
                  COALESCE(sum(realized_pnl_usd), 0),
                  COALESCE(sum(settle_pnl_usd), 0),
                  COALESCE(sum((peak_bid - entry_price) * size_shares), 0),
                  COALESCE(sum((exit_at_fair_price - entry_price) * size_shares)
                              FILTER (WHERE exit_at_fair_price IS NOT NULL), 0),
                  COALESCE(sum((exit_at_target_price - entry_price) * size_shares)
                              FILTER (WHERE exit_at_target_price IS NOT NULL), 0),
                  COALESCE(sum((exit_tb_out_price - entry_price) * size_shares)
                              FILTER (WHERE exit_tb_out_price IS NOT NULL AND in_time_bomb), 0),
                  count(*) FILTER (WHERE realized_pnl_usd > 0),
                  count(*) FILTER (WHERE settle_pnl_usd > 0),
                  count(*) FILTER (WHERE exit_at_fair_price IS NOT NULL),
                  count(*) FILTER (WHERE exit_at_target_price IS NOT NULL),
                  count(*) FILTER (WHERE exit_tb_out_price IS NOT NULL AND in_time_bomb),
                  count(*) FILTER (WHERE in_time_bomb)
           FROM convergence_shadow
           WHERE settle_result IS NOT NULL AND realized_pnl_usd IS NOT NULL
             AND market_is_1x2"""
    )
    row = cur.fetchone()
    (n, conv_pnl, hold_pnl, peak_pnl,
     fair_pnl, target_pnl, tb_pnl,
     conv_wins, hold_wins,
     n_fair, n_target, n_tb, n_in_tb) = row

    if not n:
        print("\n  No settled flips yet — comparison appears once matches resolve.")
        conn.close()
        return

    staked = n * SHADOW_STAKE_USD
    target_pct_label = f"+{int(TARGET_EXIT_PCT*100)}%"
    print(f"\n  Settled flips: n={n}  staked ${staked:.0f}  "
          f"(of which {n_in_tb} entered in a time-bomb zone)")
    print(f"\n  {'Exit rule':<28}{'P&L':>10}{'Yield':>9}{'Fired':>7}  Notes")
    print(f"  {'-'*60}")

    def _row(label, pnl, wins=None, fired=None, note=""):
        pnl_s = f"{float(pnl):>+9.2f}"
        yield_s = f"{float(pnl)/staked*100:>+8.1f}%"
        win_s = f"{wins/n*100:>5.0f}%" if wins is not None else "    —"
        fired_s = f"{fired:>5}" if fired is not None else "    —"
        print(f"  {label:<28}{pnl_s}{yield_s}{fired_s}  {note}")

    _row("1. CONVERGED (current)",  conv_pnl,   conv_wins, n,      "bid ≈ fair")
    _fill_verification_note(cur, staked)
    _row("2. HOLD to resolution",   hold_pnl,   hold_wins, n,      "FT result")
    _row("3. PEAK bid (ceiling)",   peak_pnl,   None,      n,      "best bid seen (oracle)")
    _row(f"4. AT MODEL FAIR",        fair_pnl,   None,      n_fair, f"first bid ≥ entry_fair  ({n_fair}/{n} fired)")
    _row(f"5. AT TARGET {target_pct_label}",     target_pnl, None,  n_target, f"first bid ≥ entry×{1+TARGET_EXIT_PCT:.2f}  ({n_target}/{n} fired)")
    _row(f"6. TIME BOMB EXIT",       tb_pnl,     None,      n_tb,   f"exits fast zone  ({n_tb}/{n_in_tb} fired from TB entries)")

    print(f"\n  → CONVERGED ≈ HOLD: selling early is free risk reduction.")
    print(f"  → HOLD >> CONVERGED: we're leaving money — hold longer.")
    print(f"  → AT FAIR / TARGET tell you where on the path the best exit sits.")
    print(f"  → TIME BOMB EXIT: valid only for TB-zone entries; compares riding the wave.")
    print(f"  → PEAK is the unreachable oracle ceiling.")
    print(f"\n  ⚠ n={n} — need ≥30 settled flips for conclusions.")

    cur.execute(
        "SELECT COALESCE(avg(realized_pct),0) FROM convergence_shadow "
        "WHERE exit_reason='converged' AND market_is_1x2"
    )
    print(f"\n  Converged avg per-unit return: {float(cur.fetchone()[0])*100:+.1f}%")

    # Recent settled detail
    cur.execute(
        """SELECT home, away, outcome_key, in_time_bomb, time_bomb_zone,
                  entry_price, exit_price, exit_reason,
                  realized_pnl_usd, settle_pnl_usd,
                  exit_at_fair_price, exit_at_target_price, exit_tb_out_price, peak_bid
           FROM convergence_shadow
           WHERE settle_result IS NOT NULL AND settle_pnl_usd IS NOT NULL
             AND market_is_1x2
           ORDER BY settled_at DESC NULLS LAST LIMIT 15"""
    )
    rows = cur.fetchall()
    if rows:
        print("\n  Recent settled  (conv$ | hold$ | @fair | @target | @tb | peak):")
        for (h, a, ok, itb, tbz, ep, xp, rs,
             cpnl, hpnl, efp, etp, etbp, pk) in rows:
            tb_s = f"[{tbz}]" if itb else "      "
            xp_s  = f"{float(xp):.3f}"  if xp   else "  — "
            efp_s = f"{float(efp):.3f}" if efp   else "  — "
            etp_s = f"{float(etp):.3f}" if etp   else "  — "
            etbp_s= f"{float(etbp):.3f}"if etbp  else "  — "
            pk_s  = f"{float(pk):.3f}"  if pk    else "  — "
            print(f"  {h[:10]:10} v {a[:10]:10} {ok:9} {tb_s} "
                  f"{float(ep):.3f}→{xp_s} {rs[:9]:9} "
                  f"${float(cpnl):+.2f}|${float(hpnl):+.2f}|"
                  f"{efp_s}|{etp_s}|{etbp_s}|{pk_s}")
    conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="In-play convergence trader (shadow)")
    p.add_argument("--once", action="store_true", help="One cycle then exit")
    p.add_argument("--forever", action="store_true", help="Run forever with goal detection")
    p.add_argument("--cycles", type=int, default=1, help="Number of cycles (ignored with --forever)")
    p.add_argument("--interval", type=int, default=300, help="Seconds between cycles")
    p.add_argument("--dry-run", action="store_true", help="No DB writes")
    p.add_argument("--report", action="store_true", help="Print shadow P&L and exit")
    args = p.parse_args()

    if args.report:
        report()
        return

    if args.forever:
        run_forever(dry_run=args.dry_run)
        return

    cycles = 1 if args.once else args.cycles
    for i in range(cycles):
        log.info(f"── cycle {i+1}/{cycles} ──")
        try:
            run_once(dry_run=args.dry_run)
        except Exception as exc:
            log.error(f"cycle error: {exc}", exc_info=True)
        if i < cycles - 1:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
