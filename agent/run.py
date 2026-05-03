#!/usr/bin/env python3
"""
NOPREDICTIONS Agent — CLI entry point.

Usage:
    # Run one full cycle (both strategies + resolver)
    python run.py

    # Run only pre-match consensus scan
    python run.py --strategy consensus

    # Run only in-play Poisson scan
    python run.py --strategy poisson

    # Run 5 cycles, 15 min apart (in-play daemon mode)
    python run.py --cycles 5 --interval 900

    # Dry run (no DB writes)
    python run.py --dry-run
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / 'ingest' / '.env')

if not os.getenv('ANTHROPIC_API_KEY'):
    print("WARNING: ANTHROPIC_API_KEY not set (not needed for current strategies).")

from agent import orchestrator


def main():
    parser = argparse.ArgumentParser(description='NOPREDICTIONS Trading Agent')
    parser.add_argument('--strategy', choices=['consensus', 'poisson'],
                        help='Run only a specific strategy (default: all)')
    parser.add_argument('--cycles', type=int, default=1,
                        help='Number of cycles to run (default: 1)')
    parser.add_argument('--interval', type=int, default=0,
                        help='Seconds between cycles (default: 0, for in-play daemon use 900)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print decisions without DB writes')
    args = parser.parse_args()

    if args.dry_run:
        print("[run.py] DRY RUN — no DB writes")

    strategies = [args.strategy] if args.strategy else None

    if args.cycles == 1 and args.interval == 0:
        orchestrator.run_once(strategies=strategies, dry_run=args.dry_run)
    else:
        orchestrator.run_loop(
            max_cycles=args.cycles,
            interval=args.interval,
            strategies=strategies,
            dry_run=args.dry_run,
        )


if __name__ == '__main__':
    main()
