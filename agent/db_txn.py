"""Connections that cannot be left idle in a transaction, and a way to ask
for one on purpose.

WHY THIS EXISTS
---------------
psycopg2 defaults to ``autocommit = False``. That means the FIRST statement on
a connection — a bare ``SELECT`` included — opens a transaction that stays open
until something calls ``commit()`` or ``rollback()``. A long-lived daemon that
reads and then goes off to do something else is therefore holding a
transaction, and a transaction holds locks and pins a snapshot.

Measured on production 2026-09-08, while enabling RLS::

    pid 2403981 | state: idle in transaction | xact_age: 00:08:14
    query: SELECT fixture_id, minute, home_goals, away_goals
             FROM fav_ht_observations WHERE fixture_id = ANY(ARRAY...)

That is ``fav_pressure_agent.settle()``. It ran two SELECTs and then went into
a loop calling api-football over HTTP, once per pending fixture — **with the
read transaction still open the whole time**. It blocked
``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` through 120 retries over six
minutes, and it does something quieter and worse every day: an 8-minute-old
snapshot stops VACUUM reclaiming dead tuples newer than it, on tables that take
writes every cycle (``pressure_observations`` 761k rows and climbing).

Two separate bugs produced it, and both are the same shape:

1. **Network I/O inside a transaction.** The read has finished; Python has not.
2. **An early return that skips the commit.** ``settle()`` returns 0 when
   nothing is pending, and ``open_trades()`` ``continue``s past its
   ``conn.commit()`` when a fixture was already entered — both leaving the
   preceding SELECT's transaction open.

Sprinkling more ``commit()`` calls fixes today's two paths and not tomorrow's.
``autocommit = True`` fixes the class: every statement ends when it ends, no
transaction is ever left open by accident, and locks are released immediately.
Verified against this database: with autocommit on, ``get_transaction_status()``
is IDLE after a SELECT, and ``commit()``/``rollback()`` remain safe no-ops — so
the existing calls scattered through these modules keep working untouched.

What autocommit costs is the one thing it should not take away: two writes that
must land together. ``atomic()`` gives that back, explicitly, for the places
that actually need it — and its body must contain no network calls, which is
the rule this module exists to keep.
"""

from __future__ import annotations

import contextlib
from typing import Iterator

import psycopg2


def connect(dsn: str):
    """A connection that never sits idle inside a transaction.

    Use this everywhere instead of ``psycopg2.connect``. The only difference is
    ``autocommit``, and that difference is the whole point.
    """
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


@contextlib.contextmanager
def atomic(conn) -> Iterator[None]:
    """Run a block as one transaction on an autocommit connection.

    For the few places where two statements must both land or neither does —
    settling an observation and paying out its paper trade, say. On the way out
    it COMMITs, and on any exception it ROLLBACKs and re-raises.

    ⚠️ Never put a network call inside this block. Every millisecond spent here
       is a lock held and a snapshot pinned, which is the bug this module was
       written for. Fetch first, then open the transaction.
    """
    if conn.autocommit:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
        try:
            yield
        except Exception:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK")
            raise
        with conn.cursor() as cur:
            cur.execute("COMMIT")
        return

    # A connection that is already transactional needs no BEGIN — psycopg2 has
    # opened one for us. This branch keeps `atomic()` correct if it is ever
    # handed a connection from somewhere that has not adopted `connect()`.
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def in_transaction(conn) -> bool:
    """True when this connection is holding a transaction open.

    A cheap assertion for a daemon's main loop: if this is ever true at the top
    of a cycle, something opened a transaction and walked away, and the next
    thing the process does is sleep on it.
    """
    return conn.get_transaction_status() != psycopg2.extensions.TRANSACTION_STATUS_IDLE
