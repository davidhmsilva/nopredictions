"""
Pre-Match Drift Scanner — Strategy: "NO on Longshots"

Thesis: PM systematically overprices longshots in the hours before kickoff.
The YES price drifts DOWN as the game approaches, meaning the NO price rises.
We enter NO positions early and exit before kickoff, capturing the drift.

Calibrated parameters (backtest n=376, p=0.000):
  - target=10%, stop=5% → yield +0.72%/trade, CI [+0.26%, +1.18%]
  - stop=5% is the key parameter — cuts the 11% of trades where YES spikes
  - 87% of positions exit at hard close (kickoff), not at target

Entry conditions:
  - DC model fair < MAX_FAIR_PCT (default 30%) for the outcome
  - PM YES ask > DC fair by MIN_EDGE_PP (default 8pp)
  - Match kickoff is MIN_HOURS_OUT to MAX_HOURS_OUT from now
  - Only home_win and draw (away_win excluded: smaller drift, more stop-outs)
  - No existing open position on this token

Exit (handled by drift_monitor.py):
  - NO price reaches target (+TARGET_PCT, default 10%)
  - NO price falls below stop (-STOP_PCT, default 5%)
  - Hard close HARD_CLOSE_MINUTES (default 15) before kickoff

Usage:
    cd agent && source ../ingest/.venv/bin/activate
    python drift_scanner.py              # scan + log entries
    python drift_scanner.py --dry-run    # print only, no DB writes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../ingest/.env"))
sys.path.insert(0, os.path.dirname(__file__))

from dixon_coles import DixonColesModel
from dc_scanner import _norm, _find_team, _fetch_pm_events, _pm_token_id

DATABASE_URL = os.getenv("DATABASE_URL")
PARAMS_PATH = os.path.join(os.path.dirname(__file__), "dc_model_params.json")

# ── Knobs ─────────────────────────────────────────────────────────────────────
MAX_FAIR_PCT    = float(os.environ.get("DRIFT_MAX_FAIR",   "0.30"))  # DC fair < 30% = longshot
MIN_EDGE_PP     = float(os.environ.get("DRIFT_MIN_EDGE",   "8.0"))   # YES price - DC fair (pp)
MIN_HOURS_OUT   = float(os.environ.get("DRIFT_MIN_HOURS",  "1.5"))   # don't enter < 1.5h before kickoff
MAX_HOURS_OUT   = float(os.environ.get("DRIFT_MAX_HOURS",  "48.0"))  # up to 48h out (captures best drift bucket)
TARGET_PCT      = float(os.environ.get("DRIFT_TARGET_PCT", "0.10"))  # take profit at +10% on NO
STOP_PCT        = float(os.environ.get("DRIFT_STOP_PCT",   "0.05"))  # stop loss at -5% on NO (key parameter)
HARD_CLOSE_MIN  = int(os.environ.get("DRIFT_HARD_CLOSE",  "15"))     # force close 15min before kickoff
STAKE_USD       = float(os.environ.get("DRIFT_STAKE_USD",  "1.0"))
# Draw drift signal scales with favourite strength. Observer (n=88 obs ≥66.7%):
# −4.39pp avg draw drift, 68% down, +7.19% NO return; 1.50-1.80 still +4.81%;
# 1.80-2.50 is the dead zone (+2-3%). Require fav_yes ≥ this gate on DRAW entries.
DRAW_MIN_FAV_YES = float(os.environ.get("DRIFT_DRAW_MIN_FAV", "0.556"))  # odds ≤ 1.80
PRICE_BAND      = (0.05, 0.45)  # YES price range to consider (up to 45c)
SCAN_DAYS       = 3
ALLOWED_OUTCOMES_CLUB = {"home_win", "draw"}        # club football: real home advantage exists
ALLOWED_OUTCOMES_NEUTRAL = {"home_win", "away_win", "draw"}  # neutral venue: all outcomes valid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [drift] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("drift")

handler = logging.FileHandler(os.path.join(os.path.dirname(__file__), "drift_scanner.log"))
handler.setFormatter(logging.Formatter("%(asctime)s [drift] %(message)s", "%H:%M:%S"))
log.addHandler(handler)


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _load_national_teams(conn) -> set[str]:
    """Return set of canonical names for national teams (neutral venue → away_win allowed)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT t.canonical_name
        FROM teams t
        JOIN team_aliases ta ON t.id = ta.team_id
        WHERE ta.source = 'international'
    """)
    names = {r[0].lower() for r in cur.fetchall()}
    cur.close()
    return names


def _is_neutral_venue(home: str, away: str, national_teams: set[str]) -> bool:
    """True when both teams are national sides → no home advantage, away_win is valid."""
    return home.lower() in national_teams and away.lower() in national_teams


def _open_token_ids(conn) -> set[str]:
    """Token IDs already open so we don't double-enter."""
    cur = conn.cursor()
    cur.execute("SELECT token_id FROM drift_positions WHERE status = 'open'")
    return {r[0] for r in cur.fetchall()}


