"""pm_close: Polymarket's price at kick-off, read live and filled from history."""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pm_close  # noqa: E402

KO = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
KO_TS = int(KO.timestamp())


class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.conn.rows


class _Conn:
    def __init__(self, rows=()):
        self.rows, self.sql = list(rows), []

    def cursor(self, cursor_factory=None):
        return _Cur(self)


def _updates(conn):
    return [(q, p) for q, p in conn.sql if q.startswith("UPDATE")]


# ── the live read ────────────────────────────────────────────────────────────

def test_mid_needs_both_sides_of_the_book():
    assert pm_close.mid_of({"bid": 0.44, "ask": 0.46}) == 0.45
    assert pm_close.mid_of(None) is None
    assert pm_close.mid_of({"bid": None, "ask": 0.46}) is None
    assert pm_close.mid_of({"bid": 0.0, "ask": 0.46}) is None
    assert pm_close.mid_of({"bid": 0.50, "ask": 0.46}) is None      # crossed


def test_live_close_writes_mid_bid_ask_and_pm_clv_without_any_sharp_line():
    conn = _Conn()
    mid = pm_close.record_live(conn, 7, 0.40, {"bid": 0.43, "ask": 0.45})
    assert mid == 0.44
    (q, p), = _updates(conn)
    assert "pm_clv_source = 'clob_book'" in q and "closing_price IS NULL" not in q
    assert "clv = " not in q.replace("pm_clv = ", "")              # the sharp close is left alone
    assert p == (0.44, round(0.44 / 0.40 - 1, 5), 0.43, 0.45, 7)


def test_live_close_on_a_one_sided_book_writes_nothing():
    conn = _Conn()
    assert pm_close.record_live(conn, 7, 0.40, None) is None
    assert _updates(conn) == []


# ── the fill from history ────────────────────────────────────────────────────

def _history(monkeypatch, pts):
    seen = {}

    def fake(token_id, start=None, end=None, bucket_s=None, **k):
        seen.update(token=token_id, start=start, end=end, bucket=bucket_s)
        return pts
    monkeypatch.setattr(pm_close.pm_prices, "history", fake)
    return seen


def test_history_close_is_the_last_minute_at_or_before_kickoff(monkeypatch):
    seen = _history(monkeypatch, [(KO_TS - 120, 0.41), (KO_TS - 60, 0.42), (KO_TS + 60, 0.60)])
    assert pm_close.close_from_history("tok", KO_TS) == (KO_TS - 60, 0.42)
    assert seen == {"token": "tok", "start": KO_TS - 1800, "end": KO_TS, "bucket": 60}


def test_history_close_refuses_a_stale_point(monkeypatch):
    _history(monkeypatch, [(KO_TS - 301, 0.42)])
    assert pm_close.close_from_history("tok", KO_TS) is None


def test_history_close_refuses_the_settled_zero_or_one(monkeypatch):
    # an old market's series comes back as the result at every bucket
    for p in (0.0, 1.0):
        _history(monkeypatch, [(KO_TS, p)])
        assert pm_close.close_from_history("tok", KO_TS) is None


def test_history_close_with_no_points_is_none(monkeypatch):
    _history(monkeypatch, [])
    assert pm_close.close_from_history("tok", KO_TS) is None


def test_fill_writes_only_what_history_has_and_never_over_a_live_close(monkeypatch):
    rows = [{"id": 1, "pm_token_id": "a", "entry_price": 0.40, "kickoff": KO},
            {"id": 2, "pm_token_id": "b", "entry_price": 0.50, "kickoff": KO}]
    conn = _Conn(rows)
    monkeypatch.setattr(pm_close, "close_from_history",
                        lambda tok, ts, session=None: (ts - 60, 0.44) if tok == "a" else None)
    assert pm_close.fill_from_history(conn, "NBA Every Game") == 1
    select = conn.sql[0][0]
    assert "pm_closing_price IS NULL" in select and "s.name = %s" in select
    assert conn.sql[0][1] == ("NBA Every Game", pm_close.SETTLE_LAG_MIN, pm_close.HISTORY_DAYS)
    (q, p), = _updates(conn)
    assert "pm_clv_source = 'prices_history_1m'" in q and "pm_closing_price IS NULL" in q
    assert p == (0.44, KO - timedelta(seconds=60), round(0.44 / 0.40 - 1, 5), 1)


def test_fill_dry_run_writes_nothing(monkeypatch):
    conn = _Conn([{"id": 1, "pm_token_id": "a", "entry_price": 0.40, "kickoff": KO}])
    monkeypatch.setattr(pm_close, "close_from_history", lambda tok, ts, session=None: (ts, 0.44))
    assert pm_close.fill_from_history(conn, "NFL Every Game", dry_run=True) == 1
    assert _updates(conn) == []
