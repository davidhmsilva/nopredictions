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
MAX_ENTRY_MINUTE = int(os.environ.get("CONV_MAX_MIN", "88"))         # too late: no liquidity to flip
SHADOW_STAKE_USD = float(os.environ.get("CONV_STAKE_USD", "10.0"))
PRICE_BAND = (0.05, 0.95)                                            # sane entry-ask band
SIM_N = 30_000
HOURS_WINDOW = 4

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
        teams = fix.get("teams", {})
        home = teams.get("home", {}).get("name", "")
        away = teams.get("away", {}).get("name", "")
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
                  entry_price, entry_fair, entry_minute, size_shares, status, peak_bid
           FROM convergence_shadow WHERE settle_result IS NULL"""
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _record_path(conn, pos_id, minute, fair, bid, ask):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO convergence_path (position_id, minute, fair, bid, ask)
           VALUES (%s,%s,%s,%s,%s)""",
        (pos_id, minute, round(fair, 4) if fair is not None else None, bid, ask),
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
    actual = None
    if pos["status"] == "open":
        actual = _close_position(
            conn, pos["id"], settle_price,
            "settled_win" if won else "settled_loss",
            None, entry, size,
        )
    return won, settle_pnl, actual


def _close_position(conn, pos_id, exit_price, reason, exit_minute, entry_price, size_shares):
    realized = (float(exit_price) - float(entry_price)) * float(size_shares)
    pct = (float(exit_price) / float(entry_price) - 1.0) if entry_price else None
    cur = conn.cursor()
    cur.execute(
        """UPDATE convergence_shadow SET
              status='closed', exit_at=NOW(), exit_price=%s, exit_reason=%s,
              exit_minute=%s, realized_pnl_usd=%s, realized_pct=%s, updated_at=NOW()
           WHERE id=%s""",
        (exit_price, reason, exit_minute, round(realized, 4),
         round(pct, 4) if pct is not None else None, pos_id),
    )
    conn.commit()
    return realized, pct


def _touch_position(conn, pos_id, fair, bid, minute):
    cur = conn.cursor()
    cur.execute(
        """UPDATE convergence_shadow SET last_fair=%s, last_bid=%s,
              last_checked_at=NOW(), exit_minute=%s, updated_at=NOW() WHERE id=%s""",
        (round(fair, 4), bid, minute, pos_id),
    )
    conn.commit()


