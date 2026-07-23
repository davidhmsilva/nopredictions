#!/usr/bin/env bash
# flb_daemon.sh — one FLB cycle: scan for new positions, then settle what resolved.
#
# The cadence guard used to live here; it now lives in cron_guard.sh so that every
# sleep-fragile job in this project shares one mechanism. Cron calls:
#
#   */10 * * * * agent/cron_guard.sh --gap 110 flb agent/flb_daemon.sh
#
# Run it by hand any time — it is idempotent (the scanner dedupes against open
# positions, the eval only writes settlements it has not written).
cd "$(dirname "$0")" || exit 1

set -a; source ../ingest/.env; set +a
source ../ingest/.venv/bin/activate

echo "$(date -u +%FT%TZ) --- flb cycle ---" >> flb_scanner.log
python flb_scanner.py >> flb_scanner.log 2>&1
python flb_eval.py --write >> flb_eval.log 2>&1
