"""
Tests for the late-goal observer's score inference and fair-value lookup.

The score is read off PM's over ladder rather than a live feed, so these cases
are the only thing standing between us and a confidently wrong score — which is
strictly worse than a missing one, because it mislabels the target line and
every fair value derived from it.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_late_goals.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import late_goals_observer as o  # noqa: E402
import late_goals_table as lgt  # noqa: E402


# ── score inference ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,ladder,expected", [
    ("clean 2 goals",
     {0.5: 0.9995, 1.5: 0.9995, 2.5: 0.73, 3.5: 0.34}, (2, 2, True)),
    ("clean 3 goals",
     {1.5: 0.9995, 2.5: 0.9995, 3.5: 0.42}, (3, 3, True)),
    ("clean 0 goals in play",
     {0.5: 0.36, 1.5: 0.08}, (0, 0, True)),
    # A rung caught mid-update sits in the dead zone. It must widen the bounds
    # and drop certainty, never be rounded to the nearer side. Observed live at
    # minute 63: a just-resolved Over 2.5 quoted 0.985 and a single 0.99
    # threshold called the score 2 when it was 3.
    ("mid-update rung is not trusted",
     {0.5: 0.9995, 1.5: 0.9995, 2.5: 0.985, 3.5: 0.575}, (2, 3, False)),
    # Missing rungs give a range, never a guess.
    ("gap in the ladder",
     {0.5: 0.9995, 4.5: 0.0005}, (1, 4, False)),
    ("single rung",
     {2.5: 0.30}, (0, 2, False)),
    # A crossed ladder means a stale book; refuse rather than pick a side.
    ("contradiction",
     {0.5: 0.20, 1.5: 0.9995}, (None, None, False)),
])
def test_infer_goals(name, ladder, expected):
    assert o.infer_goals(ladder) == expected, name


def test_dead_zone_never_yields_certainty():
    """Any rung between UNSETTLED_MAX and SETTLED_PRICE kills certainty."""
    for price in (0.951, 0.97, 0.985, 0.9899):
        _lo, _up, certain = o.infer_goals({0.5: 0.9995, 1.5: price, 2.5: 0.30})
        assert not certain, price


def test_settled_threshold_survives_a_rare_scoreline():
    """PM quotes resolved lines at 0.9995 — that must read as settled."""
    lower, upper, certain = o.infer_goals({0.5: 0.9995, 1.5: 0.44})
    assert (lower, upper, certain) == (1, 1, True)


# ── ladder consistency ───────────────────────────────────────────────────────

def test_monotonic_ladder_is_consistent():
    assert o.ladder_consistent({0.5: 0.99, 1.5: 0.80, 2.5: 0.50, 3.5: 0.20})


def test_violation_below_the_bound_is_rejected():
    assert not o.ladder_consistent({0.5: 0.30, 1.5: 0.80}, up_to=2.0)


def test_violation_above_the_bound_is_ignored():
    """A stale far rung must not discard an otherwise usable fixture."""
    ladder = {0.5: 0.9995, 1.5: 0.9995, 2.5: 0.73, 3.5: 0.34, 4.5: 0.12, 5.5: 0.1535}
    assert not o.ladder_consistent(ladder)              # 5.5 > 4.5, globally broken
    assert o.ladder_consistent(ladder, up_to=3.5)       # but the rungs we use are fine


# ── market classification ────────────────────────────────────────────────────

@pytest.mark.parametrize("question,keep", [
    ("KF Egnatia vs. NK Celje: O/U 2.5", True),
    ("FC Cincinnati vs. Vancouver Whitecaps FC: O/U 0.5", True),
    # Team totals overwrite the real rung and silently corrupt the score.
    ("KF Egnatia vs. NK Celje: NK Celje O/U 1.5", False),
    ("KF Egnatia vs. NK Celje: KF Egnatia O/U 2.5", False),
    # Period totals are a different market entirely.
    ("KF Egnatia vs. NK Celje: 1st Half O/U 0.5", False),
    ("KF Egnatia vs. NK Celje: 2nd Half O/U 1.5", False),
    ("KF Egnatia vs. NK Celje: NK Celje 1st Half O/U 0.5", False),
    # Not goals at all.
    ("KF Egnatia vs. NK Celje: O/U 7.5 Total Corners", False),
])
def test_full_match_line_classification(question, keep):
    rem = question.split(": ", 1)[1] if ": " in question else question
    assert bool(o._FULL_LINE_RE.match(rem.strip())) is keep, question


@pytest.mark.parametrize("title,blocked", [
    ("Counter-Strike: Team Falcons vs 100 Thieves (BO3)", True),
    ("LoL: Barça eSports vs UCAM Esports Club (BO3)", True),
    ("Dota 2: Zero Tenacity vs Level UP (BO3)", True),
    ("Inter Miami CF vs. Chicago Fire FC", False),
])
def test_esports_are_blocked(title, blocked):
    assert o._is_esports(title) is blocked


def test_sibling_event_titles_merge_to_one_fixture():
    for suffix in (" - More Markets", " - Total Corners", " - Halftime Result"):
        assert o._SUFFIX_RE.sub("", "Inter Miami CF vs. Chicago Fire FC" + suffix).strip() \
            == "Inter Miami CF vs. Chicago Fire FC"


# ── clock ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("wall,expected", [
    (0, 0), (30, 30), (45, 45),
    (52, 45),      # halftime holds at 45 instead of drifting into the 2nd half
    (60, 45),
    (75, 60), (90, 75), (105, 90),
])
def test_game_minute(wall, expected):
    from datetime import datetime, timedelta, timezone
    ko = datetime(2026, 7, 22, 19, 0, tzinfo=timezone.utc)
    _w, minute = o.game_minute(ko, ko + timedelta(minutes=wall))
    assert minute == expected


def test_game_minute_is_none_before_kickoff():
    from datetime import datetime, timedelta, timezone
    ko = datetime(2026, 7, 22, 19, 0, tzinfo=timezone.utc)
    wall, minute = o.game_minute(ko, ko - timedelta(minutes=30))
    assert wall == -30 and minute is None


# ── score confirmation against the CLOB ──────────────────────────────────────
#
# The ladder reads Gamma, and Gamma lags the book. Where api-football could
# referee the v1 series the ladder was wrong on 29% of polls (305 of 1,048),
# under-reading the score in 181 of them — and the existing cross-check hid it,
# because a disagreement downgrades the row to certain=false, leaving the
# surviving set agreeing 743/743 by construction.

def _bk(bid=None, ask=None):
    return {"best_bid": bid, "best_ask": ask}


def test_books_confirm_a_clean_two_goal_state():
    assert o.confirm_score_books(True, 2, 1, _bk(0.44, 0.47), _bk(0.995, 0.999))


def test_stale_gamma_on_the_settled_rung_is_caught():
    """Gamma still quotes Over 1.5 live; the CLOB has it paid at 0.999."""
    assert not o.confirm_score_books(True, 1, 1, _bk(0.98, 0.999), _bk(0.995, 0.999))


def test_settled_rung_not_actually_paid_is_caught():
    """Claimed 2 goals, but the rung below trades at 0.61 — the score is wrong."""
    assert not o.confirm_score_books(True, 2, 1, _bk(0.40, 0.44), _bk(0.58, 0.61))


def test_goalless_match_has_no_lower_rung_to_check():
    assert o.confirm_score_books(True, 0, 1, _bk(0.33, 0.36), None)


def test_leveraged_line_is_not_the_boundary_rung():
    """needed=2 sits a rung above the score, so its ask carries no bound."""
    assert o.confirm_score_books(True, 2, 2, _bk(0.20, 0.23), _bk(0.995, 0.999))


def test_an_uncertain_score_is_never_confirmed():
    assert not o.confirm_score_books(False, 2, 1, _bk(0.44, 0.47), _bk(0.995, 0.999))


def test_a_missing_book_is_never_confirmed():
    assert not o.confirm_score_books(True, 2, 1, None, _bk(0.995, 0.999))
    assert not o.confirm_score_books(True, 2, 1, _bk(0.44, 0.47), None)


# ── kick-off detection ───────────────────────────────────────────────────────
#
# The clock was the defect that made the first 13 days of observations
# unusable: game_minute came from PM's listed start time and nothing checked it,
# and on smaller-league fixtures that time runs ~30 minutes ahead of the real
# kick-off. Measured on 26 settled fixtures, an average of 0.96 goals arrived
# after what we were calling minute 90. These cases cover the quota-free anchor.

from datetime import datetime, timedelta, timezone  # noqa: E402

_KO = datetime(2026, 8, 4, 19, 0, tzinfo=timezone.utc)
_PRE = {0.5: 0.93, 1.5: 0.74, 2.5: 0.52, 3.5: 0.28}


def _at(mins):
    return _KO + timedelta(minutes=mins)


def test_static_ladder_after_kickoff_is_not_detected_as_started():
    """A match listed at 19:00 that has not actually begun leaves the book still."""
    st = {}
    o.track_kickoff(st, "A vs B", _PRE, -20, _at(-20))
    for wall in range(1, 25, 5):
        assert o.track_kickoff(st, "A vs B", _PRE, wall, _at(wall)) is None


def test_drifting_ladder_dates_the_kickoff():
    st = {}
    o.track_kickoff(st, "A vs B", _PRE, -10, _at(-10))
    assert o.track_kickoff(st, "A vs B", _PRE, 5, _at(5)) is None
    live = {0.5: 0.88, 1.5: 0.68, 2.5: 0.47, 3.5: 0.25}   # every rung decayed
    assert o.track_kickoff(st, "A vs B", live, 10, _at(10)) == _at(10)


def test_a_goal_dates_the_kickoff_even_if_nothing_else_moved():
    st = {}
    o.track_kickoff(st, "A vs B", _PRE, -5, _at(-5))
    scored = {**_PRE, 0.5: 0.9995}
    assert o.track_kickoff(st, "A vs B", scored, 8, _at(8)) == _at(8)


def test_detection_latches_and_does_not_drift():
    """The first movement is the kick-off; later cycles must not overwrite it."""
    st = {}
    o.track_kickoff(st, "A vs B", _PRE, -5, _at(-5))
    live = {0.5: 0.86, 1.5: 0.66, 2.5: 0.44, 3.5: 0.22}
    first = o.track_kickoff(st, "A vs B", live, 6, _at(6))
    later = {0.5: 0.70, 1.5: 0.50, 2.5: 0.30, 3.5: 0.12}
    assert o.track_kickoff(st, "A vs B", later, 40, _at(40)) == first


def test_no_baseline_means_no_detection():
    """A fixture first seen after its listed start is never assumed to be on time.

    Absence of evidence that it began late is not evidence that it began on
    time, and this is exactly the population the v1 clock got wrong.
    """
    st = {}
    live = {0.5: 0.60, 1.5: 0.40, 2.5: 0.20}
    assert o.track_kickoff(st, "A vs B", live, 70, _at(70)) is None


def test_late_kickoff_is_outside_tolerance():
    """The 30-minute error that broke the v1 series has to fail the gate."""
    st = {}
    o.track_kickoff(st, "A vs B", _PRE, -30, _at(-30))
    live = {0.5: 0.87, 1.5: 0.67, 2.5: 0.45, 3.5: 0.24}
    detected = o.track_kickoff(st, "A vs B", live, 32, _at(32))
    assert int((detected - _KO).total_seconds() // 60) > o.KICKOFF_TOLERANCE_MIN


# ── fair-value table ─────────────────────────────────────────────────────────

def _servable(t, minute, goals, bucket):
    """The cell lookup() would actually return, or None if it falls back."""
    cell = t["cells"].get(f"{minute}|{goals}|{bucket}")
    return cell if cell and cell["n"] >= t["min_cell_n"] else None


@pytest.mark.parametrize("goals", [1, 2])
def test_table_is_strictly_monotone_where_the_strategy_lives(goals):
    """1-2 goals is the only state this strategy ever acts on, and those cells
    carry n~1,000-1,900. A single inversion there is a build error, not noise."""
    t = lgt.load()
    for b in (x["name"] for x in t["buckets"]):
        ps = [c["p"] for m in t["minutes"] if (c := _servable(t, m, goals, b))]
        assert ps == sorted(ps, reverse=True), (goals, b)


def test_table_is_monotone_elsewhere_within_sampling_noise():
    """Outside the strategy's states the grid thins out — 5 goals in a match the
    market read as low-scoring is a rare corner. Small inversions there are
    sampling noise; a large one still means the build is wrong."""
    t = lgt.load()
    for goals in t["goals"]:
        for b in (x["name"] for x in t["buckets"]):
            ps = [c["p"] for m in t["minutes"] if (c := _servable(t, m, goals, b))]
            assert all(a >= b2 - 0.02 for a, b2 in zip(ps, ps[1:])), (goals, b)


def test_richer_pre_match_total_never_prices_lower():
    t = lgt.load()
    for goals in (1, 2):
        for m in t["minutes"]:
            lo, hi = _servable(t, m, goals, "lo"), _servable(t, m, goals, "hi")
            if lo and hi:
                assert hi["p"] >= lo["p"], (m, goals)


def test_thin_cells_are_never_served():
    """Every cell below MIN_CELL_N must fall back to the pooled rate."""
    t = lgt.load()
    thin = [k for k, v in t["cells"].items()
            if not k.endswith("|all") and v["n"] < t["min_cell_n"]]
    for key in thin:
        minute, goals, bucket = key.split("|")
        edges = {b["name"]: b for b in t["buckets"]}[bucket]
        p_over = (edges["lo"] + edges["hi"]) / 2
        p, n = lgt.lookup(t, int(minute), int(goals), p_over)
        assert n >= t["min_cell_n"], key
        assert p == t["cells"][f"{minute}|{goals}|all"]["p"], key


def test_lookup_falls_back_to_the_pooled_cell_without_a_pre_match_total():
    t = lgt.load()
    p, n = lgt.lookup(t, 76, 1, None)
    assert p is not None and n > 0


def test_lookup_refuses_states_off_the_grid():
    t = lgt.load()
    assert lgt.lookup(t, 20, 1, 0.6) == (None, 0)


def test_two_goal_line_is_always_cheaper_than_one():
    """P(>=2 more) must sit strictly below P(>=1 more) in every servable cell."""
    t = lgt.load()
    for goals in (1, 2):
        for m in t["minutes"]:
            for b in ("lo", "mid", "hi", "all"):
                c = _servable(t, m, goals, b)
                if c and c.get("p2"):
                    assert c["p2"] < c["p"], (m, goals, b)


@pytest.mark.parametrize("needed", [1, 2])
def test_lookup_serves_both_lines(needed):
    t = lgt.load()
    p, n = lgt.lookup(t, 76, 1, 0.60, needed=needed)
    assert p is not None and n > 0


def test_lookup_rejects_unsupported_goal_counts():
    t = lgt.load()
    assert lgt.lookup(t, 76, 1, 0.60, needed=3) == (None, 0)
    assert lgt.lookup(t, 76, 1, 0.60, needed=0) == (None, 0)


def test_football_is_underdispersed_against_poisson_late_on():
    """The empirical two-goal line sits BELOW its Poisson value, and the gap
    widens as time runs out — two goals need time between them, which Poisson
    does not require. Pricing the leveraged line off a Poisson extrapolation
    therefore invents fair value; the table must carry a measured p2."""
    t = lgt.load()
    ratios = {}
    for m in (68, 86):
        c = t["cells"][f"{m}|1|all"]
        ratios[m] = c["p2"] / lgt.poisson_p2(lgt.implied_lambda(c["p"]))
    assert ratios[68] < 1.0 and ratios[86] < 1.0
    assert ratios[86] < ratios[68] - 0.1


def test_bucket_edges():
    assert lgt.bucket_of(0.40) == "lo"
    assert lgt.bucket_of(0.50) == "mid"
    assert lgt.bucket_of(0.62) == "hi"
    assert lgt.bucket_of(None) == "all"
