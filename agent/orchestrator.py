"""
Orchestrator — main control loop.

Runs the two trading strategies (consensus + poisson) and the resolver.
No LLM needed — both strategies are deterministic.
"""

from __future__ import annotations

import time

from . import paper_trader, resolver
from .tools.db import log_agent_run

# Lazy import — poisson_trader may not exist yet during initial restructure
_poisson_trader = None


def _get_poisson_trader():
    global _poisson_trader
    if _poisson_trader is None:
        try:
            from . import poisson_trader
            _poisson_trader = poisson_trader
        except ImportError:
            pass
    return _poisson_trader


def run_once(strategies: list[str] | None = None, dry_run: bool = False) -> dict:
    """
    Run one cycle: selected strategies + resolver.
    If strategies is None, run all.
    """
    print("\n" + "=" * 60)
    print("[orchestrator] Starting cycle")
    print("=" * 60)

    result = {}

    if strategies is None or 'consensus' in strategies:
        print("[orchestrator] Running PM-vs-Sharp Consensus scan...")
        trades = paper_trader.run(dry_run=dry_run)
        result['consensus'] = trades or []
        if trades:
            print(f"[orchestrator] {len(trades)} consensus edge(s) found")

    if strategies is None or 'poisson' in strategies:
        pt = _get_poisson_trader()
        if pt:
            print("[orchestrator] Running PM-vs-Poisson In-Play scan...")
            trades = pt.run(dry_run=dry_run)
            result['poisson'] = trades or []
            if trades:
                print(f"[orchestrator] {len(trades)} poisson edge(s) found")
        else:
            print("[orchestrator] Poisson trader not available, skipping")
            result['poisson'] = []

    print("[orchestrator] Running resolver...")
    resolved = resolver.run()
    result['resolved'] = resolved or []

    n_consensus = len(result.get('consensus', []))
    n_poisson = len(result.get('poisson', []))
    n_resolved = len(result.get('resolved', []))

    log_agent_run(
        'orchestrator', 'cycle',
        f"consensus={n_consensus} poisson={n_poisson} resolved={n_resolved}",
    )

    print(f"[orchestrator] Cycle complete: "
          f"{n_consensus} consensus, {n_poisson} poisson, {n_resolved} resolved\n")
    return result


def run_loop(max_cycles: int = 1, interval: int = 0,
             strategies: list[str] | None = None, dry_run: bool = False) -> None:
    """Run the orchestration loop for N cycles with optional interval between them."""
    for i in range(max_cycles):
        print(f"\n{'=' * 60}")
        print(f"CYCLE {i + 1}/{max_cycles}")
        run_once(strategies=strategies, dry_run=dry_run)

        if interval and i < max_cycles - 1:
            print(f"[orchestrator] Sleeping {interval}s until next cycle...")
            time.sleep(interval)
