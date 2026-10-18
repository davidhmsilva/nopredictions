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
  - The token has never been traded by this agent (one position per token, ever)

Execution (db/076): the position is bought at the NO token's ASK on the CLOB
book, walking the ladder for the whole stake (entry_no_price = the average price
paid), and drift_monitor sells it at the NO BID. Before db/076 it bought at
1 − Gamma's YES ask, which is the NO BID, and banked the spread as profit; those
rows carry price_convention = 'yes_side_inverted'. The SIGNAL is unchanged: it
still compares Gamma's YES ask with the DC fair.

Book gates and edge (2026-10-19), all on the executable price:
  - the NO book is two-sided, ≤ MAX_SPREAD wide and holds ≥ MIN_DEPTH_USD at the ask
  - edge = (1 − NO price paid) − DC fair ≥ MIN_EDGE_PP. Buying NO at its ask is
    selling YES at its bid, so this is the YES price actually obtained. Gamma's
    YES ask is only a cheap pre-filter: it can only overstate the edge.

Exit (handled by drift_monitor.py):
  - NO bid reaches target (+TARGET_PCT on the price paid, default 10%)
  - NO bid falls STOP_PCT below the NO bid at entry (a price move, not the spread)
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

import drift_book
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
# Book gates on the NO side, read at entry (2026-10-19). With executable prices a
# position is marked at the bid the moment it is bought, so a book wider than the
# stop is a stop-out on the first check: on 2026-10-16 11 of 18 dry-run entries
# were. Same thresholds the in-play arms use: 3c is where those books broke.
MAX_SPREAD      = float(os.environ.get("DRIFT_MAX_SPREAD", "0.03"))  # NO ask − NO bid
MIN_DEPTH_USD   = float(os.environ.get("DRIFT_MIN_DEPTH",  "25"))    # $ resting at the NO ask
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
# No FileHandler: the scheduler appends stdout to drift_scanner.log, and a
# second handler on the same file wrote every line twice.
log = logging.getLogger("drift")


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


def _known_token_ids(conn) -> set[str]:
    """Every token this agent has ever held, open or closed. Guarding on open
    positions only let a token re-enter on every scan once the last position
    closed: 32 entries per token in October 2026, ten minutes apart."""
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT token_id FROM drift_positions")
    return {r[0] for r in cur.fetchall()}


