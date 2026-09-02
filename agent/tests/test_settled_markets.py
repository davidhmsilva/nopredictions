"""
Tests for the deterministic settlement rules.

The asymmetry these guard against: a rule that wrongly claims a token is worth 1
makes us buy a worthless token at 6 cents believing it is a 16x. A rule that
wrongly says "not settled" costs nothing but a missed trade. So most of these
tests are about the rules REFUSING to answer.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from settled_markets import (  # noqa: E402
    MatchState, decide, resolve_side, strip_fixture_prefix,
    PHASE_HALFTIME, PHASE_IN_MATCH, PHASE_POST_WHISTLE,
)

HOME, AWAY = "Arsenal", "Chelsea"


def st(status, gh, ga, ht=None, minute=None):
    hh, ha = (ht if ht else (None, None))
    return MatchState(status=status, goals_home=gh, goals_away=ga,
                      home=HOME, away=AWAY, minute=minute,
                      ht_home=hh, ht_away=ha)


def q(rest):
    return f"{HOME} vs. {AWAY}: {rest}"


# ── the guards that matter most ──────────────────────────────────────────────

@pytest.mark.parametrize("status", ["ET", "BT", "P", "AET", "PEN", "SUSP", "INT",
                                    "PST", "CANC", "ABD", "AWD", "WO", "NS", "TBD"])
def test_unsafe_status_never_settles_anything(status):
    """Knockout and interrupted matches are refused outright.

    This is the Portugal-Croatia case: a "draw?" market bought at 0.003 after
    the whistle that resolved NO because the tie went past regulation.
    """
    s = st(status, 3, 0, ht=(2, 0))
    for rest in ["O/U 2.5", "1st Half O/U 0.5", "Both Teams to Score",
                 "Draw at halftime?", f"Will {HOME} win on 2026-09-02?"]:
        assert decide(q(rest), s) is None, rest


def test_corners_markets_never_match_the_goal_totals():
    """The only thing separating these two questions is the anchor."""
    s = st("FT", 4, 3, ht=(2, 1))
    assert decide(q("O/U 9.5 Total Corners"), s) is None
    assert decide(q("1st Half O/U 4.5 Total Corners"), s) is None
    assert decide(q("2nd Half O/U 4.5 Total Corners"), s) is None
    assert decide(q("Total Corners Odd or Even?"), s) is None
    assert decide(q("Team to Take First Corner"), s) is None
    # ...and the real one still works
    assert decide(q("O/U 2.5"), s).winning_outcome == "Over"


def test_unparsed_questions_return_none():
    s = st("FT", 2, 1, ht=(1, 0))
    for rest in ["Any Other Score?", "Neither team to score first?",
                 "Team to Advance", "Method of Victory", "Spread: Arsenal (-1.5)",
                 "", "O/U", "O/U abc"]:
        assert decide(q(rest), s) is None, rest


def test_ambiguous_team_fails_closed():
    """A team we cannot place on a side is never settled: the error inverts."""
    s = st("FT", 2, 0, ht=(1, 0))
    assert decide("Will Some Unrelated FC win on 2026-09-02?", s) is None
    assert decide("Some Unrelated FC leading at halftime?", s) is None


def test_missing_halftime_score_blocks_every_halftime_rule():
    """During 2H api-football has the HT score; if we do not hold it, we refuse.

    Without this guard a half-time market would silently settle on the RUNNING
    score, which is a different number as soon as the second half has a goal.
    """
    s = st("2H", 3, 1, ht=None, minute=60)
    for rest in ["1st Half O/U 1.5", "Draw at halftime?",
                 f"{HOME} leading at halftime?", "Both Teams to Score in First Half",
                 "2nd Half O/U 0.5", "Both Teams to Score in Second Half"]:
        assert decide(q(rest), s) is None, rest


# ── goal totals ──────────────────────────────────────────────────────────────

def test_ft_over_settles_mid_match_but_under_does_not():
    live = st("2H", 2, 1, ht=(1, 0), minute=70)
    assert decide(q("O/U 2.5"), live).winning_outcome == "Over"   # 3 > 2.5
    assert decide(q("O/U 3.5"), live) is None                      # not yet, and not final
    done = st("FT", 2, 1, ht=(1, 0))
    assert decide(q("O/U 3.5"), done).winning_outcome == "Under"


def test_over_on_the_exact_line_is_not_settled():
    """Totals are half-lines in practice, but an integer line must not be
    resolved by a strict > that would silently treat a push as a win."""
    s = st("FT", 1, 1, ht=(1, 0))
    assert decide(q("O/U 2"), s) is None      # total == line: neither side


def test_first_half_over_settles_during_the_first_half():
    s = st("1H", 1, 0, minute=20)
    assert decide(q("1st Half O/U 0.5"), s).winning_outcome == "Over"
    assert decide(q("1st Half O/U 1.5"), s) is None       # the half is not over
    at_ht = st("HT", 1, 0, ht=(1, 0))
    assert decide(q("1st Half O/U 1.5"), at_ht).winning_outcome == "Under"


def test_first_half_market_uses_the_halftime_score_not_the_running_one():
    """0-0 at the break, 3-0 at the hour: the 1st-half Over 0.5 pays Under."""
    s = st("2H", 3, 0, ht=(0, 0), minute=60)
    assert decide(q("1st Half O/U 0.5"), s).winning_outcome == "Under"


def test_second_half_totals():
    s = st("2H", 3, 1, ht=(1, 1), minute=75)          # 2 second-half goals
    assert decide(q("2nd Half O/U 1.5"), s).winning_outcome == "Over"
    assert decide(q("2nd Half O/U 2.5"), s) is None
    done = st("FT", 3, 1, ht=(1, 1))
    assert decide(q("2nd Half O/U 2.5"), done).winning_outcome == "Under"


# ── both teams to score ──────────────────────────────────────────────────────

def test_btts_yes_settles_early_no_only_at_the_end():
    live = st("2H", 1, 1, ht=(1, 0), minute=55)
    assert decide(q("Both Teams to Score"), live).winning_outcome == "Yes"
    live_one = st("2H", 2, 0, ht=(1, 0), minute=55)
    assert decide(q("Both Teams to Score"), live_one) is None
    done = st("FT", 2, 0, ht=(1, 0))
    assert decide(q("Both Teams to Score"), done).winning_outcome == "No"


def test_btts_second_half_uses_second_half_goals_only():
    """1-1 at the break and 2-1 at the end: both scored, but not in H2."""
    done = st("FT", 2, 1, ht=(1, 1))
    assert decide(q("Both Teams to Score in Second Half"), done).winning_outcome == "No"
    both = st("FT", 2, 2, ht=(1, 1))
    assert decide(q("Both Teams to Score in Second Half"), both).winning_outcome == "Yes"


# ── result markets ───────────────────────────────────────────────────────────

def test_halftime_result_markets():
    s = st("HT", 1, 0, ht=(1, 0))
    assert decide(q("Draw at halftime?"), s).winning_outcome == "No"
    assert decide(f"{HOME} leading at halftime?", s).winning_outcome == "Yes"
    assert decide(f"{AWAY} leading at halftime?", s).winning_outcome == "No"
    level = st("HT", 1, 1, ht=(1, 1))
    assert decide(q("Draw at halftime?"), level).winning_outcome == "Yes"
    assert decide(f"{HOME} leading at halftime?", level).winning_outcome == "No"


def test_halftime_lead_is_resolved_against_the_fixture_not_the_title_order():
    """The market names the AWAY team; the answer must follow the score, not
    the position of the name in PM's title."""
    s = st("HT", 0, 2, ht=(0, 2))
    assert decide(f"{AWAY} leading at halftime?", s).winning_outcome == "Yes"
    assert decide(f"{HOME} leading at halftime?", s).winning_outcome == "No"


