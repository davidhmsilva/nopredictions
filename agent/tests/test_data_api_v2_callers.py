"""Data API v2 (v1 answers 410 since 2026-11-02): the cursor walk in pm_data,
and the three readers moved onto it or onto pm_trades — pm_prematch_flow
(/v2/trades), pm_chain_sync (/v2/positions), wallet_analyzer (/v2/activity,
/v2/value). Every failed read is None or an exception, never "no rows"."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pm_data  # noqa: E402


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise pm_data.requests.HTTPError(str(self.status_code))

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class _Feed:
    """Serves the given pages in order, recording every request."""
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        p = self.pages[len(self.calls) - 1]
        return p if isinstance(p, _Resp) else _Resp(p)


def _page(rows, cursor=None):
    return {"data": rows, "pagination": {"has_more": cursor is not None, "next_cursor": cursor}}


GONE = _Resp({"error": "Data API v1 has been retired"}, 410)


# ── pm_data.walk / value ─────────────────────────────────────────────────────

def test_walk_follows_the_cursor_and_resends_the_same_filters():
    f = _Feed([_page([{"a": 1}, {"a": 2}], "k1"), _page([{"a": 3}], "k2"), _page([])])
    rows, complete = pm_data.walk("activity", {"user": "0xw", "start": 5}, session=f, limit=2)
    assert [r["a"] for r in rows] == [1, 2, 3] and complete
    assert f.calls[0][0] == "https://data-api.polymarket.com/v2/activity"
    assert [q.get("cursor") for _, q in f.calls] == [None, "k1", "k2"]
    for _, q in f.calls:
        assert q["user"] == "0xw" and q["start"] == 5 and q["limit"] == 2 and "offset" not in q


def test_a_short_page_is_not_the_end_only_a_null_cursor_is():
    f = _Feed([_page([], "k1"), _page([{"a": 1}])])
    rows, complete = pm_data.walk("positions", {"user": "0xw"}, session=f)
    assert rows == [{"a": 1}] and complete


def test_a_documented_miss_is_an_empty_walk_not_a_failure():
    assert pm_data.walk("positions", {"user": "0xw"}, session=_Feed([{"data": None}])) == ([], True)


@pytest.mark.parametrize("bad", [GONE, _Resp({"error": "bad"}, 400), _Resp(ValueError("html")),
                                 _Resp([{"v1": "bare list"}]), _Resp({"data": {"not": "a list"}})])
def test_any_page_that_is_not_a_v2_page_fails_the_whole_walk(bad):
    f = _Feed([_page([{"a": 1}], "k1"), bad])
    assert pm_data.walk("activity", {"user": "0xw"}, session=f, backoff=0) is None


def test_a_throttle_is_retried_and_a_410_is_not():
    f = _Feed([_Resp({}, 429), _page([{"a": 1}])])
    assert pm_data.walk("activity", {"user": "0xw"}, session=f, backoff=0) == ([{"a": 1}], True)
    g = _Feed([GONE, _page([{"a": 1}])])
    assert pm_data.walk("activity", {"user": "0xw"}, session=g, backoff=0) is None and len(g.calls) == 1


def test_a_row_cap_says_the_walk_is_incomplete():
    f = _Feed([_page([{"a": 1}, {"a": 2}], "k1"), _page([{"a": 3}])])
    rows, complete = pm_data.walk("activity", {"user": "0xw"}, session=f, max_rows=2)
    assert len(rows) == 2 and not complete


def test_a_repeating_cursor_is_a_failure_not_a_loop():
    f = _Feed([_page([{"a": 1}], "k1"), _page([{"a": 2}], "k1")])
    assert pm_data.walk("activity", {"user": "0xw"}, session=f) is None


def test_value_reads_the_v2_object():
    assert pm_data.value("0xw", session=_Feed([{"data": {"proxy_wallet": "0xw", "value": 1.4442}}])) == 1.4442
    assert pm_data.value("0xw", session=_Feed([{"data": None}])) is None
    assert pm_data.value("0xw", session=_Feed([[{"user": "0xw", "value": 3.0}]])) is None   # v1's list
    assert pm_data.value("0xw", session=_Feed([GONE])) is None


# ── pm_prematch_flow: /v2/trades, window applied on our side ──────────────────

def _trade(ts, price=0.5, idx=0):
    return {"timestamp": ts, "price": price, "size": 10.0, "side": "BUY", "token_id": f"t{idx}",
            "condition_id": "0xc", "outcome_index": idx, "proxy_wallet": "0xw",
            "transaction_hash": f"0x{ts}"}


def test_prematch_flow_keeps_only_the_window_and_stores_v1_column_names():
    pf = pytest.importorskip("pm_prematch_flow")
    ko = 1_000_000
    start = ko - pf.WINDOW_S
    # newest first, as v2 serves a market: in-play prints after KO, then the
    # window, then a page that reaches past KO−96h (where the walk must stop)
    f = _Feed([_page([_trade(ko + 600), _trade(ko - 60, 0.4, 1)], "k1"),
               _page([_trade(start + 1), _trade(start - 1)], "k2")])
    d = pf.fetch_market(f, pf.Pacer(1e9), "0xc", ko)
    assert len(f.calls) == 2                                  # stopped once past the window
    assert list(d.columns) == list(pf.KEEP)
    assert sorted(d.timestamp) == [start + 1, ko - 60]
    row = d[d.timestamp == ko - 60].iloc[0]
    assert row.outcomeIndex == 1 and row.proxyWallet == "0xw" and row.transactionHash == f"0x{ko - 60}"
    q = f.calls[0][1]
    assert q["condition"] == "0xc" and q["taker_only"] == "true" and "market" not in q


def test_prematch_flow_raises_on_a_failed_market_so_no_pickle_is_written():
    pf = pytest.importorskip("pm_prematch_flow")
    f = _Feed([GONE])
    with pytest.raises(RuntimeError):
        pf.fetch_market(f, pf.Pacer(1e9), "0xc", 1_000_000)


def test_prematch_flow_session_retries_a_throttle(monkeypatch):
    pf = pytest.importorskip("pm_prematch_flow")
    monkeypatch.setattr(pf.time, "sleep", lambda s: None)
    f = _Feed([_Resp({}, 429), _page([_trade(999_990)])])
    d = pf.fetch_market(f, pf.Pacer(1e9), "0xc", 1_000_000)
    assert len(d) == 1 and len(f.calls) == 2


# ── pm_chain_sync: /v2/positions ─────────────────────────────────────────────

POS = {"proxy_wallet": "0xw", "token_id": "101", "condition_id": "0xc", "current_size": 5.0,
       "avg_price": 0.26, "entry_cost_usdc": 1.3, "current_price": 0.0, "current_value": 0.0,
       "unrealized_pnl": -1.3, "percent_pnl": -100.0, "status": "REDEEMABLE", "title": "T",
       "outcome": "Yes", "event_slug": "ev", "end_date": "2026-07-01"}


def test_chain_sync_walks_v2_positions(monkeypatch):
    import pm_chain_sync as cs
    f = _Feed([_page([POS], "k1"), _page([dict(POS, token_id="102")])])
    monkeypatch.setattr(pm_data, "requests", type("R", (), {"get": f.get,
                                                            "RequestException": Exception}))
    rows = cs.fetch_positions("0xw")
    assert [r["token_id"] for r in rows] == ["101", "102"]
    assert f.calls[0][0].endswith("/v2/positions") and f.calls[1][1]["cursor"] == "k1"
    assert cs.pnl_pct(POS) == -100.0 and cs.pnl_pct(dict(POS, entry_cost_usdc=0)) == 0.0


def test_chain_sync_refuses_to_sync_on_a_failed_read(monkeypatch):
    import pm_chain_sync as cs
    monkeypatch.setattr(pm_data, "requests", type("R", (), {"get": _Feed([GONE]).get,
                                                            "RequestException": Exception}))
    with pytest.raises(RuntimeError):
        cs.fetch_positions("0xw")


# ── wallet_analyzer: /v2/activity, /v2/value ──────────────────────────────────

ACT = {"proxy_wallet": "0xw", "timestamp": 100, "condition_id": "0xc", "type": "TRADE", "size": 10.0,
       "usdc_size": 4.1, "transaction_hash": "0xh", "price": 0.4, "token_id": "TY", "side": "BUY",
       "outcome_index": 0, "title": "T", "slug": "s", "event_slug": "ev", "outcome": "Yes",
       "name": "nm", "pseudonym": "ps"}


def test_activity_rows_come_back_in_the_names_the_reconstruction_reads():
    import wallet_analyzer as wa
    merge = dict(ACT, timestamp=200, type="MERGE", token_id="", outcome_index=999, side="", price=0.0)
    f = _Feed([_page([merge], "k1"), _page([ACT])])
    rows, complete = wa.fetch_activity("0xw", since=50, session=f)
    assert complete and [r["type"] for r in rows] == ["TRADE", "MERGE"]        # oldest first
    t, m = rows
    assert (t["asset"], t["conditionId"], t["outcomeIndex"], t["usdcSize"], t["eventSlug"],
            t["transactionHash"], t["name"]) == ("TY", "0xc", 0, 4.1, "ev", "0xh", "nm")
    assert m["asset"] is None and m["outcomeIndex"] is None
    for _, q in f.calls:
        assert q["start"] == 50 and "end" not in q and q["limit"] == wa.PAGE <= 1000


def test_activity_raises_rather_than_reading_a_refusal_as_an_empty_wallet():
    import wallet_analyzer as wa
    with pytest.raises(RuntimeError):
        wa.fetch_activity("0xw", session=_Feed([_page([ACT], "k1"), GONE]))


def test_a_v2_redeem_lands_on_its_own_token_without_market_metadata():
    import wallet_analyzer as wa
    buy = wa.v1_row(dict(ACT, token_id="TN", outcome_index=1, outcome="No", usdc_size=6.0, price=0.6))
    other = wa.v1_row(dict(ACT, timestamp=101, transaction_hash="0xh2"))
    red = wa.v1_row(dict(ACT, timestamp=300, type="REDEEM", token_id="TN", outcome_index=1, side="",
                         price=0.0, size=10.0, usdc_size=10.0, outcome="No"))
    lots, _ = wa.build_lots([buy, other, red], {})
    redeemed = [lot for lot in lots if lot.exit_kind == "redeem"]
    assert len(redeemed) == 1 and redeemed[0].asset == "TN" and redeemed[0].exit_price == 1.0


def test_current_value_reads_v2():
    import wallet_analyzer as wa
    assert wa.fetch_current_value("0xw", _Feed([{"data": {"value": 12.5}}])) == 12.5
    assert wa.fetch_current_value("0xw", _Feed([GONE])) is None
