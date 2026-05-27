"""
Live Polymarket order submission, gated by env flag.

Calls polymarket_client.py via subprocess (it lives in a different venv with
modern Python because py-clob-client-v2 needs >=3.9.10 but the agent's main
venv is 3.9.6). The subprocess writes a single JSON line to stdout that we
parse here.

PM_LIVE_MODE=1  → actually submit orders
PM_LIVE_MODE=0  → no-op (paper mode), just mark trade as paper

Env knobs:
  PM_LIVE_MODE                 0|1
  PM_LIVE_STAKE_USD            1.0
  PM_MAX_NOTIONAL_PER_ORDER    5.0
  PM_BALANCE_HEADROOM          1.1
  PM_MIN_SHARES                5
  PM_VENV_PYTHON               /path/to/.venv-pm/bin/python  (default: repo .venv-pm)

Note: there is NO daily cap. Free balance + per-order cap are the only guards.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

LIVE_MODE = os.environ.get("PM_LIVE_MODE", "0") == "1"
LIVE_STAKE_USD = float(os.environ.get("PM_LIVE_STAKE_USD", "1.0"))
MAX_NOTIONAL_PER_ORDER = float(os.environ.get("PM_MAX_NOTIONAL_PER_ORDER", "5.0"))
BALANCE_HEADROOM = float(os.environ.get("PM_BALANCE_HEADROOM", "1.1"))
MIN_SHARES = float(os.environ.get("PM_MIN_SHARES", "5"))

_REPO = Path(__file__).resolve().parent.parent
PM_VENV_PYTHON = os.environ.get("PM_VENV_PYTHON") or str(_REPO / ".venv-pm" / "bin" / "python")
PM_CLIENT_SCRIPT = str(_REPO / "agent" / "polymarket_client.py")


@dataclass
class LiveResult:
    trade_id: int
    pm_order_id: Optional[str]
    pm_order_status: str
    pm_token_id: Optional[str]
    pm_order_size: Optional[float]
    pm_order_price: Optional[float]
    pm_order_error: Optional[str]
    notional: float


def _run_pm(args: list[str], timeout: int = 30) -> dict:
    """Invoke polymarket_client.py in the PM venv and parse the single JSON line."""
    cmd = [PM_VENV_PYTHON, PM_CLIENT_SCRIPT, *args]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    # The script prints exactly one JSON line. Tolerate extra warnings on stderr.
    stdout = (out.stdout or "").strip().splitlines()
    if not stdout:
        return {"ok": False, "error": f"empty stdout. stderr={out.stderr[-300:]}"}
    try:
        return json.loads(stdout[-1])
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"bad json: {e}. raw={stdout[-1][:200]}"}


def try_execute(
    conn,
    *,
    trade_id: int,
    token_id: Optional[str],
    side: str,
    price: float,
    stake_usd: Optional[float] = None,
    dry_run: bool = False,
) -> LiveResult:
    """Submit a GTC limit order if all guards pass; persist execution metadata."""
    stake = stake_usd if stake_usd is not None else LIVE_STAKE_USD
    size = max(MIN_SHARES, round(stake / price, 2)) if price > 0 else 0.0
    notional = round(size * price, 4)  # actual $ at risk = shares × price

    r = LiveResult(
        trade_id=trade_id,
        pm_order_id=None,
        pm_order_status="paper",
        pm_token_id=token_id,
        pm_order_size=size,
        pm_order_price=price,
        pm_order_error=None,
        notional=notional,
    )

    if not LIVE_MODE or dry_run:
        log.info(f"[live_executor] trade #{trade_id} paper-only (LIVE_MODE={LIVE_MODE}, dry={dry_run})")
        _persist(conn, r, live=False)
        return r

    if not token_id:
        r.pm_order_status = "skipped"
        r.pm_order_error = "no token_id"
        log.warning(f"[live_executor] trade #{trade_id} skipped — no token_id")
        _persist(conn, r, live=False)
        return r

    if notional > MAX_NOTIONAL_PER_ORDER:
        r.pm_order_status = "skipped"
        r.pm_order_error = f"notional > cap (${MAX_NOTIONAL_PER_ORDER:.2f})"
        log.warning(f"[live_executor] trade #{trade_id} {r.pm_order_error}")
        _persist(conn, r, live=False)
        return r

    # Cross-strategy dedup: skip if another paper_trade already has a live/matched
    # order on the same token (avoids two strategies stacking on the same outcome).
    cur = conn.cursor()
    cur.execute(
        """SELECT id, strategy_id, pm_order_status
           FROM paper_trades
           WHERE pm_token_id = %s
             AND pm_live = TRUE
             AND pm_order_status IN ('live', 'matched')
             AND id <> %s
           LIMIT 1""",
        (token_id, trade_id),
    )
    dup = cur.fetchone()
    if dup:
        r.pm_order_status = "skipped"
        r.pm_order_error = f"dedup: trade #{dup[0]} (strategy {dup[1]}) already {dup[2]} on this token"
        log.info(f"[live_executor] trade #{trade_id} {r.pm_order_error}")
        _persist(conn, r, live=False)
        return r

    # Retry safety: don't allow a retry to balloon exposure beyond the original commitment.
    cur.execute(
        """SELECT COALESCE(pm_order_size * pm_order_price, 0)
           FROM paper_trades
           WHERE id = %s
             AND COALESCE(pm_executed_at, placed_at) >= date_trunc('day', NOW())""",
        (trade_id,),
    )
    row = cur.fetchone()
    existing = float(row[0]) if row else 0.0
    if existing > 0 and notional > existing * 1.25 + 0.10:
        r.pm_order_status = "skipped"
        r.pm_order_error = f"retry would inflate exposure ${existing:.2f}→${notional:.2f}"
        log.warning(f"[live_executor] trade #{trade_id} {r.pm_order_error}")
        _persist(conn, r, live=False)
        return r

    if not Path(PM_VENV_PYTHON).exists():
        r.pm_order_status = "failed"
        r.pm_order_error = f"PM venv python missing: {PM_VENV_PYTHON}"
        log.error(f"[live_executor] {r.pm_order_error}")
        _persist(conn, r, live=False)
        return r

    bal_resp = _run_pm(["balance_json"])
    if not bal_resp.get("ok"):
        r.pm_order_status = "failed"
        r.pm_order_error = f"balance check failed: {bal_resp.get('error')}"
        log.error(f"[live_executor] {r.pm_order_error}")
        _persist(conn, r, live=False)
        return r
    bal = float(bal_resp["balance_usd"])
    if bal < notional * BALANCE_HEADROOM:
        r.pm_order_status = "skipped"
        r.pm_order_error = f"balance ${bal:.2f} below ${notional * BALANCE_HEADROOM:.2f}"
        log.warning(f"[live_executor] trade #{trade_id} {r.pm_order_error}")
        _persist(conn, r, live=False)
        return r

    place_resp = _run_pm(["place_json", token_id, side.upper(), str(price), str(size)], timeout=60)
    if not place_resp.get("ok"):
        r.pm_order_status = "failed"
        r.pm_order_error = str(place_resp.get("error"))[:500]
        log.error(f"[live_executor] trade #{trade_id} place failed: {r.pm_order_error}")
        _persist(conn, r, live=True)
        return r

    resp = place_resp.get("resp") or {}
    r.pm_order_id = resp.get("orderID") or resp.get("order_id")
    r.pm_order_status = resp.get("status", "live")
    log.info(f"[live_executor] ✅ trade #{trade_id} → PM order {r.pm_order_id} ({r.pm_order_status})")
    _persist(conn, r, live=True)
    return r


def _persist(conn, r: LiveResult, *, live: bool) -> None:
    """Persist execution metadata.

    `live=True` means the trade has an active position or live order.
    `live=False` means it's paper / never went live.

    For a SKIPPED retry (we previously had a live order, current submit failed),
    preserve the existing pm_live so the checker can re-attempt next cycle.
    """
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE paper_trades SET
            pm_live          = CASE
                                  WHEN %s THEN TRUE
                                  WHEN pm_live = TRUE AND %s = 'skipped' THEN TRUE
                                  ELSE FALSE
                              END,
            pm_token_id      = %s,
            pm_order_id      = %s,
            pm_order_status  = %s,
            pm_order_size    = %s,
            pm_order_price   = %s,
            pm_order_error   = %s,
            pm_executed_at   = CASE WHEN %s AND %s IS NOT NULL THEN NOW() ELSE pm_executed_at END,
            pm_attempts      = pm_attempts + CASE WHEN %s AND %s IS NOT NULL THEN 1 ELSE 0 END
        WHERE id = %s
        """,
        (
            live,
            r.pm_order_status,
            r.pm_token_id,
            r.pm_order_id,
            r.pm_order_status,
            r.pm_order_size,
            r.pm_order_price,
            r.pm_order_error,
            live, r.pm_order_id,
            live, r.pm_order_id,
            r.trade_id,
        ),
    )
    conn.commit()
