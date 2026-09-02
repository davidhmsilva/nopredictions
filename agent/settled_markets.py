"""
settled_markets.py — which PM football markets the score has ALREADY settled.

WHY THIS IS ITS OWN MODULE
--------------------------
The sweep strategy has no model risk and no price risk. Its only risk is this
file being wrong about what a market pays, and that error is not symmetric: a
wrong rule means buying at 0.06 something worth 0, believing it is worth 1. So
the rules live apart from the network, the DB and the daemon, and they are
unit-tested against fixtures rather than exercised only in production.

Two failures in this repo's history set the standards here:

  * db/038 — strategy 16 settled off `max()` over its own poll tape. A flap can
    only push a maximum UP, so every feed glitch became a fabricated WIN. Here
    the score comes from api-football's status/score block, never from a tape,
    never from PM's own outcomePrices ladder.

  * The one measured instance of a settlement rule being wrong on PM's own terms:
    wallet GSX- bought "Will Portugal vs. Croatia end in a draw?" at 0.003 after
    full time, and the market resolved NO. Extra time and penalties change what
    these questions pay. So every status that is not plain regulation football
    returns NOTHING here — see UNSAFE_STATUS.

EVERYTHING FAILS CLOSED. A question we cannot parse, a team we cannot place on a
side, a status we do not recognise, a score we do not have — all return None.
None means "we do not know", which is the correct and safe answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fixture_match import MIN_SIDE_SCORE, team_score

# api-football status codes.
#
# SAFE: plain regulation football, where the score alone settles the question.
# Nothing else is ever settled here. AET/PEN/ET/BT/P are excluded not because
# the score is unknown but because the RULE is unknown: PM's knockout markets
# vary in whether they pay on 90 minutes or on the tie, and getting that wrong
# inverts the position rather than blunting it.
STATUS_FIRST_HALF = "1H"
STATUS_HALFTIME = "HT"
STATUS_SECOND_HALF = "2H"
STATUS_FULLTIME = "FT"
SAFE_STATUS = frozenset({STATUS_FIRST_HALF, STATUS_HALFTIME,
                         STATUS_SECOND_HALF, STATUS_FULLTIME})
# Named so the exclusion is visible in code review rather than implied by absence.
UNSAFE_STATUS = frozenset({
    "ET", "BT", "P", "AET", "PEN",          # knockout: rule risk, see module docstring
    "SUSP", "INT", "PST", "CANC", "ABD",    # interrupted: the score is not final
    "AWD", "WO",                            # awarded/walkover: PM may void
    "TBD", "NS", "LIVE",                    # not started, or an unusable placeholder
})

# Phases, purely descriptive — they say WHERE in the match a reading was taken.
PHASE_IN_MATCH = "in_match"
PHASE_HALFTIME = "halftime"
PHASE_POST_WHISTLE = "post_whistle"


@dataclass(frozen=True)
class MatchState:
    """Everything the rules are allowed to know. api-football only."""
    status: str
    goals_home: int
    goals_away: int
    home: str
    away: str
    minute: int | None = None
    ht_home: int | None = None
    ht_away: int | None = None

    # ── derived, with the guards the rules depend on ─────────────────────────

    @property
    def safe(self) -> bool:
        return self.status in SAFE_STATUS

    @property
    def match_done(self) -> bool:
        """Regulation is over and the full-time score is the score."""
        return self.status == STATUS_FULLTIME

    @property
    def first_half_done(self) -> bool:
        """The first half has been played AND we hold its score.

        Both halves of that matter. api-football does not publish
        `score.halftime` during 1H, and a market settled on a half-time score we
        do not have would be settled on the running score instead.
        """
        return (self.status in (STATUS_HALFTIME, STATUS_SECOND_HALF, STATUS_FULLTIME)
                and self.ht_home is not None and self.ht_away is not None)

    @property
    def in_first_half(self) -> bool:
        return self.status == STATUS_FIRST_HALF

    @property
    def total(self) -> int:
        return self.goals_home + self.goals_away

    @property
    def ht_total(self) -> int | None:
        if self.ht_home is None or self.ht_away is None:
            return None
        return self.ht_home + self.ht_away

    @property
    def sh_home(self) -> int | None:
        """Second-half goals. None until the half-time score exists."""
        return None if self.ht_home is None else self.goals_home - self.ht_home

    @property
    def sh_away(self) -> int | None:
        return None if self.ht_away is None else self.goals_away - self.ht_away

    @property
    def phase(self) -> str:
        if self.match_done:
            return PHASE_POST_WHISTLE
        if self.status == STATUS_HALFTIME:
            return PHASE_HALFTIME
        return PHASE_IN_MATCH


@dataclass(frozen=True)
class Decision:
    rule: str
    winning_outcome: str    # the PM outcome label that is worth exactly 1


# ── question parsing ─────────────────────────────────────────────────────────
#
# Anchored at both ends on purpose. "O/U 9.5 Total Corners" must not match the
# goals total, and the only thing standing between the two is the `$`.

_NUM = r"(?P<line>\d+(?:\.\d+)?)"
_RE_FT_TOTAL = re.compile(rf"^O/U\s+{_NUM}$", re.I)
_RE_1H_TOTAL = re.compile(rf"^1st Half O/U\s+{_NUM}$", re.I)
_RE_2H_TOTAL = re.compile(rf"^2nd Half O/U\s+{_NUM}$", re.I)
_RE_BTTS = re.compile(r"^Both Teams to Score$", re.I)
_RE_BTTS_1H = re.compile(r"^Both Teams to Score in First Half$", re.I)
_RE_BTTS_2H = re.compile(r"^Both Teams to Score in Second Half$", re.I)
_RE_HT_DRAW = re.compile(r"^Draw at halftime\?$", re.I)
_RE_SH_DRAW = re.compile(r"^Second half draw\?$", re.I)
_RE_HT_LEAD = re.compile(r"^(?P<team>.+?) leading at halftime\?$", re.I)
_RE_SH_WIN = re.compile(r"^(?P<team>.+?) to win the second half\?$", re.I)
# ⚠️ The date in this question is NOT the fixture date — a board seen on
# 2026-09-02 carried "Will CD Coquimbo Unido win on 2026-07-25?". It is matched
# loosely and then discarded; nothing downstream may read it.
_RE_FT_WIN = re.compile(r"^Will (?P<team>.+?) win on \d{4}-\d{2}-\d{2}\?$", re.I)
_RE_FT_DRAW = re.compile(r"^Will (?P<a>.+?) vs\.?\s+(?P<b>.+?) end in a draw\?$", re.I)
_RE_EXACT = re.compile(
    r"^Exact Score:\s+(?P<a>.+?)\s+(?P<gh>\d+)\s*-\s*(?P<ga>\d+)\s+(?P<b>.+?)\?$", re.I)

YES, NO = "Yes", "No"
OVER, UNDER = "Over", "Under"

# Families that need a team name placed on a side. Kept as a set so a caller can
# ask for the score-only subset, which carries no side-error risk at all.
TEAM_BEARING_RULES = frozenset({
    "ht_lead", "sh_win", "ft_win", "exact_score",
})


def strip_fixture_prefix(question: str) -> str:
    """'A vs. B: Draw at halftime?' -> 'Draw at halftime?'.

    'Exact Score: ...' is left whole — its own prefix is the family marker.
    """
    q = (question or "").strip()
    if q.lower().startswith("exact score:"):
        return q
    return q.split(": ", 1)[1].strip() if ": " in q else q


def resolve_side(name: str, home: str, away: str) -> str | None:
    """'home' / 'away' for a PM team name, or None when it is not unambiguous.

    Alias-aware scoring against api-football's names, which are the authority on
    which side is which — never PM's title order. Ambiguity fails closed,
    because on these markets a side error inverts the position.

    Same rule as fav_pressure_agent.resolve_side; duplicated rather than
    imported so this module stays free of that agent's import graph.
    """
    h, a = team_score(name, home), team_score(name, away)
    if max(h, a) < MIN_SIDE_SCORE or h == a:
        return None
    return "home" if h > a else "away"


# ── the rules ────────────────────────────────────────────────────────────────

def _total_rule(rule: str, line: float, total: int | None, final: bool) -> Decision | None:
    """Over/Under on a goal total that may or may not be finished.

    Over settles the moment the total passes the line — that is the whole point,
    it is available mid-match. Under settles ONLY when the period is over,
    because a total below the line is not a result until no more goals can come.
    """
    if total is None:
        return None
    if total > line:
        return Decision(rule + "_over", OVER)
    if final and total < line:
        return Decision(rule + "_under", UNDER)
    return None


def _both_scored_rule(rule: str, h: int | None, a: int | None, final: bool) -> Decision | None:
    if h is None or a is None:
        return None
    if h > 0 and a > 0:
        return Decision(rule + "_yes", YES)
    if final:
        return Decision(rule + "_no", NO)
    return None


def decide(question: str, state: MatchState) -> Decision | None:
    """The outcome this question already pays, or None when it is not settled.

    None is the answer for anything unparsed, any unsafe status, any missing
    score and any ambiguous team. Callers must treat None as "no opinion" and
    never as "not yet".
    """
    if not state.safe:
        return None
    rem = strip_fixture_prefix(question)
    if not rem:
        return None

    # ── goal totals ─────────────────────────────────────────────────────────
    m = _RE_FT_TOTAL.match(rem)
    if m:
        return _total_rule("ft_total", float(m.group("line")),
                           state.total, state.match_done)

    m = _RE_1H_TOTAL.match(rem)
    if m:
        line = float(m.group("line"))
        if state.first_half_done:
            return _total_rule("h1_total", line, state.ht_total, True)
        if state.in_first_half:
            # Mid-first-half the running score IS the first-half score, so Over
            # can settle early. Under cannot: the half is not over.
            return _total_rule("h1_total", line, state.total, False)
        return None

    m = _RE_2H_TOTAL.match(rem)
    if m:
        if not state.first_half_done:
            return None
        sh_total = (state.sh_home or 0) + (state.sh_away or 0)
        return _total_rule("h2_total", float(m.group("line")),
                           sh_total, state.match_done)

    # ── both teams to score ─────────────────────────────────────────────────
    if _RE_BTTS.match(rem):
        return _both_scored_rule("btts", state.goals_home, state.goals_away,
                                 state.match_done)
    if _RE_BTTS_1H.match(rem):
        if state.first_half_done:
            return _both_scored_rule("btts_h1", state.ht_home, state.ht_away, True)
        if state.in_first_half:
            return _both_scored_rule("btts_h1", state.goals_home, state.goals_away, False)
        return None
    if _RE_BTTS_2H.match(rem):
        if not state.first_half_done:
            return None
        return _both_scored_rule("btts_h2", state.sh_home, state.sh_away,
                                 state.match_done)

    # ── half-time and second-half result ────────────────────────────────────
    if _RE_HT_DRAW.match(rem):
        if not state.first_half_done:
            return None
        return Decision("ht_draw", YES if state.ht_home == state.ht_away else NO)

    if _RE_SH_DRAW.match(rem):
        if not state.match_done or state.sh_home is None:
            return None
        return Decision("sh_draw", YES if state.sh_home == state.sh_away else NO)

    m = _RE_HT_LEAD.match(rem)
    if m:
        if not state.first_half_done:
            return None
        side = resolve_side(m.group("team").strip(), state.home, state.away)
        if side is None:
            return None
        lead = (state.ht_home > state.ht_away if side == "home"
                else state.ht_away > state.ht_home)
        return Decision("ht_lead", YES if lead else NO)

    m = _RE_SH_WIN.match(rem)
    if m:
        if not state.match_done or state.sh_home is None:
            return None
        side = resolve_side(m.group("team").strip(), state.home, state.away)
        if side is None:
            return None
        won = (state.sh_home > state.sh_away if side == "home"
               else state.sh_away > state.sh_home)
        return Decision("sh_win", YES if won else NO)

    # ── full-time result ────────────────────────────────────────────────────
    m = _RE_FT_WIN.match(rem)
    if m:
        if not state.match_done:
            return None
        side = resolve_side(m.group("team").strip(), state.home, state.away)
        if side is None:
            return None
        won = (state.goals_home > state.goals_away if side == "home"
               else state.goals_away > state.goals_home)
        return Decision("ft_win", YES if won else NO)

    m = _RE_FT_DRAW.match(rem)
    if m:
        if not state.match_done:
            return None
        # Both names are checked against the fixture rather than trusted: this
        # question carries no side, but it does carry the claim that it is THIS
        # match, and a merged PM board can hold a sibling fixture's market.
        sides = {resolve_side(m.group("a").strip(), state.home, state.away),
                 resolve_side(m.group("b").strip(), state.home, state.away)}
        if sides != {"home", "away"}:
            return None
        return Decision("ft_draw", YES if state.goals_home == state.goals_away else NO)

    # ── exact score ─────────────────────────────────────────────────────────
    m = _RE_EXACT.match(rem)
    if m:
        if resolve_side(m.group("a").strip(), state.home, state.away) != "home" \
                or resolve_side(m.group("b").strip(), state.home, state.away) != "away":
            return None
        gh, ga = int(m.group("gh")), int(m.group("ga"))
        # A score already exceeded on either side can never come back.
        if state.goals_home > gh or state.goals_away > ga:
            return Decision("exact_score_no", NO)
        if state.match_done:
            hit = state.goals_home == gh and state.goals_away == ga
            return Decision("exact_score", YES if hit else NO)
        return None

    return None
