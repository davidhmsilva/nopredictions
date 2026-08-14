"""Tests for PM <-> api-football fixture matching.

The regression cases at the top are real: both were produced by the previous
substring matcher on 2026-08-14 and both reported double-digit fake edges.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fixture_match import (  # noqa: E402
    best_match, pair_score, split_title, squads_agree, team_score,
)


# ── the two that actually happened ───────────────────────────────────────────

def test_river_plate_is_not_platense():
    """"plate" is a prefix of "platense", and "juniors" appears in both names.
    The old matcher joined these and reported +22.4pp of edge."""
    assert pair_score("CA River Plate", "AA Argentinos Juniors",
                      "Platense", "Boca Juniors") == 0.0


def test_first_team_is_not_the_reserve_side():
    """Real Salt Lake / Minnesota United vs their MLS Next Pro reserve sides.
    The old matcher joined these and reported +16.5pp."""
    assert pair_score("Real Salt Lake", "Minnesota United FC",
                      "Real Monarchs", "Minnesota United II") == 0.0


# ── real fixtures must still match ───────────────────────────────────────────

def test_abbreviations_still_meet_full_names():
    assert pair_score("Man City", "Spurs", "Manchester City", "Spurs") > 0
    assert team_score("Man City", "Manchester City") == 1.0


def test_club_suffixes_are_ignored():
    assert team_score("Arsenal FC", "Arsenal") == 1.0
    assert team_score("CA River Plate", "River Plate") == 1.0


def test_accents_are_ignored():
    assert team_score("FC Petrolul Ploiesti", "FC Petrolul Ploieşti") == 1.0


def test_a_genuine_pair_scores():
    assert pair_score("Arsenal FC", "Coventry City FC",
                      "Arsenal", "Coventry City") > 0.9


# ── squad markers ────────────────────────────────────────────────────────────

def test_reserve_and_youth_sides_never_match_the_first_team():
    assert not squads_agree("Minnesota United", "Minnesota United II")
    assert not squads_agree("Bayern Munich", "Bayern Munich Legends")
    assert not squads_agree("Chelsea", "Chelsea U21")
    assert squads_agree("Chelsea U21", "Chelsea U21")


def test_womens_fixtures_are_a_different_match():
    assert not squads_agree("Barcelona", "Barcelona Women")


# ── kickoff gate and ties ────────────────────────────────────────────────────

_CANDIDATES = {
    1: {"home": "Arsenal", "away": "Coventry City", "kickoff": "2026-08-21T19:00:00+00:00"},
    2: {"home": "Platense", "away": "Boca Juniors", "kickoff": "2026-08-21T19:00:00+00:00"},
}


def test_matches_the_right_fixture():
    assert best_match("Arsenal FC vs. Coventry City FC",
                      "2026-08-21T19:00:00Z", _CANDIDATES) == 1


def test_kickoff_gate_rejects_a_different_day():
    """Names can be made to agree; kick-off times cannot."""
    assert best_match("Arsenal FC vs. Coventry City FC",
                      "2026-08-28T19:00:00Z", _CANDIDATES) is None


def test_a_tie_returns_nothing():
    """Two fixtures scoring the same means the names cannot separate them.
    Picking either is exactly how the wrong-fixture comparison happened."""
    tied = {
        1: {"home": "Arsenal", "away": "Coventry", "kickoff": "2026-08-21T19:00:00+00:00"},
        2: {"home": "Arsenal", "away": "Coventry", "kickoff": "2026-08-21T19:00:00+00:00"},
    }
    assert best_match("Arsenal vs. Coventry", "2026-08-21T19:00:00Z", tied) is None


def test_unparseable_titles_return_nothing():
    assert split_title("some market with no versus") is None
    assert best_match("some market with no versus", None, _CANDIDATES) is None


# ── the feeds disagree by ADDING words, not changing them ────────────────────

def test_official_names_meet_short_ones():
    """Polymarket writes the official name, api-football the short one. This is
    the normal case and rejecting it cost 90% of the board on the first pass."""
    for pm, af in [
        ("Coventry City", "Coventry"),
        ("Stade de Reims", "Reims"),
        ("Viborg FF", "Viborg"),
        ("Atalanta BC", "Atalanta"),
        ("FC Sochaux-Montbéliard", "Sochaux"),
        ("Dijon Football Côte d'Or", "Dijon"),
        ("Red Star FC", "RED Star FC 93"),
        ("FK Orenburg", "FC Orenburg"),
    ]:
        assert team_score(pm, af) == 1.0, f"{pm} != {af}"


def test_reserve_notation_b_equals_ii():
    """The same reserve side is "Real Sociedad B" on PM and "Real Sociedad II"
    on api-football."""
    assert squads_agree("Real Sociedad de Fútbol B", "Real Sociedad II")
    assert pair_score("Real Sociedad de Fútbol B", "CD Castellón",
                      "Real Sociedad II", "Castellón") == 1.0


def test_containment_does_not_resurrect_platense():
    """The containment rule must not undo the regression it sits next to:
    "plate" is 5 characters, so it is not an abbreviation of "platense"."""
    assert team_score("River Plate", "Platense") < 0.6
    assert pair_score("CA River Plate", "AA Argentinos Juniors",
                      "Platense", "Boca Juniors") == 0.0


def test_abbreviations_are_short():
    assert team_score("Man City", "Manchester City") == 1.0     # 'man' is 3
    assert team_score("Sporting", "Sportivo Italiano") < 1.0    # 'sporting' is 8


# ── letter folding ───────────────────────────────────────────────────────────

def test_nordic_letters_fold():
    """NFKD leaves ø, æ and å intact — they are letters, not accented vowels —
    so "Lillestrøm" never met api-football's "Lillestrom" until they were folded
    explicitly. This was a whole class of clubs, not a handful."""
    for pm, af in [
        ("Lillestrøm SK", "Lillestrom"),
        ("Tromsø IL", "Tromso"),
        ("FC Nordsjælland", "FC Nordsjaelland"),
        ("Vålerenga Fotball", "Valerenga"),
    ]:
        assert team_score(pm, af) == 1.0, f"{pm} != {af}"


# ── the alias table ──────────────────────────────────────────────────────────

import fixture_match as fm  # noqa: E402


def _with_aliases(monkeypatch, mapping):
    monkeypatch.setattr(fm, "_ALIASES", {fm._norm_key(k): v for k, v in mapping.items()})


def test_alias_reaches_its_target(monkeypatch):
    _with_aliases(monkeypatch, {"Wolverhampton Wanderers FC": "wolves"})
    assert fm.team_score("Wolverhampton Wanderers FC", "Wolves") == 1.0


def test_alias_does_not_swallow_namesakes(monkeypatch):
    """The safety property the whole design turns on. api-football calls Dinamo
    Moskva plain "Dynamo", and under the containment rule a bare "Dynamo" is
    inside Dynamo Kyiv, Dynamo Dresden, BFC Dynamo and Houston Dynamo. Because
    the alias resolves to the literal name and is compared by EQUALITY, it
    reaches exactly one club."""
    _with_aliases(monkeypatch, {"FK Dinamo Moskva": "dynamo"})
    assert fm.team_score("FK Dinamo Moskva", "Dynamo") == 1.0
    for namesake in ("Dynamo Kyiv", "Dynamo Dresden", "BFC Dynamo", "Houston Dynamo"):
        assert fm.team_score("FK Dinamo Moskva", namesake) < 0.6, namesake


def test_alias_still_obeys_squad_markers(monkeypatch):
    """Aliasing a first team must never drag its reserve side along."""
    _with_aliases(monkeypatch, {"Wolverhampton Wanderers FC": "wolves"})
    assert fm.team_score("Wolverhampton Wanderers FC", "Wolves U21") == 0.0


def test_unaliased_names_are_unaffected(monkeypatch):
    _with_aliases(monkeypatch, {"Wolverhampton Wanderers FC": "wolves"})
    assert fm.team_score("Wollongong Wolves", "Wolverhampton Wanderers FC") == 0.0
