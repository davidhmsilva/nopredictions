"""
Pre-Match Drift Monitor — exit manager for drift_positions.

Runs every POLL_INTERVAL minutes (default 15). For each open drift position:
  1. Read the CLOB book of the NO token (or the YES book mirrored, for rows
     that never stored the NO token).
  2. Price the exit as a SALE: walk the NO bids for the shares held. That is
     what selling NO receives. (Until db/076 this read GET /price?side=BUY on
     the YES token, which returns the YES BID, so the exit was 1 − YES bid =
     the NO ASK — a price nobody would pay us.)
  3. Exit if:
       a. NO sale price >= target_no_price  (take profit)
       b. NO sale price <= stop_no_price    (stop loss)
       c. kickoff <= HARD_CLOSE_MINUTES away (force close before match starts)

Usage:
    python drift_monitor.py              # one check cycle
    python drift_monitor.py --forever    # loop every POLL_INTERVAL seconds
    python drift_monitor.py --dry-run    # print only, no DB writes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../ingest/.env"))
sys.path.insert(0, os.path.dirname(__file__))

import drift_book

DATABASE_URL   = os.getenv("DATABASE_URL")
POLL_INTERVAL  = int(os.environ.get("DRIFT_POLL_SEC",  "900"))   # 15 min default
# 60 min default (was 15/30) — must be wide enough that we never hold past kickoff
# even if the monitor misses 2-3 consecutive cycles. Drift is a PRE-MATCH-ONLY
# strategy: no position should ever face settlement.
HARD_CLOSE_MIN = int(os.environ.get("DRIFT_HARD_CLOSE", "60"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [drift-mon] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
# No FileHandler: the scheduler appends stdout to drift_monitor.log, and a
# second handler on the same file wrote every line twice.
log = logging.getLogger("drift-mon")


def _conn():
    return psycopg2.connect(DATABASE_URL)


def exit_quote(yes_token: str, no_token: str | None, shares: float) -> dict | None:
    """What selling `shares` of NO receives right now, from the CLOB book.

    None when the book could not be read. `sell` is None when the NO bids
    cannot absorb the position: there is no price to exit at."""
    try:
        books = drift_book.fetch_books([yes_token, no_token or ""])
    except Exception as exc:                                         # noqa: BLE001
        log.warning(f"    book read failed: {exc}")
        return None
    nb = drift_book.no_book(books, yes_token, no_token)
    if nb is None:
        return None
    t = drift_book.top(nb)
    return {
        "sell": drift_book.sell_fill(nb["bids"], shares),   # average price RECEIVED
        "no_bid": t["bid"], "no_ask": t["ask"], "no_bid_size": t["bid_size"],
    }


def _open_positions(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT id, token_id, no_token_id, home, away, outcome_key, price_convention,
                  entry_yes_price, entry_no_price, size_shares,
                  target_no_price, stop_no_price, hard_close_minutes,
                  kickoff_at, stake_usd
           FROM drift_positions
           WHERE status = 'open'
           ORDER BY kickoff_at"""
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _close_position(conn, pos_id: int, quote: dict, reason: str, dry_run=False):
    no_price = quote["sell"]
    cur = conn.cursor()
    cur.execute(
        "SELECT entry_no_price, size_shares, stake_usd FROM drift_positions WHERE id=%s",
        (pos_id,)
    )
    row = cur.fetchone()
    if not row:
        return
    entry_no, size, stake = float(row[0]), float(row[1] or 0), float(row[2] or 1)

    pnl_pct  = round((no_price - entry_no) / entry_no, 4)
    pnl_usd  = round((no_price - entry_no) * size, 4)

    log.info(
        f"  EXIT [{pos_id}] reason={reason} | "
        f"NO paid {entry_no:.3f} → sold {no_price:.3f} (bid {quote['no_bid']}, ask {quote['no_ask']}) | "
        f"pnl={'+' if pnl_pct>=0 else ''}{pnl_pct*100:.1f}% / "
        f"{'+'if pnl_usd>=0 else ''}{pnl_usd:.2f}$"
    )

    if dry_run:
        return

    cur.execute(
        """UPDATE drift_positions SET
               status=%s, exit_at=NOW(), exit_yes_price=%s, exit_no_price=%s,
               exit_no_bid=%s, exit_no_ask=%s, exit_no_bid_size=%s,
               exit_reason=%s, realized_pnl_pct=%s, realized_pnl_usd=%s,
               updated_at=NOW()
           WHERE id=%s AND status='open'""",
        ("closed", round(1.0 - no_price, 4), no_price,
         quote["no_bid"], quote["no_ask"], quote["no_bid_size"],
         reason, pnl_pct, pnl_usd, pos_id),
    )
    conn.commit()


