"""
Pre-Match Drift Monitor — exit manager for drift_positions.

Runs every POLL_INTERVAL minutes (default 15). For each open drift position:
  1. Fetch current YES price from PM CLOB public endpoint.
  2. Derive NO price = 1 - YES price.
  3. Exit if:
       a. NO >= target_no_price  (+20% → take profit)
       b. NO <= stop_no_price    (-15% → stop loss)
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
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../ingest/.env"))

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
log = logging.getLogger("drift-mon")

handler = logging.FileHandler(os.path.join(os.path.dirname(__file__), "drift_monitor.log"))
handler.setFormatter(logging.Formatter("%(asctime)s [drift-mon] %(message)s", "%H:%M:%S"))
log.addHandler(handler)


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _fetch_yes_price(token_id: str) -> float | None:
    """Public CLOB endpoint — no auth needed."""
    try:
        resp = requests.get(
            "https://clob.polymarket.com/price",
            params={"token_id": token_id, "side": "BUY"},
            timeout=6,
        )
        if resp.ok:
            p = resp.json().get("price")
            return float(p) if p is not None else None
    except Exception:
        pass
    return None


def _open_positions(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT id, token_id, home, away, outcome_key,
                  entry_yes_price, entry_no_price, size_shares,
                  target_no_price, stop_no_price, hard_close_minutes,
                  kickoff_at, stake_usd
           FROM drift_positions
           WHERE status = 'open'
           ORDER BY kickoff_at"""
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _close_position(conn, pos_id: int, yes_price: float, reason: str, dry_run=False):
    no_price = round(1.0 - yes_price, 4)
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
        f"NO {entry_no:.3f}→{no_price:.3f} | "
        f"pnl={'+' if pnl_pct>=0 else ''}{pnl_pct*100:.1f}% / "
        f"{'+'if pnl_usd>=0 else ''}{pnl_usd:.2f}$"
    )

    if dry_run:
        return

    cur.execute(
        """UPDATE drift_positions SET
               status=%s, exit_at=NOW(), exit_yes_price=%s, exit_no_price=%s,
               exit_reason=%s, realized_pnl_pct=%s, realized_pnl_usd=%s,
               updated_at=NOW()
           WHERE id=%s""",
        ("closed", yes_price, no_price, reason, pnl_pct, pnl_usd, pos_id),
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

    yes_price = _fetch_yes_price(token_id)

    # ── Safety net: kickoff already passed ──────────────────────────────
    # Drift is pre-match only. Anything sitting past kickoff is a
    # monitor failure that we must close immediately regardless of price.
    if mins_to_kick <= 0:
        if yes_price is not None:
            log.info(f"  [{pos['id']}] {pos['home']} v {pos['away']} — PAST KICKOFF, force-close at NO {1-yes_price:.3f}")
            _close_position(conn, pos["id"], yes_price, "kickoff_post", dry_run)
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

    if yes_price is None:
        log.warning(f"  [{pos['id']}] {pos['home']} v {pos['away']} — could not fetch price, skipping")
        return False

    no_price  = round(1.0 - yes_price, 4)
    drift_pct = round((no_price - entry_no) / entry_no * 100, 1)

    log.info(
        f"  [{pos['id']}] {pos['home']} v {pos['away']} — {pos['outcome_key']} | "
        f"YES {float(pos['entry_yes_price']):.3f}→{yes_price:.3f} | "
        f"NO {entry_no:.3f}→{no_price:.3f} ({drift_pct:+.1f}%) | "
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
        _close_position(conn, pos["id"], yes_price, reason, dry_run)
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
