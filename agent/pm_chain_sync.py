"""
Polymarket on-chain position sync.

Pulls every position currently held by the trading wallet from the Polymarket
data API (/v2/positions, through pm_data: v1 has answered 410 since 2026-11-02)
and reconciles it with paper_trades:

  - If a paper_trade row exists for the same pm_token_id and is pm_live, we
    refresh pm_size_matched + closing_price + flip pm_order_status to
    'matched' if the position is filled.

  - Orphan positions (held on-chain but with no matching paper_trade — e.g.
    a manual bet you placed yourself in the PM UI) are inserted as
    strategy_id=9 'Live Polymarket'.

  - We also sanity-check the size via a raw eth_call to the CTF contract on
    Polygon. If the API and chain disagree by more than 0.01 shares we log
    a warning (but still trust the chain).

Run as:
    python agent/pm_chain_sync.py             # live writes
    python agent/pm_chain_sync.py --dry-run   # log only

Cron: every 15 min.
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
import psycopg2.extras
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

import pm_data  # noqa: E402

WALLET = "0x4fE6F5E78093925fa2D446c26b59cAA71558BB5c"
GAMMA_API = "https://gamma-api.polymarket.com"
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Polymarket CTF on Polygon
LIVE_STRATEGY_ID = 9

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pm_chain_sync] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── PM data API ─────────────────────────────────────────────────────────────

def fetch_positions(wallet: str) -> list[dict]:
    """Every position the wallet still holds (v2's default status, as v1's
    /positions served), walked by cursor. v2 rows are snake_case: `token_id`
    for v1's `asset`, `current_size` for `size`, `current_price`, `avg_price`,
    `entry_cost_usdc` (v1 initialValue), `current_value`, `unrealized_pnl` (v1
    cashPnl, current value − entry cost). Raises when the walk failed: a
    refused read must not look like a wallet that holds nothing."""
    got = pm_data.walk("positions", {"user": wallet}, limit=500)
    if got is None:
        raise RuntimeError("data-api /v2/positions did not answer: nothing synced")
    rows, complete = got
    if not complete:
        raise RuntimeError("data-api /v2/positions walk did not finish: nothing synced")
    return rows


def pnl_pct(pos: dict) -> float:
    """v1's percentPnl: unrealized P&L over entry cost, in percent. v2's own
    percent_pnl counts fees and realized P&L, a different number."""
    cost = float(pos.get("entry_cost_usdc") or 0)
    return 100.0 * float(pos.get("unrealized_pnl") or 0) / cost if cost else 0.0


# ─── Polygon raw RPC (no web3 dependency) ────────────────────────────────────

def chain_balance(wallet: str, token_id_str: str) -> Optional[float]:
    """
    CTF.balanceOf(wallet, tokenId) via raw eth_call. Returns shares as float
    (PM shares are 6 decimals like USDC). None on error.
    """
    # function selector for balanceOf(address,uint256) → keccak4 = 0x00fdd58e
    selector = "00fdd58e"
    addr_hex = wallet.lower().replace("0x", "").rjust(64, "0")
    token_hex = hex(int(token_id_str))[2:].rjust(64, "0")
    data = "0x" + selector + addr_hex + token_hex

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": CTF_CONTRACT, "data": data}, "latest"],
    }
    try:
        r = requests.post(POLYGON_RPC, json=payload, timeout=15)
        r.raise_for_status()
        resp = r.json()
        if "result" not in resp:
            return None
        # CTF token balance is in 6 decimals (USDC-style)
        return int(resp["result"], 16) / 1e6
    except Exception as e:
        log.debug(f"chain_balance failed for token {token_id_str[:12]}…: {e}")
        return None


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def fetch_known_trades(conn) -> dict[str, dict]:
    """Map pm_token_id → paper_trade row (only live ones)."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id, strategy_id, market_id, outcome, entry_price, stake_units,
               pm_token_id, pm_order_id, pm_order_status, pm_size_matched,
               pm_order_size, pm_order_price, pm_live, result
        FROM paper_trades
        WHERE pm_token_id IS NOT NULL
        """
    )
    return {r["pm_token_id"]: dict(r) for r in cur.fetchall()}


def upsert_pm_market(conn, condition_id: str, title: str, end_date_str: Optional[str]) -> Optional[int]:
    """Insert pm_markets row if missing; return id."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM pm_markets WHERE external_id = %s", (condition_id,))
    row = cur.fetchone()
    if row:
        return row[0]

    end_dt = None
    if end_date_str:
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        except Exception:
            end_dt = None

    cur.execute(
        """
        INSERT INTO pm_markets (platform, external_id, title, resolution_time, status)
        VALUES ('polymarket', %s, %s, %s, 'open')
        RETURNING id
        """,
        (condition_id, title, end_dt),
    )
    return cur.fetchone()[0]