def _open_shadow(conn, *, token_id, condition_id, question, home, away, outcome_key,
                 play_type, fixture_id, ask, fair, edge_pp, minute, score):
    size = round(SHADOW_STAKE_USD / ask, 4) if ask else 0.0
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO convergence_shadow
                 (token_id, condition_id, question, home, away, outcome_key, play_type,
                  fixture_id, entry_price, entry_fair, entry_edge_pp, entry_minute,
                  entry_score, stake_usd, size_shares, last_fair, last_bid, last_checked_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
               RETURNING id""",
            (token_id, condition_id, question, home, away, outcome_key, play_type,
             fixture_id, ask, round(fair, 4), edge_pp, minute, score,
             SHADOW_STAKE_USD, size, round(fair, 4), None),
        )
        rid = cur.fetchone()[0]
        conn.commit()
        return rid
    except psycopg2.errors.UniqueViolation:
        conn.rollback()  # already have an open position on this token
        return None


# ── Cycle ───────────────────────────────────────────────────────────────────────

def run_once(dry_run: bool = False) -> dict:
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

        if pos["status"] != "open" or fair is None:
            continue  # measurement only (already exited, or no fair this cycle)
        if bid is None:
            _touch_position(conn, pos["id"], fair, None, ls["minute"])
            continue
        _touch_position(conn, pos["id"], fair, bid, ls["minute"])
        # Convergence / edge-gone: market bid has caught up to (or passed) fair.
        if (fair - bid) <= EXIT_BUFFER_PP / 100.0:
            pnl, pct = _close_position(
                conn, pos["id"], bid, "converged", ls["minute"],
                pos["entry_price"], pos["size_shares"],
            )
            n_exits += 1
            log.info(f"  EXIT converged | {key[0]} v {key[1]} {pos['outcome_key']} "
                     f"@{ls['minute']}' | entry {float(pos['entry_price']):.3f}→bid {bid:.3f} "
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
        minute = ls["minute"]
        if minute < MIN_ENTRY_MINUTE or minute > MAX_ENTRY_MINUTE:
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
                if _classify_market(mkt.get("question", ""), home, away) != outcome_key:
                    continue
                edge_pp = round((fair - ask) * 100, 1)
                if edge_pp < ENTRY_THRESHOLD_PP:
                    continue
                token_id = _pm_token_id(mkt, "yes")
                if not token_id:
                    continue
                score = f"{ls['home_score']}-{ls['away_score']}"
                log.info(f"  ENTRY signal +{edge_pp}pp | {home} {score} {away} @{minute}' | "
                         f"{outcome_key} | ask {ask:.3f} fair {fair:.3f}")
                n_entries += 1
                if dry_run:
                    continue
                rid = _open_shadow(
                    conn, token_id=token_id,
                    condition_id=str(mkt.get("conditionId") or mkt.get("id") or ""),
                    question=mkt.get("question", ""), home=home, away=away,
                    outcome_key=outcome_key, play_type=play_type,
                    fixture_id=ls["fixture_id"], ask=ask, fair=fair, edge_pp=edge_pp,
                    minute=minute, score=score,
                )
                if rid:
                    log.info(f"    → shadow position #{rid} opened")
                break  # one market per match per cycle

    if conn:
        conn.close()
    log.info(f"Cycle done — {len(live)} live | {n_exits} exits | "
             f"{n_settled} settled | {n_entries} entry signals")
    return {"live": len(live), "exits": n_exits, "settled": n_settled,
            "entries": n_entries, "dry_run": dry_run}


def _market_for_token(markets: list[dict], token_id: str) -> Optional[dict]:
    for m in markets:
        if _pm_token_id(m, "yes") == token_id:
            return m
    return None


# ── Report ──────────────────────────────────────────────────────────────────────

def report():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM convergence_shadow WHERE status='open'")
    n_open = cur.fetchone()[0]

    print(f"\n=== Convergence shadow ledger ===")
    print(f"Open positions:   {n_open}")

    # ── The exit-rule comparison (answers: sell on convergence vs hold to end) ──
    # Computed only over entries that have BOTH a strategy exit AND a recorded
    # settlement, so the three rules are scored on the same set of bets.
    cur.execute(
        """SELECT count(*),
                  COALESCE(sum(realized_pnl_usd),0),                       -- converged exit
                  COALESCE(sum(settle_pnl_usd),0),                         -- hold to end
                  COALESCE(sum((peak_bid - entry_price) * size_shares),0), -- exit at peak (ceiling)
                  count(*) FILTER (WHERE realized_pnl_usd > 0),
                  count(*) FILTER (WHERE settle_pnl_usd > 0)
           FROM convergence_shadow
           WHERE settle_result IS NOT NULL AND realized_pnl_usd IS NOT NULL"""
    )
    n, conv_pnl, hold_pnl, peak_pnl, conv_wins, hold_wins = cur.fetchone()
    if not n:
        print("\n  No settled flips yet — comparison appears once matches resolve.")
        conn.close()
        return

    staked = n * SHADOW_STAKE_USD
    print(f"\n  Settled flips:  n={n}  (staked ${staked:.0f} @ $10)")
    print(f"  {'Exit rule':<22}{'P&L':>10}{'Yield':>9}{'Win%':>7}")
    print(f"  {'-'*46}")
    print(f"  {'CONVERGED (current)':<22}{float(conv_pnl):>+9.2f}{float(conv_pnl)/staked*100:>+8.1f}%"
          f"{conv_wins/n*100:>6.0f}%")
    print(f"  {'HOLD to resolution':<22}{float(hold_pnl):>+9.2f}{float(hold_pnl)/staked*100:>+8.1f}%"
          f"{hold_wins/n*100:>6.0f}%")
    print(f"  {'PEAK bid (ceiling)':<22}{float(peak_pnl):>+9.2f}{float(peak_pnl)/staked*100:>+8.1f}%"
          f"{'—':>6}")
    print(f"\n  → If CONVERGED ≈ HOLD: selling early is free risk reduction, keep it.")
    print(f"  → If HOLD >> CONVERGED: we're leaving money by selling — hold longer.")
    print(f"  → PEAK is the unreachable ceiling (perfect timing) — gap to it = timing cost.")
    print(f"\n  ⚠ n={n} — need ≥30 settled flips before trusting this.")

    # Per-unit (liquidity-blind) check on the converged rule.
    cur.execute(
        "SELECT COALESCE(avg(realized_pct),0) FROM convergence_shadow WHERE exit_reason='converged'"
    )
    print(f"\n  Converged avg per-unit return: {float(cur.fetchone()[0])*100:+.1f}%")

    cur.execute(
        """SELECT home, away, outcome_key, entry_price, exit_price, exit_reason,
                  realized_pnl_usd, settle_pnl_usd, peak_bid
           FROM convergence_shadow
           WHERE settle_result IS NOT NULL ORDER BY settled_at DESC NULLS LAST LIMIT 12"""
    )
    rows = cur.fetchall()
    if rows:
        print("\n  Recent settled (entry→exit | converged$ | hold$ | peak):")
        for h, a, ok, ep, xp, rs, cpnl, hpnl, pk in rows:
            xps = f"{float(xp):.3f}" if xp is not None else "  —  "
            pks = f"{float(pk):.3f}" if pk is not None else "  —  "
            print(f"    {h[:12]:12} v {a[:12]:12} {ok:9} {float(ep):.3f}→{xps} {rs:13} "
                  f"${float(cpnl):+.2f} | ${float(hpnl):+.2f} | {pks}")
    conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="In-play convergence trader (shadow)")
    p.add_argument("--once", action="store_true", help="One cycle then exit")
    p.add_argument("--cycles", type=int, default=1, help="Number of cycles")
    p.add_argument("--interval", type=int, default=300, help="Seconds between cycles")
    p.add_argument("--dry-run", action="store_true", help="No DB writes")
    p.add_argument("--report", action="store_true", help="Print shadow P&L and exit")
    args = p.parse_args()

    if args.report:
        report()
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