def _process_position(conn, pos: dict, now: datetime, dry_run: bool) -> bool:
    """Check one position; close if a trigger fires. Returns True if exited.

    Each position is independently try/except'd by the caller so one bad row
    can never crash the whole monitor cycle (that's what swallowed the
    Estonia v BiH exit window on 2026-06-09).
    """
    token_id       = pos["token_id"]
    target_no      = float(pos["target_no_price"])
    stop_no        = float(pos["stop_no_price"])
    entry_no       = float(pos["entry_no_price"])
    kickoff        = pos["kickoff_at"]
    hard_close_min = pos["hard_close_minutes"] or HARD_CLOSE_MIN
    mins_to_kick   = (kickoff - now).total_seconds() / 60 if kickoff else 9999

    size = float(pos["size_shares"] or 0) or float(pos["stake_usd"] or 1) / entry_no
    quote = exit_quote(token_id, pos.get("no_token_id"), size)
    no_price = quote["sell"] if quote else None

    # ── Safety net: kickoff already passed ──────────────────────────────
    # Drift is pre-match only. Anything sitting past kickoff is a
    # monitor failure that we must close immediately regardless of price.
    if mins_to_kick <= 0:
        if no_price is not None:
            log.info(f"  [{pos['id']}] {pos['home']} v {pos['away']} — PAST KICKOFF, force-close at NO bid {no_price:.3f}")
            _close_position(conn, pos["id"], quote, "kickoff_post", dry_run)
        else:
            # Can't price (market resolved/inactive). Mark as monitor-miss
            # with realized PnL = 0 (best honest estimate: we missed the
            # exit window, don't claim a fake settlement P&L).
            log.warning(f"  [{pos['id']}] {pos['home']} v {pos['away']} — PAST KICKOFF + price unfetchable → kickoff_missed (0 PnL)")
            if not dry_run:
                cur = conn.cursor()
                cur.execute(
                    """UPDATE drift_positions SET
                           status='closed', exit_at=NOW(), exit_reason='kickoff_missed',
                           realized_pnl_pct=0, realized_pnl_usd=0, updated_at=NOW()
                       WHERE id=%s AND status='open'""",
                    (pos["id"],),
                )
                conn.commit()
        return True

    if no_price is None:
        why = "could not read the book" if quote is None else "no NO bid deep enough to sell into"
        log.warning(f"  [{pos['id']}] {pos['home']} v {pos['away']} — {why}, skipping")
        return False

    drift_pct = round((no_price - entry_no) / entry_no * 100, 1)

    log.info(
        f"  [{pos['id']}] {pos['home']} v {pos['away']} — {pos['outcome_key']} "
        f"[{pos['price_convention']}] | "
        f"NO paid {entry_no:.3f} → bid {no_price:.3f} ({drift_pct:+.1f}%) "
        f"ask {quote['no_ask']} | "
        f"target={target_no:.3f} stop={stop_no:.3f} | "
        f"kickoff in {mins_to_kick:.0f}min"
    )

    reason = None
    if no_price >= target_no:
        reason = "target"
    elif no_price <= stop_no:
        reason = "stop"
    elif mins_to_kick <= hard_close_min:
        reason = "kickoff"

    if reason:
        _close_position(conn, pos["id"], quote, reason, dry_run)
        return True
    return False


def run_once(dry_run=False) -> dict:
    conn = _conn()
    positions = _open_positions(conn)
    now = datetime.now(timezone.utc)

    n_checked = n_exited = n_errors = 0

    for pos in positions:
        n_checked += 1
        try:
            if _process_position(conn, pos, now, dry_run):
                n_exited += 1
        except Exception as exc:
            # Per-position isolation: never let one bad row crash the cycle.
            n_errors += 1
            try:
                conn.rollback()
            except Exception:
                pass
            log.error(f"  [{pos.get('id','?')}] error processing position: {exc}")

    conn.close()
    log.info(f"Monitor cycle done — {n_checked} checked | {n_exited} exited | {n_errors} errors")
    return {"checked": n_checked, "exited": n_exited, "errors": n_errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--forever", action="store_true")
    args = ap.parse_args()

    if args.forever:
        log.info(f"Running forever, poll every {POLL_INTERVAL}s")
        while True:
            try:
                run_once(dry_run=args.dry_run)
            except Exception as exc:
                log.error(f"Cycle error: {exc}")
            time.sleep(POLL_INTERVAL)
    else:
        run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