def _drift_strategy_id(conn) -> int | None:
    """The 'Pre-Match Drift' strategy row (db/075), so positions carry their
    strategy_id and the owner's Agents tab can find them. None if the migration
    has not been applied — positions still open, just unlinked."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE rules->>'kind' = 'drift' LIMIT 1")
    row = cur.fetchone()
    return int(row[0]) if row else None


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


def entry_quote(yes_token: str, no_token: str | None, stake_usd: float = STAKE_USD) -> dict | None:
    """What buying `stake_usd` of NO costs right now, from the CLOB book.

    None when the book could not be read or its NO asks cannot absorb the stake:
    a position the market would not have filled is not a paper trade."""
    try:
        books = drift_book.fetch_books([yes_token, no_token or ""])
    except Exception as exc:                                         # noqa: BLE001
        log.warning(f"    book read failed: {exc}")
        return None
    nb = drift_book.no_book(books, yes_token, no_token)
    if nb is None:
        return None
    fill = drift_book.buy_fill(nb["asks"], stake_usd)
    if fill is None:
        return None
    yb = books.get(yes_token) or drift_book.mirror(nb)
    no_top, yes_top = drift_book.top(nb), drift_book.top(yb)
    return {
        "entry_no_price": fill[0],          # average price PAID per NO share
        "size_shares": fill[1],
        "entry_no_bid": no_top["bid"],
        "entry_no_ask": no_top["ask"],
        "entry_no_ask_size": no_top["ask_size"],
        "entry_no_depth_usd": round(no_top["ask"] * no_top["ask_size"], 2),
        "entry_yes_bid": yes_top["bid"],
        "entry_yes_ask": yes_top["ask"],
    }


def entry_gate(quote: dict, fair: float) -> tuple[str | None, float]:
    """(reason to refuse or None, executable edge in pp) for a quoted entry.

    The edge is measured on the YES price actually obtained: buying NO at p is
    selling YES at 1 − p."""
    edge_pp = round((1.0 - quote["entry_no_price"] - fair) * 100, 1)
    bid, ask = quote["entry_no_bid"], quote["entry_no_ask"]
    if bid is None or ask is None:
        return "one-sided NO book", edge_pp
    if ask - bid > MAX_SPREAD + 1e-9:
        return f"NO spread {ask - bid:.3f} > {MAX_SPREAD:.3f}", edge_pp
    if (quote["entry_no_depth_usd"] or 0) < MIN_DEPTH_USD:
        return f"${quote['entry_no_depth_usd']:.0f} at the NO ask < ${MIN_DEPTH_USD:.0f}", edge_pp
    if edge_pp < MIN_EDGE_PP:
        return f"executable edge +{edge_pp:.1f}pp < {MIN_EDGE_PP:.1f}pp", edge_pp
    return None, edge_pp


def exit_levels(quote: dict) -> tuple[float, float]:
    """(target, stop) on the NO bid. The target is a real profit on the price
    paid; the stop is a fall of STOP_PCT from the bid at entry, so the spread
    already paid on the way in is not counted as a move against us."""
    target = round(quote["entry_no_price"] * (1 + TARGET_PCT), 4)
    stop = round(quote["entry_no_bid"] * (1 - STOP_PCT), 4)
    return target, stop


def _insert_position(conn, *, token_id, no_token_id, condition_id, question, home, away,
                     outcome_key, kickoff_at, entry_yes_price, dc_fair,
                     edge_pp, quote, exec_edge_pp=None, strategy_id=None,
                     dry_run=False) -> bool:
    entry_no_price  = quote["entry_no_price"]
    size_shares     = quote["size_shares"]
    target_no_price, stop_no_price = exit_levels(quote)
    no_bid = quote["entry_no_bid"]

    log.info(
        f"  ENTRY | {home} v {away} — {outcome_key} | "
        f"signal YES={entry_yes_price:.2f} DC={dc_fair:.2f} edge=+{edge_pp:.1f}pp "
        f"(executable +{exec_edge_pp:.1f}pp) | "
        f"BUY NO at ask {quote['entry_no_ask']:.3f} (paid {entry_no_price:.4f}, "
        f"bid {no_bid if no_bid is not None else '-'}, "
        f"${quote['entry_no_depth_usd']:.0f} at the top) | "
        f"target_NO={target_no_price:.3f} stop_NO={stop_no_price:.3f} | "
        f"kickoff={kickoff_at.strftime('%m-%d %H:%M') if kickoff_at else '?'}"
    )

    if dry_run:
        return True

    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO drift_positions
                 (token_id, no_token_id, condition_id, question, home, away, outcome_key,
                  kickoff_at,
                  entry_yes_price, entry_no_price, dc_fair, edge_pp,
                  stake_usd, size_shares, target_no_price, stop_no_price,
                  hard_close_minutes, strategy_id, price_convention,
                  entry_yes_bid, entry_yes_ask, entry_no_bid, entry_no_ask,
                  entry_no_ask_size, entry_no_depth_usd, exec_edge_pp)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       'executable',%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING
               RETURNING id""",
            (token_id, no_token_id, condition_id, question, home, away, outcome_key,
             kickoff_at,
             entry_yes_price, entry_no_price, dc_fair, edge_pp,
             STAKE_USD, size_shares, target_no_price, stop_no_price,
             HARD_CLOSE_MIN, strategy_id,
             quote["entry_yes_bid"], quote["entry_yes_ask"], quote["entry_no_bid"],
             quote["entry_no_ask"], quote["entry_no_ask_size"], quote["entry_no_depth_usd"],
             exec_edge_pp),
        )
        row = cur.fetchone()
        conn.commit()
        if row:
            log.info(f"    → drift position #{row[0]} opened")
            return True
        log.info(f"    → token already traded by this agent, skipped")
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

    # A dry run still READS the table (known tokens, national teams), so it
    # refuses exactly what a real run would; it never writes.
    conn = _conn() if DATABASE_URL else None
    open_tokens = _known_token_ids(conn) if conn else set()
    national_teams = _load_national_teams(conn) if conn else set()
    strategy_id = _drift_strategy_id(conn) if conn else None
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

            # Pre-filter on Gamma's YES ask: it can only overstate the edge the
            # book will give, so anything that fails here fails entry_gate too.
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

            # The signal fired; now the price a buyer would actually pay.
            no_token_id = _pm_token_id(mkt, "no")
            quote = entry_quote(token_id, no_token_id)
            if quote is None:
                log.info(f"  SKIP {outcome_key} | {home} v {away} | edge=+{edge_pp:.1f}pp "
                         f"but no NO ask deep enough for ${STAKE_USD:.2f}")
                continue
            refuse, exec_edge_pp = entry_gate(quote, fair)
            if refuse:
                log.info(f"  SKIP {outcome_key} | {home} v {away} | Gamma edge=+{edge_pp:.1f}pp "
                         f"but {refuse}")
                continue

            entered = _insert_position(
                conn,
                token_id=token_id,
                no_token_id=no_token_id,
                condition_id=str(mkt.get("conditionId") or mkt.get("id") or ""),
                question=question,
                home=home, away=away,
                outcome_key=outcome_key,
                kickoff_at=kickoff,
                entry_yes_price=ask,
                dc_fair=round(fair, 4),
                edge_pp=edge_pp,
                exec_edge_pp=exec_edge_pp,
                quote=quote,
                strategy_id=strategy_id,
                dry_run=dry_run,
            )
            if entered:
                n_entered += 1
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