def update_known_trade(conn, trade_id: int, *, size_matched: float,
                       cur_price: float, status: str,
                       current_value: float, cash_pnl: float, percent_pnl: float) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE paper_trades
        SET pm_size_matched   = %s,
            closing_price     = %s,
            pm_order_status   = %s,
            pm_current_value  = %s,
            pm_cash_pnl       = %s,
            pm_percent_pnl    = %s,
            pm_last_checked_at = NOW()
        WHERE id = %s
        """,
        (round(size_matched, 4), round(cur_price, 6), status,
         round(current_value, 6), round(cash_pnl, 6), round(percent_pnl, 4),
         trade_id),
    )


def insert_orphan(conn, pos: dict, market_db_id: int) -> int:
    """Insert a manual / unknown PM position as strategy_id=9."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO paper_trades (
            strategy_id, market_id, outcome,
            entry_price, entry_odds, stake_units,
            model_probability, expected_edge, confidence,
            reasoning, placed_at, signed_timestamp_proof,
            pm_token_id, pm_order_status, pm_size_matched,
            pm_order_size, pm_order_price, pm_live, pm_executed_at,
            closing_price, pm_current_value, pm_cash_pnl, pm_percent_pnl
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s,
            NULL, NULL, NULL,
            %s, NOW(), %s,
            %s, 'matched', %s,
            %s, %s, TRUE, NOW(),
            %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            LIVE_STRATEGY_ID, market_db_id, pos["outcome"].lower(),
            float(pos["avg_price"]),
            (1.0 / float(pos["avg_price"])) if float(pos["avg_price"]) > 0 else None,
            round(float(pos["entry_cost_usdc"]), 4),
            f"Orphan on-chain position discovered by pm_chain_sync (manual bet or pre-agent trade). PM eventSlug={pos.get('event_slug')}.",
            f"chain_sync:{pos['token_id'][:12]}",
            pos["token_id"],
            float(pos["current_size"]),
            float(pos["current_size"]),
            float(pos["avg_price"]),
            float(pos["current_price"]),
            float(pos.get("current_value") or 0),
            float(pos.get("unrealized_pnl") or 0),
            pnl_pct(pos),
        ),
    )
    return cur.fetchone()[0]


# ─── Main ────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, verify_chain: bool = True) -> dict:
    log.info(f"Fetching positions for {WALLET}…")
    positions = fetch_positions(WALLET)
    log.info(f"PM data API returned {len(positions)} positions")

    conn = _conn()
    known = fetch_known_trades(conn)

    n_updated = 0
    n_orphan = 0
    n_chain_mismatch = 0

    for pos in positions:
        token_id = pos["token_id"]
        size_api = float(pos["current_size"])
        cur_price = float(pos["current_price"])

        chain_size = None
        if verify_chain:
            chain_size = chain_balance(WALLET, token_id)
            if chain_size is not None and abs(chain_size - size_api) > 0.01:
                log.warning(
                    f"  chain/API mismatch on {token_id[:14]}…: "
                    f"api={size_api:.4f} chain={chain_size:.4f}"
                )
                n_chain_mismatch += 1

        match = known.get(token_id)
        if match:
            status = "matched" if size_api > 0 else (match.get("pm_order_status") or "live")
            cash_pnl = float(pos.get("unrealized_pnl") or 0)
            cur_value = float(pos.get("current_value") or 0)
            pct_pnl = pnl_pct(pos)
            log.info(
                f"  #{match['id']:>4} {pos['title'][:55]:55s} "
                f"size={size_api:.2f} @{cur_price:.3f} pnl={cash_pnl:+.2f} ({pct_pnl:+.1f}%) → {status}"
            )
            if not dry_run:
                update_known_trade(
                    conn, match["id"],
                    size_matched=size_api,
                    cur_price=cur_price,
                    status=status,
                    current_value=cur_value,
                    cash_pnl=cash_pnl,
                    percent_pnl=pct_pnl,
                )
            n_updated += 1
        else:
            # Manual bet — insert under strategy 9
            log.info(
                f"  ORPHAN: {pos['title'][:55]:55s} | {pos['outcome']:5s} "
                f"size={size_api:.2f} @avg{float(pos['avg_price']):.3f} cur={cur_price:.3f} "
                f"pnl={float(pos.get('unrealized_pnl') or 0):+.2f}"
            )
            if not dry_run:
                market_db_id = upsert_pm_market(
                    conn, pos["condition_id"], pos["title"], pos.get("end_date")
                )
                if market_db_id:
                    new_id = insert_orphan(conn, pos, market_db_id)
                    log.info(f"    → orphan inserted as paper_trade #{new_id} (strategy 9)")
                    n_orphan += 1

    if not dry_run:
        conn.commit()

    summary = {
        "positions_on_chain": len(positions),
        "trades_updated": n_updated,
        "orphans_ingested": n_orphan,
        "chain_api_mismatches": n_chain_mismatch,
        "wallet": WALLET,
        "dry_run": dry_run,
    }
    log.info(json.dumps(summary, indent=2))
    conn.close()
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-chain-verify", action="store_true",
                    help="Skip Polygon eth_call sanity check (faster)")
    args = ap.parse_args()
    run(dry_run=args.dry_run, verify_chain=not args.no_chain_verify)


if __name__ == "__main__":
    main()
