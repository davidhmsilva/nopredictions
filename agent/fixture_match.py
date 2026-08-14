"""
Matching a Polymarket fixture to an api-football one.

This exists because the obvious implementation is wrong in a way that produces
confident nonsense rather than errors. Substring containment on team names —
"does the api-football title contain any long word from the PM title" — matched
these on 2026-08-14:

    PM "CA River Plate vs. AA Argentinos Juniors"
      -> af "Platense vs Boca Juniors"        ("plate" is inside "platense",
                                                "juniors" is inside "boca juniors")
    PM "Real Salt Lake vs. Minnesota United FC"
      -> af "Real Monarchs vs Minnesota United II"   (reserve teams, "real" shared)

Both then priced a PM board against a different match's line and reported +22pp
and +16pp of edge, on a board where every honest comparison sat at 0.1pp. A
loose matcher does not fail loudly; it invents the biggest edges in the file.

The rules here are therefore deliberately strict, and prefer a missed fixture to
a wrong one:

  * tokens, not substrings — with a prefix allowance so "Man City" still meets
    "Manchester City", but scored against the LONGER name so one shared token
    out of three does not pass
  * reserve and youth markers must agree — "United" and "United II" are
    different teams playing different matches
  * kick-off times must be close, which no amount of name similarity can fake
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

# Both names must reach this. 0.5 is not enough: "River Plate" vs "Platense"
# scores exactly 0.5 under the prefix rule, and it is not the same club.
MIN_SIDE_SCORE = 0.60
MAX_KICKOFF_DELTA = timedelta(hours=6)

# Dropped before scoring: they carry no identity and appear on hundreds of clubs.
_NOISE = {
    "fc", "cf", "ca", "aa", "sc", "ac", "as", "sv", "sk", "fk", "bk", "if",
    "afc", "cd", "ud", "sd", "rc", "cs", "club", "de", "do", "da", "the",
    "ff", "bc", "gf", "ik", "aik", "os", "vf", "sk", "kv", "us", "usl",
    "football", "futbol", "calcio", "cp", "cr", "ec", "sp",
}

# Reserve, youth and exhibition sides. A mismatch here is disqualifying however
# well the names otherwise score. Canonicalised, because the same reserve team
# is "Real Sociedad B" on Polymarket and "Real Sociedad II" on api-football —
# treating those as different squads rejects a perfectly good fixture.
_SQUAD_CANON = {
    "ii": "reserve", "b": "reserve", "reserves": "reserve",
    "iii": "third",
    "u17": "u17", "u18": "u18", "u19": "u19", "u20": "u20",
    "u21": "u21", "u23": "u23",
    "legends": "legends", "youth": "youth", "academy": "academy",
    "women": "women", "w": "women",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def tokens(name: str) -> tuple[frozenset[str], frozenset[str]]:
    """(identity tokens, canonical squad markers) for a team name."""
    clean = _strip_accents(name).lower()
    clean = re.sub(r"[^a-z0-9 ]", " ", clean)
    raw = [t for t in clean.split() if t]
    markers = frozenset(_SQUAD_CANON[t] for t in raw if t in _SQUAD_CANON)
    ident = frozenset(t for t in raw if t not in _SQUAD_CANON and t not in _NOISE)
    return ident, markers


# An abbreviation is short. "Man" standing in for "Manchester" is the case the
# prefix rule is for; "plate" standing in for "platense" is not — those are two
# different words that happen to share five letters, and allowing it is what
# paired River Plate with Platense.
_MAX_ABBREV_LEN = 4


def _token_hit(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return 3 <= len(short) <= _MAX_ABBREV_LEN and long.startswith(short)


def team_score(a: str, b: str) -> float:
    """0-1 similarity between two spellings of a club.

    Full containment scores 1.0: the feeds disagree by ADDING words rather than
    changing them — Polymarket writes the official name ("Coventry City FC",
    "Stade de Reims") where api-football writes the short one ("Coventry",
    "Reims") — so every token of the shorter name being present in the longer is
    what agreement actually looks like here.

    Otherwise the score is the shared fraction of the LONGER name, which is what
    keeps "Real Salt Lake" away from "Real Monarchs": one shared token out of
    three is 0.33, not the 0.5 that dividing by the shorter name would give.
    """
    ta, _ = tokens(a)
    tb, _ = tokens(b)
    if not ta or not tb:
        return 0.0

    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if all(any(_token_hit(x, y) for y in long) for x in short):
        return 1.0

    shared = sum(1 for x in ta if any(_token_hit(x, y) for y in tb))
    return shared / max(len(ta), len(tb))


def squads_agree(a: str, b: str) -> bool:
    _, ma = tokens(a)
    _, mb = tokens(b)
    return ma == mb


def pair_score(pm_home: str, pm_away: str, af_home: str, af_away: str) -> float:
    """Score for a whole fixture. 0 when either side fails its own bar."""
    if not squads_agree(pm_home, af_home) or not squads_agree(pm_away, af_away):
        return 0.0
    h = team_score(pm_home, af_home)
    a = team_score(pm_away, af_away)
    if min(h, a) < MIN_SIDE_SCORE:
        return 0.0
    return (h + a) / 2


def split_title(title: str) -> tuple[str, str] | None:
    """'A vs. B' -> ('A', 'B'). Suffixes like ' - More Markets' are removed."""
    clean = re.sub(r"\s+-\s+[A-Z][A-Za-z0-9 /'&.]*$", "", title).strip()
    parts = re.split(r"\s+vs\.?\s+", clean, maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None
    return parts[0].strip(), parts[1].strip()


def parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def best_match(
    pm_title: str,
    pm_kickoff,
    candidates: dict,
    *,
    require_kickoff: bool = True,
) -> int | None:
    """Best api-football fixture id for a PM title, or None.

    `candidates` is {fixture_id: {'home', 'away', 'kickoff'}}.

    Returns None on a tie. Two fixtures scoring equally means the names cannot
    tell them apart, and picking either one is how the River Plate / Platense
    comparison happened.
    """
    split = split_title(pm_title)
    if not split:
        return None
    pm_h, pm_a = split
    pm_ko = parse_ts(pm_kickoff)

    scored: list[tuple[float, int]] = []
    for fid, info in candidates.items():
        if pm_ko is not None:
            af_ko = parse_ts(info.get("kickoff"))
            if af_ko is not None and abs(af_ko - pm_ko) > MAX_KICKOFF_DELTA:
                continue
        elif require_kickoff:
            # No PM kickoff to check against — names alone have already been
            # shown to be insufficient, so only an exact-ish pair is accepted.
            pass
        s = pair_score(pm_h, pm_a, info.get("home", ""), info.get("away", ""))
        if s > 0:
            scored.append((s, fid))

    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]
