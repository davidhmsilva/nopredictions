"""The first-half fair-value table (rebuilt 2026-11-05) and the pre-match total
that feeds it (ht_pressure_agent.fill_pre_match)."""

import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import first_half_table as fht  # noqa: E402
import ht_pressure_agent as ht  # noqa: E402


# ── the fit ──────────────────────────────────────────────────────────────────

def test_fit_recovers_a_known_curve():
    a, b = -0.4, 0.7
    groups = []
    for x in [i / 10 for i in range(-15, 16)]:
        n = 10_000
        p = 1 / (1 + math.exp(-(a + b * x)))
        groups.append((x, n, round(n * p)))
    fa, fb = fht.fit_logistic(groups)
    assert fa == pytest.approx(a, abs=1e-3)
    assert fb == pytest.approx(b, abs=1e-3)


# ── the shipped table ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def table():
    return fht.load()


def test_the_shipped_table_has_a_curve_for_every_minute(table):
    assert set(table["model"]) == {str(m) for m in table["minutes"]}
    assert table["era_start"] >= "2022-07-01"


def test_the_curve_rises_with_the_pre_match_total(table):
    """A match the market expected goals from cannot be less likely to produce
    one from the same state."""
    for m in (15, 25, 35):
        ps = [fht.lookup(table, m, p)[0] for p in (0.35, 0.45, 0.55, 0.65)]
        assert ps == sorted(ps)


def test_the_curve_falls_with_the_clock(table):
    for p_over in (None, 0.40, 0.55, 0.65):
        ps = [fht.lookup(table, m, p_over)[0] for m in range(15, 41)]
        assert ps == sorted(ps, reverse=True)


def test_the_measured_levels(table):
    """The numbers the rebuild was judged on (ESPN 2022-07+, Pinnacle close):
    0-0 at 15' pooled ~0.56; low total ~0.52, high ~0.66. A rebuild that moves
    these by more than a few points changed the universe, not the era."""
    assert fht.lookup(table, 15, None)[0] == pytest.approx(0.56, abs=0.03)
    assert fht.lookup(table, 15, 0.40)[0] == pytest.approx(0.51, abs=0.04)
    assert fht.lookup(table, 15, 0.62)[0] == pytest.approx(0.65, abs=0.04)


def test_a_silly_total_is_clipped_not_extrapolated(table):
    assert fht.lookup(table, 20, 0.999)[0] == fht.lookup(table, 20, 0.95)[0]


def test_an_old_table_without_curves_still_prices_from_its_buckets():
    old = {"minutes": [20], "min_cell_n": 200,
           "cells": {"20|lo": {"n": 900, "p": 0.45}, "20|all": {"n": 3000, "p": 0.50}}}
    assert fht.lookup(old, 20, 0.30) == (0.45, 900)
    assert fht.lookup(old, 20, None) == (0.50, 3000)


def test_off_grid_states_return_nothing(table):
    assert fht.lookup(table, 70, 0.5) == (None, 0)


# ── fill_pre_match ───────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def execute(self, sql, params):
        self.log.append(params)

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def cursor(self):
        return _Cur(self.rows, self.calls)


def _fx(title, minutes_ago=20):
    return {"title": title, "kickoff": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)}


def test_fill_sets_a_pre_match_total_from_the_recorder():
    fx = _fx("A vs. B")
    conn = _Conn([("A vs. B", fx["kickoff"], 0.55, 0.57)])
    state = ht.HTState()
    assert ht.fill_pre_match(conn, [fx], state) == 1
    assert state.pre["A vs. B"]["prematch"] is True
    assert state.pre["A vs. B"]["p"] == pytest.approx(0.56)


def test_a_known_total_is_never_asked_for_again():
    fx = _fx("A vs. B")
    state = ht.HTState()
    state.pre["A vs. B"] = {"p": 0.5, "prematch": True, "at": time.time()}
    conn = _Conn([])
    assert ht.fill_pre_match(conn, [fx], state) == 0
    assert conn.calls == []


def test_a_miss_is_retried_later_not_every_poll_and_not_never():
    fx = _fx("A vs. B")
    state = ht.HTState()
    conn = _Conn([])
    ht.fill_pre_match(conn, [fx], state)
    ht.fill_pre_match(conn, [fx], state)
    assert len(conn.calls) == 1                      # cached for PRE_RETRY_S
    state.pre_looked["A vs. B"] -= ht.PRE_RETRY_S + 1
    ht.fill_pre_match(conn, [fx], state)
    assert len(conn.calls) == 2                      # and then asked again
    state.pre_looked["A vs. B"] -= 7 * 3600
    state.prune()
    assert "A vs. B" not in state.pre_looked         # the miss expires


def test_the_same_title_on_another_day_is_not_this_fixture():
    fx = _fx("A vs. B")
    conn = _Conn([("A vs. B", fx["kickoff"] - timedelta(days=7), 0.55, 0.57)])
    state = ht.HTState()
    assert ht.fill_pre_match(conn, [fx], state) == 0
    assert "A vs. B" not in state.pre


def test_fixtures_that_cannot_be_in_their_first_half_are_not_asked_about():
    conn = _Conn([])
    state = ht.HTState()
    ht.fill_pre_match(conn, [_fx("Old", minutes_ago=5 * 60), _fx("Later", minutes_ago=-120)], state)
    assert conn.calls == []


def test_a_filled_total_is_the_one_observe_reads():
    """_note_pre_match_total keeps a pre-match entry and never overwrites it
    with the drifted in-play board."""
    fx = _fx("A vs. B")
    state = ht.HTState()
    state.pre["A vs. B"] = {"p": 0.61, "prematch": True, "at": time.time()}
    got = ht._note_pre_match_total(state, fx, {})
    assert got["p"] == 0.61 and got["prematch"] is True
