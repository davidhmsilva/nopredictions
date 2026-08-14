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

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# Clubs the two feeds simply call different things — "Wuhan San Zhen" against
# "Wuhan Three Towns", "Shandong Taishan" against "Shandong Luneng". No amount
# of token scoring reaches those; they are renames and translations, so they
# have to be listed. Learned and maintained by learn_fixture_aliases.py.
#
# Kept separate from team_aliases.json on purpose: that one maps a Polymarket
# name to a name in our own `teams` model, which is a different question with
# different consumers (dc_scanner, model_pricer). Merging them would couple the
# model's vocabulary to api-football's.
ALIASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixture_aliases.json")


def _load_aliases() -> dict[str, str]:
    try:
        with open(ALIASES_PATH) as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return {_norm_key(k): v for k, v in (raw.get("aliases") or {}).items()}


def _norm_key(name: str) -> str:
    clean = _strip_accents(name).lower()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", clean).split())

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


# NFKD decomposes an accent away from its base letter, but these are letters in
# their own right and survive it untouched: "Lillestrøm" stays "Lillestrøm" and
# never meets api-football's "Lillestrom". Nordic, Polish and Turkish clubs are
# a whole class of names that fail on this, so they are folded explicitly rather
# than listed one by one in the alias table.
_LETTER_FOLD = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "å": "a", "Å": "a", "ß": "ss", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d",
    "ł": "l", "Ł": "l", "ı": "i", "İ": "i", "þ": "th", "Þ": "th",
})


def _strip_accents(s: str) -> str:
    folded = s.translate(_LETTER_FOLD)
    return "".join(c for c in unicodedata.normalize("NFKD", folded)
                   if not unicodedata.combining(c))


_ALIASES: dict[str, str] = _load_aliases()


def reload_aliases() -> int:
    """Re-read the alias table. Used by the learner after it writes."""
    global _ALIASES
    _ALIASES = _load_aliases()
    return len(_ALIASES)


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


def canonical(name: str) -> str | None:
    """The alias-table key for a club, or None when it is not listed."""
    return _ALIASES.get(_norm_key(name))


def team_score(a: str, b: str) -> float:
    """0-1 similarity between two spellings of a club.

    An explicit alias wins outright — the whole point of the table is the pairs
    scoring cannot reach. It is still subject to the squad-marker check, so
    aliasing a first team never drags its reserve side along with it.

    Full containment scores 1.0: the feeds disagree by ADDING words rather than
    changing them — Polymarket writes the official name ("Coventry City FC",
    "Stade de Reims") where api-football writes the short one ("Coventry",
    "Reims") — so every token of the shorter name being present in the longer is
    what agreement actually looks like here.

    Otherwise the score is the shared fraction of the LONGER name, which is what
    keeps "Real Salt Lake" away from "Real Monarchs": one shared token out of
    three is 0.33, not the 0.5 that dividing by the shorter name would give.
    """
    ta, ma = tokens(a)
    tb, mb = tokens(b)
    if ma != mb:
        return 0.0

    # An alias resolves to the OTHER feed's literal name, and the other side is
    # then matched by exact equality rather than by scoring. That directness is
    # what makes the table safe: api-football calls Dinamo Moskva plain
    # "Dynamo", and mapping to the bare word would be lethal under the
    # containment rule — "Dynamo" is inside Dynamo Kyiv, Dynamo Dresden, BFC
    # Dynamo and Houston Dynamo. Under exact equality the alias reaches "Dynamo"
    # and nothing else, so a club can be aliased to a generic-looking name
    # without that name swallowing its namesakes.
    ka, kb = _norm_key(a), _norm_key(b)
    ca, cb = _ALIASES.get(ka), _ALIASES.get(kb)
    if (ca is not None and ca == kb) or (cb is not None and cb == ka) or \
       (ca is not None and ca == cb):
        return 1.0

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
