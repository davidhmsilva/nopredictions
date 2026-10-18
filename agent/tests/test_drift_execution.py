"""Pre-Match Drift books its paper trades on the side of the book that fills
(db/076): buy NO at the NO ask, sell NO at the NO bid.

The book below is the real one read on 2026-10-16 for Middlesbrough v Cardiff,
"draw", position #1824: YES 0.22 / 0.35, NO 0.65 / 0.78. The old code entered
that position at NO 0.65 (the bid) and would have exited at the NO ask, so a
+10% target fired on the spread alone.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import drift_book                      # noqa: E402
import drift_monitor                   # noqa: E402

YES, NO = "yes-token", "no-token"

# As /book returns them: bids ascending, asks descending (best level LAST).
RAW_YES = {
    "asset_id": YES,
    "bids": [{"price": "0.01", "size": "518.56"}, {"price": "0.03", "size": "16.81"},
             {"price": "0.06", "size": "6"}, {"price": "0.22", "size": "5"}],
    "asks": [{"price": "0.82", "size": "13"}, {"price": "0.8", "size": "5"},
             {"price": "0.35", "size": "5"}],
}
RAW_NO = {
    "asset_id": NO,
    "bids": [{"price": "0.18", "size": "13"}, {"price": "0.2", "size": "5"},
             {"price": "0.65", "size": "5"}],
    "asks": [{"price": "0.97", "size": "16.81"}, {"price": "0.94", "size": "6"},
             {"price": "0.78", "size": "5"}],
}


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def _books(monkeypatch, raws):
    by_id = {r["asset_id"]: r for r in raws}
    monkeypatch.setattr(drift_book.requests, "post",
                        lambda url, json, timeout: _Resp([by_id[t["token_id"]] for t in json
                                                          if t["token_id"] in by_id]))


def test_best_level_is_sorted_not_index_zero(monkeypatch):
    _books(monkeypatch, [RAW_YES, RAW_NO])
    b = drift_book.fetch_books([YES, NO])
    assert drift_book.top(b[YES]) == {"bid": 0.22, "bid_size": 5, "ask": 0.35, "ask_size": 5}
    assert drift_book.top(b[NO])["bid"] == 0.65
    assert drift_book.top(b[NO])["ask"] == 0.78


def test_mirrored_yes_book_equals_the_no_book(monkeypatch):
    _books(monkeypatch, [RAW_YES, RAW_NO])
    b = drift_book.fetch_books([YES, NO])
    m = drift_book.mirror(b[YES])
    assert drift_book.top(m) == drift_book.top(b[NO])
    # a row that never stored the NO token falls back to the mirror
    assert drift_book.top(drift_book.no_book({YES: b[YES]}, YES, None))["ask"] == 0.78


def test_entry_pays_the_no_ask(monkeypatch):
    import drift_scanner
    _books(monkeypatch, [RAW_YES, RAW_NO])
    q = drift_scanner.entry_quote(YES, NO, stake_usd=1.0)
    assert q["entry_no_price"] == 0.78            # the ask, not 1 − YES ask = 0.65
    assert q["entry_no_ask"] == 0.78 and q["entry_no_bid"] == 0.65
    assert q["entry_yes_bid"] == 0.22 and q["entry_yes_ask"] == 0.35
    assert q["entry_no_depth_usd"] == 3.9
    assert abs(q["size_shares"] - 1 / 0.78) < 1e-3


def test_entry_walks_the_ladder_and_refuses_what_cannot_fill():
    asks = [(0.78, 5.0), (0.94, 6.0)]
    price, shares = drift_book.buy_fill(asks, 5.0)        # $3.90 at 0.78, $1.10 at 0.94
    assert shares == round(5 + 1.10 / 0.94, 4)
    assert 0.78 < price < 0.94
    assert drift_book.buy_fill(asks, 100.0) is None
    assert drift_book.buy_fill([], 1.0) is None


def test_exit_receives_the_no_bid(monkeypatch):
    _books(monkeypatch, [RAW_YES, RAW_NO])
    q = drift_monitor.exit_quote(YES, NO, shares=1 / 0.78)
    assert q["sell"] == 0.65                       # the bid, not 1 − YES bid = 0.78
    assert drift_book.sell_fill([(0.65, 5.0)], 6.0) is None


def test_an_unchanged_book_is_a_loss_not_a_target(monkeypatch):
    """Bought at 0.78 and marked at the 0.65 bid with nothing moving: the old
    code read this as NO 0.65 → 0.78, +20%, a target. It is a −16.7% stop."""
    _books(monkeypatch, [RAW_YES, RAW_NO])
    closed = {}
    monkeypatch.setattr(drift_monitor, "_close_position",
                        lambda conn, pid, quote, reason, dry_run=False: closed.update(
                            reason=reason, sell=quote["sell"]))
    entry = 0.78
    pos = {
        "id": 1, "token_id": YES, "no_token_id": NO, "home": "Middlesbrough", "away": "Cardiff",
        "outcome_key": "draw", "price_convention": "executable",
        "entry_yes_price": 0.35, "entry_no_price": entry, "size_shares": round(1 / entry, 4),
        "target_no_price": round(entry * 1.10, 4), "stop_no_price": round(entry * 0.95, 4),
        "hard_close_minutes": 60, "stake_usd": 1.0,
        "kickoff_at": datetime.now(timezone.utc) + timedelta(hours=10),
    }
    assert drift_monitor._process_position(None, pos, datetime.now(timezone.utc), dry_run=True)
    assert closed == {"reason": "stop", "sell": 0.65}


# ── Entry gates and exit levels (db/077) ─────────────────────────────────────

import drift_scanner                   # noqa: E402


def _quote(paid, bid, ask, depth=100.0):
    return {"entry_no_price": paid, "size_shares": round(1 / paid, 4),
            "entry_no_bid": bid, "entry_no_ask": ask,
            "entry_no_ask_size": round(depth / ask, 2) if ask else None,
            "entry_no_depth_usd": depth, "entry_yes_bid": None, "entry_yes_ask": None}


def test_the_wide_middlesbrough_book_is_refused():
    # NO 0.65 / 0.78: a 13c book, marked −17% the moment it is bought.
    reason, _ = drift_scanner.entry_gate(_quote(0.78, 0.65, 0.78), fair=0.10)
    assert reason and "spread" in reason


def test_one_sided_and_thin_books_are_refused():
    assert "one-sided" in drift_scanner.entry_gate(_quote(0.70, None, 0.70), 0.10)[0]
    assert "$" in drift_scanner.entry_gate(_quote(0.70, 0.68, 0.70, depth=5), 0.10)[0]


def test_edge_is_read_on_the_yes_price_obtained():
    # Brighton v Crystal Palace draw, 2026-10-16: YES 0.04 / 0.43. Gamma's ask
    # says +18pp over a 0.25 fair; buying NO at 0.96 obtains YES at 0.04.
    reason, edge = drift_scanner.entry_gate(_quote(0.96, 0.95, 0.96), fair=0.25)
    assert edge == -21.0 and "executable edge" in reason


def test_a_tight_book_with_edge_enters():
    reason, edge = drift_scanner.entry_gate(_quote(0.66, 0.64, 0.66), fair=0.24)
    assert reason is None and edge == 10.0


def test_the_stop_is_a_move_from_the_entry_bid_not_the_spread():
    q = _quote(0.66, 0.64, 0.66)
    target, stop = drift_scanner.exit_levels(q)
    assert target == round(0.66 * 1.10, 4)
    assert stop == round(0.64 * 0.95, 4)
    # An unchanged book marks at the entry bid: a loss, but neither exit fires.
    assert stop < 0.64 < target
