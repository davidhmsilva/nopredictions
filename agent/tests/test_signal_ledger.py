"""The signal ledger (db/072): the three every-game agents write the book's
ask depth (and NFL/NBA the un-haircut fair) on every candidate row, the close
carries the venue's bid and ask, and the view's edge and CLV sit on one basis,
so CLV = edge + line movement exactly."""
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import nba_agent  # noqa: E402
import nfl_agent  # noqa: E402
import unl_agent  # noqa: E402

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
MIGRATION = os.path.join(os.path.dirname(__file__), "..", "..", "db", "072_signal_ledger.sql")


def _cand(**kw):
    base = dict(family="moneyline", subject=None, line=None, side="Eagles", condition_id="0xc",
                token_id="tok", bid=0.47, ask=0.48, liquidity=900.0, fair=0.495, fair_raw=0.505,
                source="sharp_exact", ev=1.2, depth_usd=5400.0)
    base.update(kw)
    return SimpleNamespace(**base)


def _capture(monkeypatch, module):
    seen = {}

    def fake(cur, sql, rows, *a, **k):
        seen["sql"], seen["rows"] = sql, rows
    monkeypatch.setattr(module.psycopg2.extras, "execute_values", fake)

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return seen, SimpleNamespace(cursor=lambda: Cur())


def _columns(sql: str) -> list[str]:
    inner = re.search(r"INSERT INTO \w+ \((.*?)\)\s*VALUES", sql, re.S).group(1)
    return [c.strip() for c in inner.split(",")]


@pytest.mark.parametrize("module", [nfl_agent, nba_agent, unl_agent])
def test_candidates_carry_depth_and_raw_fair(monkeypatch, module):
    seen, conn = _capture(monkeypatch, module)
    game = SimpleNamespace(slug="g", kickoff=NOW + timedelta(hours=3), home="H", away="A",
                           teams=("H", "A"))
    c = _cand()
    module.write_candidates(conn, game, None, [c], NOW, chosen=c, trade_id=7)
    cols, row = _columns(seen["sql"]), seen["rows"][0]
    assert len(cols) == len(row)
    got = dict(zip(cols, row))
    assert got["ask_depth_usd"] == 5400.0
    assert got["fair_raw"] == 0.505
    assert got["paper_trade_id"] == 7 and got["chosen"] is True


@pytest.mark.parametrize("module", [nfl_agent, nba_agent, unl_agent])
def test_close_writes_the_venue_bid_and_ask(module):
    src = open(module.__file__).read()
    assert "pm_closing_bid = %s, pm_closing_ask = %s" in src
    assert "for tid, fair, mid, book, entry, cbid, cask in closes:" in src
    assert re.search(r"closes\.append\(\(.*?c\.bid, c\.ask\)\)", src, re.S)


def test_migration_keeps_edge_and_clv_on_one_basis():
    sql = " ".join(open(MIGRATION).read().split())
    assert "b.fair_at_signal / NULLIF(b.entry_price, 0) - 1 AS edge_at_signal" in sql
    assert "b.fair_at_close / NULLIF(b.entry_price, 0) - 1 AS clv_sharp" in sql
    assert "b.fair_at_close / NULLIF(b.fair_at_signal, 0) - 1 AS line_move" in sql
    assert "REVOKE ALL ON v_signal_ledger FROM anon, authenticated" in sql
    assert "security_invoker = true" in sql


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs the database")
def test_view_identity_on_the_database():
    """(1 + edge)(1 + line_move) = 1 + clv on every row with a close — run in a
    transaction that is rolled back, so the schema is not touched."""
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"].replace(":5432/", ":6543/"))
    try:
        cur = conn.cursor()
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(open(MIGRATION).read())
        cur.execute("""SELECT count(*), max(abs((1 + edge_at_signal) * (1 + line_move) - (1 + clv_sharp)))
                         FROM v_signal_ledger WHERE clv_sharp IS NOT NULL""")
        n, err = cur.fetchone()
        assert n > 0 and err < 1e-9
    finally:
        conn.rollback()
        conn.close()
