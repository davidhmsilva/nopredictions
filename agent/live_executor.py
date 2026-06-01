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
# Take the ask to guarantee a fill, but never pay more than this above the mid.
MAX_SLIPPAGE_PP = float(os.environ.get("PM_MAX_SLIPPAGE_PP", "2.0"))
# Require the edge to survive at the price we actually execute at (not the mid).
# Legacy fallback floor used only when no fair_prob is supplied (refined engine off).
MIN_EXEC_EDGE_PP = float(os.environ.get("PM_MIN_EXEC_EDGE_PP", "3.0"))

# ── Edge hardening (2026-06-01) ──────────────────────────────────────────────
# Live P&L audit: win rate collapses as the *claimed* model edge grows — the
# only profitable band is 5–10pp; everything >10pp bled (10-15pp −14.6u,
# 15-20pp −15.8u, >20pp −12.6u). Large model-vs-market gaps are model error,
# not edge. So the live money path is now gated by the sharp-anchored
# edge_engine, and unvalidated (model-only, no sharp line) edges are capped.
USE_REFINED_EDGE = os.environ.get("PM_USE_REFINED_EDGE", "1") == "1"
# Cap on the RAW edge for model-only (sharp-unvalidated) bets, in pp. Edges
# above this with no sharp line to confirm them are refused. Sharp-validated
# (consensus) edges are exempt — the sharp already bounded them.
MAX_MODEL_EDGE_PP = float(os.environ.get("PM_MAX_MODEL_EDGE_PP", "12.0"))
# Only look up the sharp line for matches kicking off within this many hours
# (bounds api-football calls; matches the scan window).
SHARP_LOOKUP_HOURS = float(os.environ.get("PM_SHARP_LOOKUP_HOURS", "48"))
# Strategy ids that may NOT submit real orders (still logged as paper). No Bias
# (id 6): live yield −82%, p=0.000, Brier skill −2.7 — structurally a model-error
# harvester (fade favorites the sharp agrees with). Demoted to paper until it
# shows positive CLV. Comma-separated.
DISABLED_STRATEGY_IDS = {
    int(x) for x in os.environ.get("PM_DISABLED_STRATEGY_IDS", "6").split(",")
    if x.strip().isdigit()
}

# Defense-in-depth: never send real money to goals markets (totals + BTTS). The
# model overprices them with no sharp line to validate (2026-05-29 P&L audit:
# −24u). Scanners already skip them, but a stray promotion/manual path could still
# land here. Identified by the trade's `outcome` substring. Override with
# PM_ENABLE_GOALS_MARKETS=1.
_GOALS_MARKETS_ENABLED = os.environ.get("PM_ENABLE_GOALS_MARKETS") == "1"


def _is_goals_outcome(outcome: str) -> bool:
    o = (outcome or "").lower()
    return ("btts" in o or "both teams" in o
            or "over_" in o or "under_" in o
            or "o/u" in o or " over " in o or " under " in o)


def _lookup_sharp_prob(home, away, kickoff_date, outcome_key) -> Optional[float]:
    """De-vigged Pinnacle prob for this side, or None. Best-effort: any failure
    (no fixture, market not covered, api budget, import error) → None, which
    makes the refined engine fall back to its stricter model-only branch."""
    if not (home and away and outcome_key):
        return None
    try:
        import sharp_odds  # lazy: pulls in closing_collector / api-football
        date_str = None
        if kickoff_date is not None:
            date_str = kickoff_date.isoformat() if hasattr(kickoff_date, "isoformat") else str(kickoff_date)[:10]
        return sharp_odds.sharp_prob(home, away, date_str, outcome_key)
    except Exception as exc:  # never let a sharp-lookup failure block the path
        log.debug(f"[live_executor] sharp lookup failed: {exc}")
        return None


