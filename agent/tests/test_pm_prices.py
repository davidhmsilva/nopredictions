"""pm_prices: the data-api v2 price history every agent reads through."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pm_prices  # noqa: E402


class _Resp:
    def __init__(self, body, ok=True):
        self._body, self.ok = body, ok

    def json(self):
        return self._body


class _Session:
    """Answers from a list of bodies, recording every params dict sent."""

    def __init__(self, bodies):
        self.bodies, self.calls = list(bodies), []

    def get(self, url, params=None, timeout=None):
        assert url == pm_prices.PRICES_HISTORY
        self.calls.append(dict(params))
        return _Resp(self.bodies.pop(0))


def _page(points, cursor=None):
    return {"data": [{"timestamp": t, "price": p, "resolution_seconds": 60} for t, p in points],
            "pagination": {"has_more": cursor is not None, "next_cursor": cursor}}


def test_window_end_is_sent_exclusive_so_ours_stays_inclusive():
    s = _Session([_page([(100, 0.5)])])
    pm_prices.history("tok", start=100, end=200, bucket_s=60, session=s)
    assert s.calls == [{"token_id": "tok", "start": 100, "end": 201, "bucket_seconds": 60}]


def test_the_terminal_point_stamped_past_end_is_clipped():
    # Measured 2026-10-26: end=KO came back with its last point at KO+1.
    s = _Session([_page([(150, 0.4), (200, 0.5), (201, 0.5)])])
    assert pm_prices.history("tok", start=100, end=200, session=s) == [(150, 0.4), (200, 0.5)]


def test_pages_follow_the_cursor_and_come_back_sorted():
    s = _Session([_page([(300, 0.3), (100, 0.1)], cursor="c1"), _page([(200, 0.2)])])
    out = pm_prices.history("tok", interval="max", session=s)
    assert out == [(100, 0.1), (200, 0.2), (300, 0.3)]
    assert s.calls[0] == {"token_id": "tok", "interval": "max"}
    assert s.calls[1] == {"token_id": "tok", "cursor": "c1"}


def test_a_failed_page_returns_what_was_read():
    s = _Session([_page([(100, 0.1)], cursor="c1"), None])
    s.bodies[-1] = {"error": "boom"}
    assert pm_prices.history("tok", interval="max", session=s) == [(100, 0.1)]


def test_as_of_keeps_the_points_own_timestamp():
    # A 3h bucket answering for an old market: the caller must see how stale.
    s = _Session([{"data": [{"timestamp": 1000, "price": 0.475, "resolution_seconds": 10800}]}])
    assert pm_prices.price_as_of("tok", 24000, session=s) == (1000, 0.475)
    assert s.calls == [{"token_id": "tok", "as_of": 24000}]


def test_as_of_never_returns_a_point_after_the_instant():
    s = _Session([{"data": [{"timestamp": 500, "price": 0.4}, {"timestamp": 900, "price": 0.6}]}])
    assert pm_prices.price_as_of("tok", 600, session=s) == (500, 0.4)


def test_bad_rows_are_skipped_not_fatal():
    s = _Session([{"data": [{"timestamp": None, "price": 0.4}, {"timestamp": 5, "price": "x"},
                            {"timestamp": 6, "price": 0.7}]}])
    assert pm_prices.history("tok", start=0, session=s) == [(6, 0.7)]
