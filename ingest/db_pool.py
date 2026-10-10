"""
Connections for bulk ingest jobs: Supavisor's TRANSACTION-mode port.

`DATABASE_URL` points at the session-mode port (5432), where every open client
holds one of only 15 backend slots for as long as it is connected. On
2026-10-03 all 15 were held by long-lived daemon sessions (idle up to 15h), so
a single extra client — one ingest job — was enough to refuse the agents'
crons with EMAXCONNSESSION.

Transaction mode (6543) multiplexes clients over the pool: a loader holds a
backend only while a transaction runs. These jobs keep no session state across
transactions (no SET, temp tables, LISTEN or server-side prepared statements),
which is the one thing transaction mode does not carry.
"""

from __future__ import annotations

import os


def ingest_url() -> str:
    url = os.environ['DATABASE_URL']
    return url.replace(':5432/', ':6543/')