def test_full_time_result_markets():
    s = st("FT", 2, 1, ht=(1, 1))
    assert decide(f"Will {HOME} win on 2026-09-02?", s).winning_outcome == "Yes"
    assert decide(f"Will {AWAY} win on 2026-09-02?", s).winning_outcome == "No"
    assert decide(f"Will {HOME} vs. {AWAY} end in a draw?", s).winning_outcome == "No"
    draw = st("FT", 1, 1, ht=(1, 0))
    assert decide(f"Will {HOME} vs. {AWAY} end in a draw?", draw).winning_outcome == "Yes"


def test_result_markets_do_not_settle_before_full_time():
    s = st("2H", 2, 1, ht=(1, 1), minute=80)
    assert decide(f"Will {HOME} win on 2026-09-02?", s) is None
    assert decide(f"Will {HOME} vs. {AWAY} end in a draw?", s) is None
    assert decide(q("Second half draw?"), s) is None
    assert decide(f"{HOME} to win the second half?", s) is None


def test_draw_market_naming_a_different_fixture_is_refused():
    """PM boards get merged by title; a sibling's market must not be settled."""
    s = st("FT", 1, 1, ht=(0, 0))
    assert decide("Will Real Madrid CF vs. FC Barcelona end in a draw?", s) is None


def test_second_half_winner():
    done = st("FT", 3, 1, ht=(1, 1))                  # H2 was 2-0 to home
    assert decide(f"{HOME} to win the second half?", done).winning_outcome == "Yes"
    assert decide(f"{AWAY} to win the second half?", done).winning_outcome == "No"
    assert decide(q("Second half draw?"), done).winning_outcome == "No"


