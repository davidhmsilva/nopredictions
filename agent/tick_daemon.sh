#!/usr/bin/env bash
# tick_daemon.sh — keep tick_recorder.py alive 24/7.
# Football books are only interesting while matches are on, but the universe
# refresh is cheap and kickoff times span every timezone, so we just run always.
cd "$(dirname "$0")"
set -a; source ../ingest/.env; set +a
source ../ingest/.venv/bin/activate
while true; do
  python tick_recorder.py --interval 60 >> tick_recorder.log 2>&1
  echo "$(date -u +%FT%TZ) recorder exited — restarting in 30s" >> tick_recorder.log
  sleep 30
done
