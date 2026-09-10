"""The connection must never be left idle inside a transaction.

These tests run against a fake psycopg2 connection rather than the real
database: the property being asserted is about WHEN a transaction is open, and
a fake can answer that at every instant, which a real connection under a live
daemon cannot.

The regression they pin is the one found in production on 2026-09-08 — a
transaction held open for eight minutes while `settle()` did HTTP, blocking a
schema change through 120 retries and stopping VACUUM on the busiest tables in
the schema. See db_txn.py.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_txn  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append(sql.strip().split()[0].upper())
        head = sql.strip().upper()
        if head.startswith("BEGIN"):
            self.conn.depth += 1
        elif head.startswith(("COMMIT", "ROLLBACK")):
            self.conn.depth -= 1
        elif not self.conn.autocommit:
            # A plain statement on a non-autocommit connection opens a
            # transaction implicitly — the behaviour the whole module is about.
            self.conn.depth = max(self.conn.depth, 1)


class FakeConn:
    """Just enough psycopg2 to answer 'is a transaction open right now?'."""

    def __init__(self, autocommit: bool = True):
        self.autocommit = autocommit
        self.depth = 0
        self.log: list[str] = []
        self.closed = False

    def cursor(self, **_kw):
        return FakeCursor(self)

    def commit(self):
        self.depth = 0

    def rollback(self):
        self.depth = 0

    def get_transaction_status(self):
        # 0 is psycopg2's TRANSACTION_STATUS_IDLE; 2 is INTRANS.
        return 0 if self.depth == 0 else 2


def test_atomic_commits_and_leaves_nothing_open():
    conn = FakeConn()
    with db_txn.atomic(conn):
        conn.cursor().execute("UPDATE t SET a = 1")
        assert db_txn.in_transaction(conn), "the block itself must be transactional"
    assert not db_txn.in_transaction(conn)
    assert conn.log == ["BEGIN", "UPDATE", "COMMIT"]


def test_atomic_rolls_back_and_reraises():
    conn = FakeConn()
    with pytest.raises(ValueError):
        with db_txn.atomic(conn):
            conn.cursor().execute("UPDATE t SET a = 1")
            raise ValueError("write failed halfway")
    assert not db_txn.in_transaction(conn), "a failed block must not stay open"
    assert conn.log == ["BEGIN", "UPDATE", "ROLLBACK"]


def test_plain_select_opens_nothing_under_autocommit():
    """The bug, stated directly.

    On a default psycopg2 connection a bare SELECT opens a transaction that
    outlives the read — and the daemon then goes off to do HTTP. Under
    autocommit it does not.
    """
    default = FakeConn(autocommit=False)
    default.cursor().execute("SELECT 1")
    assert db_txn.in_transaction(default), "this is what production was doing"

    fixed = FakeConn(autocommit=True)
    fixed.cursor().execute("SELECT 1")
    assert not db_txn.in_transaction(fixed)


def test_atomic_on_a_non_autocommit_connection_still_works():
    """`atomic()` may be handed a connection from code that has not adopted
    `connect()` yet. It must not issue a second BEGIN there — psycopg2 has
    already opened one."""
    conn = FakeConn(autocommit=False)
    with db_txn.atomic(conn):
        conn.cursor().execute("UPDATE t SET a = 1")
    assert not db_txn.in_transaction(conn)
    assert "BEGIN" not in conn.log


# ── the callers ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module",
    ["pressure_agent", "ht_pressure_agent", "fav_pressure_agent",
     "settled_sweep_observer", "late_goals_observer"],
)
def test_every_daemon_connects_through_db_txn(module):
    """A daemon that calls psycopg2.connect directly is a daemon that will leak
    a transaction again. The import is what makes the property hold, so it is
    what the test checks — importing the modules themselves would drag in the
    network clients and the .env."""
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     f"{module}.py")
    ).read()
    body = src[src.index("def _conn("):]
    body = body[: body.index("\n\n")]
    assert "db_txn.connect" in body, f"{module}._conn must go through db_txn"
    assert "psycopg2.connect" not in body, f"{module}._conn still connects raw"


@pytest.mark.parametrize(
    "module,fetch",
    [
        ("pressure_agent", "_goal_minute_api"),
        ("ht_pressure_agent", "_first_half_goals_api"),
        ("fav_pressure_agent", "_halftime_scores"),
    ],
)
def test_settle_makes_no_network_call_inside_the_write_loop(module, fetch):
    """The eight-minute transaction, pinned.

    Each settle() fetches from api-football and then writes. The fetch must
    happen BEFORE the write cursor is opened — if the call site sits after
    `with conn.cursor()`, the lock is held across HTTP again.
    """
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     f"{module}.py")
    ).read()
    settle = src[src.index("\ndef settle(conn)"):]
    # from 1, not 0 — the slice STARTS with "\ndef ", so searching from the
    # beginning finds itself and returns an empty function body.
    settle = settle[: settle.index("\ndef ", 1)]

    # The write loop is the LAST cursor opened in settle(); everything before
    # it is reads and fetches. Anchoring on "the last cursor" rather than on an
    # exact line keeps this test about the property, not about the formatting.
    write_loop_at = settle.rindex("with conn.cursor() as cur:")
    calls = [i for i in range(len(settle)) if settle.startswith(fetch + "(", i)]
    assert calls, f"{module}.settle no longer calls {fetch} — update this test"
    assert all(i < write_loop_at for i in calls), (
        f"{module}.settle calls {fetch} inside the write loop; that holds a "
        f"transaction open across a network round trip"
    )