# ── exact score ──────────────────────────────────────────────────────────────

def test_exact_score_dies_as_soon_as_it_is_exceeded():
    s = st("2H", 1, 0, ht=(1, 0), minute=55)
    assert decide(f"Exact Score: {HOME} 0 - 0 {AWAY}?", s).winning_outcome == "No"
    assert decide(f"Exact Score: {HOME} 0 - 1 {AWAY}?", s).winning_outcome == "No"
    # still reachable, so not settled
    assert decide(f"Exact Score: {HOME} 1 - 0 {AWAY}?", s) is None
    assert decide(f"Exact Score: {HOME} 2 - 1 {AWAY}?", s) is None


def test_exact_score_settles_both_ways_at_full_time():
    s = st("FT", 1, 0, ht=(1, 0))
    assert decide(f"Exact Score: {HOME} 1 - 0 {AWAY}?", s).winning_outcome == "Yes"
    assert decide(f"Exact Score: {HOME} 1 - 1 {AWAY}?", s).winning_outcome == "No"


def test_exact_score_with_a_number_inside_the_team_name():
    """'Bohemians Praha 1905 0 - 0 FK Jablonec?' — the ' - ' is the anchor."""
    s = MatchState(status="2H", goals_home=1, goals_away=0,
                   home="Bohemians 1905", away="FK Jablonec", minute=50)
    d = decide("Exact Score: Bohemians Praha 1905 0 - 0 FK Jablonec?", s)
    assert d is not None and d.winning_outcome == "No"


def test_exact_score_orientation_must_match_the_fixture():
    """Names reversed relative to api-football's home/away: refuse rather than
    settle a mirrored score."""
    s = st("FT", 1, 0, ht=(1, 0))
    assert decide(f"Exact Score: {AWAY} 1 - 0 {HOME}?", s) is None


# ── plumbing ─────────────────────────────────────────────────────────────────

def test_strip_fixture_prefix():
    assert strip_fixture_prefix(q("O/U 2.5")) == "O/U 2.5"
    assert strip_fixture_prefix("Arsenal leading at halftime?") == "Arsenal leading at halftime?"
    assert strip_fixture_prefix("Exact Score: A 0 - 0 B?") == "Exact Score: A 0 - 0 B?"


def test_phase_labels():
    assert st("1H", 0, 0, minute=20).phase == PHASE_IN_MATCH
    assert st("HT", 0, 0, ht=(0, 0)).phase == PHASE_HALFTIME
    assert st("2H", 0, 0, ht=(0, 0), minute=70).phase == PHASE_IN_MATCH
    assert st("FT", 0, 0, ht=(0, 0)).phase == PHASE_POST_WHISTLE


def test_resolve_side_is_ambiguity_safe():
    assert resolve_side(HOME, HOME, AWAY) == "home"
    assert resolve_side(AWAY, HOME, AWAY) == "away"
    assert resolve_side("Nowhere United", HOME, AWAY) is None
    assert resolve_side(HOME, HOME, HOME) is None      # identical sides
