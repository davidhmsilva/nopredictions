#!/usr/bin/env python3
"""
live_scan.py — run all live-eligible strategies in one shot.

Strategies:
  1. DC Model Pre-Match  — draw + home-underdog (yes_p <= 0.45)
  2. Poisson In-Play     — draw + btts, edge outside 8-12pp dead zone (Filter F)

Usage:
  python live_scan.py            # scan + place real orders if PM_LIVE_MODE=1
  python live_scan.py --dry-run  # scan only, no orders
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv
load_dotenv(REPO / "ingest" / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("live_scan")

LIVE_MODE = os.environ.get("PM_LIVE_MODE", "0") == "1"


def _banner(title: str) -> None:
    log.info("")
    log.info("═" * 60)
    log.info(f"  {title}")
    log.info("═" * 60)


def run_dc_scanner(dry_run: bool) -> dict:
    """Run DC pre-match scanner (draw + home-underdog live filter)."""
    _banner("DC Model Pre-Match  [draw + home-underdog]")
    try:
        import dc_scanner
        result = dc_scanner.run(days_ahead=3, threshold_pp=3.0, dry_run=dry_run)
        edges = result.get("dc_edges_found", 0)
        trades = result.get("dc_trades_logged", 0)
        nb_trades = result.get("no_bias_trades_logged", 0)
        log.info(f"  DC: {edges} edges → {trades} DC trades, {nb_trades} No-Bias trades")
        return result
    except Exception as e:
        log.error(f"  DC scanner error: {e}")
        return {}


def run_poisson(dry_run: bool) -> list:
    """Run Poisson in-play scanner (Draw+BTTS, Filter F)."""
    _banner("Poisson In-Play  [Draw + BTTS, skip 8-12pp dead zone]")
    try:
        from agent import poisson_trader
        trades = poisson_trader.run(dry_run=dry_run)
        log.info(f"  Poisson: {len(trades)} in-play edge(s) found")
        return trades
    except Exception as e:
        log.error(f"  Poisson scanner error: {e}")
        return []


def main() -> None:
    ap = argparse.ArgumentParser(description="Run all live-eligible strategies")
    ap.add_argument("--dry-run", action="store_true", help="scan only, no real orders")
    args = ap.parse_args()

    dry_run = args.dry_run or not LIVE_MODE

    log.info("")
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║        NOPREDICTIONS — LIVE SCAN                        ║")
    log.info(f"║  PM_LIVE_MODE={'ON ' if LIVE_MODE else 'OFF'}  |  {'DRY RUN' if dry_run else 'REAL MONEY'}                          ║")
    log.info("╚══════════════════════════════════════════════════════════╝")

    t0 = time.time()

    dc_result = run_dc_scanner(dry_run=dry_run)
    poisson_trades = run_poisson(dry_run=dry_run)

    elapsed = time.time() - t0

    _banner(f"SUMMARY  ({elapsed:.0f}s)")
    dc_edges = dc_result.get("dc_edges_found", 0)
    dc_trades = dc_result.get("dc_trades_logged", 0)
    dc_nb = dc_result.get("no_bias_trades_logged", 0)
    log.info(f"  DC Pre-Match:    {dc_edges} edges  →  {dc_trades} DC + {dc_nb} No-Bias trades")
    log.info(f"  Poisson In-Play: {len(poisson_trades)} edge(s) found")
    if not LIVE_MODE or dry_run:
        log.info("")
        log.info("  ⚠  No real orders sent (set PM_LIVE_MODE=1 to go live)")
    else:
        log.info("")
        log.info("  ✅ Real orders submitted where filters passed")
    log.info("")


if __name__ == "__main__":
    main()