def refined_decision(fair_prob, exec_price, sharp_prob, sim_se):
    """Run the sharp-anchored edge engine + the live model-only cap.
    Returns (ok: bool, reason: str)."""
    import edge_engine
    res = edge_engine.compute_edge(
        model_prob=fair_prob, exec_price=exec_price,
        sharp_prob=sharp_prob, sim_se=sim_se,
    )
    if not res.bet_ok:
        return False, f"refined: {res.reason}"
    # Tighter-than-implausibility cap on unvalidated edges: the 10–20pp band is
    # where the model is wrong, not the market.
    if res.confidence == "model" and res.edge_raw_pp > MAX_MODEL_EDGE_PP:
        return False, (f"refined: model-only raw edge {res.edge_raw_pp:+.1f}pp "
                       f"> {MAX_MODEL_EDGE_PP:.0f}pp cap, no sharp to validate")
    return True, f"refined OK: {res.reason}"

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
    ask: Optional[float] = None,
    fair_prob: Optional[float] = None,
    stake_usd: Optional[float] = None,
    home: Optional[str] = None,
    away: Optional[str] = None,
    kickoff_date=None,
    outcome_key: Optional[str] = None,
    sim_se: Optional[float] = None,
    dry_run: bool = False,
) -> LiveResult:
    """
    Submit a marketable limit order to guarantee a fill, if all guards pass.

    `price`     reference mid (legacy execution price / fallback).
    `ask`       executable price we cross to (the best ask for this side). When
                given we EXECUTE AT THE ASK so the order fills immediately instead
                of resting at the mid — capped by MAX_SLIPPAGE_PP above the mid.
    `fair_prob` our model prob for this side. With the refined engine on (default),
                this is anchored against the de-vigged sharp line and the edge must
                survive at the executable price; unvalidated edges are capped.
    `home/away/kickoff_date/outcome_key` context for the sharp-line lookup.
    `sim_se`    MC standard error of fair_prob, for the uncertainty haircut.

    Omitting ask/fair_prob preserves the legacy behaviour (post a GTC limit at
    `price`), so existing callers keep working unchanged.
    """
    stake = stake_usd if stake_usd is not None else LIVE_STAKE_USD

    # Pick the execution price: take the ask to guarantee a fill, but never pay
    # more than MAX_SLIPPAGE_PP above the mid.
    exec_price = price
    slippage_skip = None
    if ask is not None and ask > 0:
        if ask > price + MAX_SLIPPAGE_PP / 100.0:
            slippage_skip = (f"ask {ask:.3f} exceeds mid {price:.3f} "
                             f"+ {MAX_SLIPPAGE_PP}pp slippage cap")
        else:
            exec_price = ask

    # Edge gate at the executable price. Refined (sharp-anchored) when we have a
    # fair_prob; otherwise the legacy naive floor. Computed after the cheap guards
    # below (sharp lookup can hit api-football) — see edge_skip use.
    edge_skip = None
    if fair_prob is not None and exec_price > 0 and not USE_REFINED_EDGE:
        edge_vs_exec = (fair_prob - exec_price) * 100.0
        if edge_vs_exec < MIN_EXEC_EDGE_PP:
            edge_skip = (f"edge {edge_vs_exec:+.1f}pp at exec {exec_price:.3f} "
                         f"< {MIN_EXEC_EDGE_PP}pp min")

    size = max(MIN_SHARES, round(stake / exec_price, 2)) if exec_price > 0 else 0.0
    notional = round(size * exec_price, 4)  # actual $ at risk = shares × price

    r = LiveResult(
        trade_id=trade_id,
        pm_order_id=None,
        pm_order_status="paper",
        pm_token_id=token_id,
        pm_order_size=size,
        pm_order_price=exec_price,
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

    # One lookup for the trade's outcome + strategy (used by the goals + strategy
    # guards below).
    cur = conn.cursor()
    cur.execute("SELECT outcome, strategy_id FROM paper_trades WHERE id = %s", (trade_id,))
    row = cur.fetchone()
    cur.close()
    trade_outcome = row[0] if row else (outcome_key or "")
    trade_strategy_id = row[1] if row else None

    # Strategy guard: some strategies are paper-only on the live venue.
    if trade_strategy_id in DISABLED_STRATEGY_IDS:
        r.pm_order_status = "skipped"
        r.pm_order_error = f"strategy {trade_strategy_id} disabled for live (paper-only)"
        log.info(f"[live_executor] trade #{trade_id} skipped — {r.pm_order_error}")
        _persist(conn, r, live=False)
        return r

    # Goals-market guard: never send real money to totals/BTTS (model overprices,
    # no sharp validation).
    if not _GOALS_MARKETS_ENABLED and _is_goals_outcome(trade_outcome):
        r.pm_order_status = "skipped"
        r.pm_order_error = "goals market disabled (totals/BTTS)"
        log.info(f"[live_executor] trade #{trade_id} skipped — {r.pm_order_error}")
        _persist(conn, r, live=False)
        return r

    # Refined edge gate (sharp-anchored consensus + uncertainty haircut + model-only
    # cap). Runs here, after the cheap guards, so the sharp lookup (api-football)
    # only fires for orders that could actually go live. Skips if no fair_prob.
    if USE_REFINED_EDGE and fair_prob is not None and exec_price > 0:
        sp = None
        if SHARP_LOOKUP_HOURS > 0:
            sp = _lookup_sharp_prob(home, away, kickoff_date, outcome_key or trade_outcome)
        ok, reason = refined_decision(fair_prob, exec_price, sp, sim_se)
        if not ok:
            edge_skip = reason

    # Don't cross a spread wider than our slippage cap.
    if slippage_skip:
        r.pm_order_status = "skipped"
        r.pm_order_error = slippage_skip
        log.info(f"[live_executor] trade #{trade_id} skipped — {slippage_skip}")
        _persist(conn, r, live=False)
        return r

    # Don't take a price where our edge has evaporated.
    if edge_skip:
        r.pm_order_status = "skipped"
        r.pm_order_error = edge_skip
        log.info(f"[live_executor] trade #{trade_id} skipped — {edge_skip}")
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

    place_resp = _run_pm(["place_json", token_id, side.upper(), str(exec_price), str(size)], timeout=60)
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