def _parse_kickoff(end_date: str | None) -> datetime | None:
    """PM endDate is approximate kickoff+90min; back off by 95min to estimate kickoff."""
    if not end_date:
        return None
    try:
        dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        return dt - timedelta(minutes=95)
    except Exception:
        return None


def _team_from_question(question: str) -> str | None:
    """Extract team name from 'Will X win on DATE?' format."""
    q = question.strip()
    if q.startswith("Will ") and " win on " in q:
        return q[5:q.index(" win on ")].strip()
    if q.startswith("Will ") and " win?" in q:
        return q[5:q.index(" win?")].strip()
    return None


def _insert_position(conn, *, token_id, condition_id, question, home, away,
                     outcome_key, kickoff_at, entry_yes_price, dc_fair,
                     edge_pp, dry_run=False) -> bool:
    entry_no_price  = round(1.0 - entry_yes_price, 4)
    size_shares     = round(STAKE_USD / entry_no_price, 4)
    target_no_price = round(entry_no_price * (1 + TARGET_PCT), 4)
    stop_no_price   = round(entry_no_price * (1 - STOP_PCT), 4)

    log.info(
        f"  ENTRY | {home} v {away} — {outcome_key} | "
        f"YES={entry_yes_price:.2f} NO={entry_no_price:.2f} DC={dc_fair:.2f} edge=+{edge_pp:.1f}pp | "
        f"target_NO={target_no_price:.2f} stop_NO={stop_no_price:.2f} | "
        f"kickoff={kickoff_at.strftime('%m-%d %H:%M') if kickoff_at else '?'}"
    )

    if dry_run:
        return True

    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO drift_positions
                 (token_id, condition_id, question, home, away, outcome_key,
                  kickoff_at,
                  entry_yes_price, entry_no_price, dc_fair, edge_pp,
                  stake_usd, size_shares, target_no_price, stop_no_price,
                  hard_close_minutes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (token_id) WHERE status = 'open' DO NOTHING
               RETURNING id""",
            (token_id, condition_id, question, home, away, outcome_key,
             kickoff_at,
             entry_yes_price, entry_no_price, dc_fair, edge_pp,
             STAKE_USD, size_shares, target_no_price, stop_no_price,
             HARD_CLOSE_MIN),
        )
        row = cur.fetchone()
        conn.commit()
        if row:
            log.info(f"    → drift position #{row[0]} opened")
            return True
        log.info(f"    → already open for this token, skipped")
        return False
    except Exception as exc:
        conn.rollback()
        log.error(f"    → DB error: {exc}")
        return False


def scan(dry_run: bool = False) -> int:
    model = DixonColesModel.load(PARAMS_PATH)
    norm_idx = {_norm(t): i for i, t in enumerate(model.teams)}

    events = _fetch_pm_events(SCAN_DAYS)
    log.info(f"Fetched {len(events)} PM events")

    conn = None if dry_run else _conn()
    open_tokens = _open_token_ids(conn) if conn else set()
    national_teams = _load_national_teams(conn) if conn else set()
    now = datetime.now(timezone.utc)

    n_entered = 0

    for ev in events:
        end_date = ev.get("endDate") or ev.get("end_date")
        kickoff = _parse_kickoff(end_date)
        if kickoff is None:
            continue
        hours_out = (kickoff - now).total_seconds() / 3600
        if not (MIN_HOURS_OUT <= hours_out <= MAX_HOURS_OUT):
            continue

        # Determine home/away from event title ("X vs. Y" or "X vs Y")
        title = ev.get("title", "")
        sep = " vs. " if " vs. " in title else (" vs " if " vs " in title else None)
        if not sep:
            continue
        parts = title.split(sep, 1)
        home_idx = _find_team(parts[0].strip(), norm_idx)
        away_idx = _find_team(parts[1].strip(), norm_idx)
        if home_idx is None or away_idx is None:
            continue
        home = model.teams[home_idx]
        away = model.teams[away_idx]

        # DC fair probabilities for this match
        neutral = _is_neutral_venue(home, away, national_teams)
        probs = model.predict_or_none(home, away)
        if probs is None:
            continue
        # Neutral venue: use raw DC probs (home_adv baked in but directionally correct
        # for identifying underdogs). Allow all three outcomes since there's no real home side.
        dc = probs
        allowed = ALLOWED_OUTCOMES_NEUTRAL if neutral else ALLOWED_OUTCOMES_CLUB

        # Also check draw market for this event
        candidate_markets = []
        for mkt in ev.get("markets", []):
            if not mkt.get("active") or mkt.get("closed"):
                continue
            candidate_markets.append(mkt)

        # Compute favourite's YES price from the event's home_win / away_win markets.
        # Used to gate DRAW entries (observer: draw drift only at fav ≥ 0.556 / odds ≤ 1.80).
        fav_yes = None
        for mkt in candidate_markets:
            q_lower = (mkt.get("question") or "").lower()
            if "draw" in q_lower or "tie" in q_lower:
                continue
            ask = mkt.get("bestAsk")
            if ask is None:
                continue
            try:
                ask_f = float(ask)
            except (TypeError, ValueError):
                continue
            if fav_yes is None or ask_f > fav_yes:
                fav_yes = ask_f

        for mkt in candidate_markets:
            question = mkt.get("question", "")
            ask = mkt.get("bestAsk")
            if ask is None:
                continue
            ask = float(ask)
            if not (PRICE_BAND[0] <= ask <= PRICE_BAND[1]):
                continue

            token_id = _pm_token_id(mkt, "yes")
            if not token_id or token_id in open_tokens:
                continue

            # Determine outcome_key
            # "Will X win?" → home_win or away_win
            # "Will the match end in a draw?" → draw
            q_lower = question.lower()
            outcome_key = None

            if "draw" in q_lower or "tie" in q_lower:
                outcome_key = "draw"
            else:
                team_name = _team_from_question(question)
                if not team_name:
                    continue
                team_idx = _find_team(team_name, norm_idx)
                if team_idx is None:
                    continue
                team = model.teams[team_idx]
                if team == home:
                    outcome_key = "home_win"
                elif team == away:
                    outcome_key = "away_win"

            if outcome_key is None or outcome_key not in allowed:
                continue

            fair = dc[outcome_key]
            if fair >= MAX_FAIR_PCT:
                continue

            edge_pp = round((ask - fair) * 100, 1)
            if edge_pp < MIN_EDGE_PP:
                continue

            # Draw drift signal scales with favourite strength. Skip draws
            # where neither side is priced ≥ DRAW_MIN_FAV_YES (default 1.80 odds).
            if outcome_key == "draw":
                if fav_yes is None or fav_yes < DRAW_MIN_FAV_YES:
                    log.info(
                        f"  SKIP draw | {home} v {away} | edge=+{edge_pp:.1f}pp "
                        f"but fav_yes={fav_yes if fav_yes is not None else '?'} < "
                        f"{DRAW_MIN_FAV_YES} (no clear favourite)"
                    )
                    continue

            entered = _insert_position(
                conn,
                token_id=token_id,
                condition_id=str(mkt.get("conditionId") or mkt.get("id") or ""),
                question=question,
                home=home, away=away,
                outcome_key=outcome_key,
                kickoff_at=kickoff,
                entry_yes_price=ask,
                dc_fair=round(fair, 4),
                edge_pp=edge_pp,
                dry_run=dry_run,
            )
            if entered:
                n_entered += 1
                if conn:
                    open_tokens.add(token_id)

    log.info(f"Scan complete — {n_entered} new drift positions opened")
    if conn:
        conn.close()
    return n_entered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    scan(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
