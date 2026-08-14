#!/usr/bin/env python3
"""
Learn PM <-> api-football club aliases from fixtures that pair themselves.

Some clubs the two feeds simply call different things — "Wuhan San Zhen" against
"Wuhan Three Towns", "Shandong Taishan" against "Shandong Luneng". Those are
renames and translations; no token scoring reaches them, so they have to be
listed. Writing that list by hand does not scale past a couple of leagues and
goes stale the moment a club is renamed.

The observation that makes it automatic: a fixture has two sides. When ONE side
matches with certainty and the kick-off pins the fixture to a single candidate,
the other pair is an alias by elimination — there is nothing else it could be.

The guards matter more than the idea, because a wrong alias is exactly the bug
this whole module exists to prevent (see fixture_match's docstring: substring
matching paired River Plate with Platense and reported +22pp of edge). A
proposal is only accepted when:

  * the anchor side scores a full 1.0, not merely well
  * exactly ONE candidate fixture survives the kick-off window, so the pairing
    is forced rather than chosen
  * no OTHER candidate has an anchor side that matches at all — if two fixtures
    both look anchored, the anchor is not distinctive
  * neither name is already aliased to something else
  * the two names do not already match, or the alias would be redundant
  * squad markers agree — a first team never aliases to its own reserve side

And then the invariant that makes the whole thing safe to run unattended:
applying the learned table may only ever ADD matches, never CHANGE one. Every
fixture that matched before must still match to the same api-football id
afterwards. An alias that rewrites an existing pairing is wrong by construction
and the batch is rejected.

Usage:
    python learn_fixture_aliases.py --days 3            # propose, show, do not write
    python learn_fixture_aliases.py --days 3 --apply    # verify invariant, then write
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

import fixture_match as fm                                    # noqa: E402
from pm_vs_pinnacle import fetch_fixtures_for_date, fetch_pm_fixtures  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                    datefmt="%H:%M:%S", force=True)
log = logging.getLogger("alias_learn")

# Tighter than the matcher's own gate. Learning should be conservative even
# where matching is permissive: a missed alias costs one fixture, a wrong one
# poisons every future comparison for that club.
LEARN_KICKOFF_WINDOW = timedelta(minutes=75)
ANCHOR_SCORE = 1.0


def _candidates(pm_home: str, pm_away: str, pm_ko, af_fixtures: dict) -> list[tuple[int, dict]]:
    """api-football fixtures kicking off close enough to be the same match."""
    out = []
    for fid, info in af_fixtures.items():
        ko = fm.parse_ts(info.get("kickoff"))
        if ko is None or pm_ko is None:
            continue
        if abs(ko - pm_ko) <= LEARN_KICKOFF_WINDOW:
            out.append((fid, info))
    return out


def _ambiguous(name: str, universe: set[str]) -> list[str]:
    """Distinct club names that normalise to the same key as this one.

    The alias resolves to api-football's literal name and is matched by exact
    equality, so a generic-looking target is not itself a problem — "Dynamo"
    reaches "Dynamo" and never Dynamo Kyiv. What WOULD be a problem is two
    genuinely different clubs whose names normalise identically, because then
    one key means two teams. That is what this checks.
    """
    key = fm._norm_key(name)
    return sorted({other for other in universe
                   if fm._norm_key(other) == key and other != name})


def propose(days: int) -> tuple[list[dict], dict]:
    af_fixtures: dict[int, dict] = {}
    for d in range(days):
        ds = (datetime.now(timezone.utc) + timedelta(days=d)).strftime("%Y-%m-%d")
        af_fixtures.update(fetch_fixtures_for_date(ds))
    pm_fixtures = fetch_pm_fixtures(days)
    log.info(f"PM {len(pm_fixtures)} fixtures · api-football {len(af_fixtures)} fixtures")

    # Every club name the feed uses in this window, for the ambiguity check.
    universe = {info[side] for info in af_fixtures.values()
                for side in ("home", "away") if info.get(side)}

    # What matches today, so the invariant can be checked against it later.
    before: dict[str, int] = {}
    for fx in pm_fixtures:
        fid = fm.best_match(fx["title"], fx.get("kickoff"), af_fixtures)
        if fid is not None:
            before[fx["title"]] = fid

    proposals: dict[tuple[str, str], dict] = {}
    for fx in pm_fixtures:
        if fx["title"] in before:
            continue                        # already matches; nothing to learn
        split = fm.split_title(fx["title"])
        if not split:
            continue
        pm_home, pm_away = split
        pm_ko = fm.parse_ts(fx.get("kickoff"))

        cands = _candidates(pm_home, pm_away, pm_ko, af_fixtures)
        if not cands:
            continue

        # Anchored on the home side, then on the away side.
        for anchor, other in (("home", "away"), ("away", "home")):
            pm_anchor = pm_home if anchor == "home" else pm_away
            pm_other = pm_away if anchor == "home" else pm_home

            hits = [(fid, info) for fid, info in cands
                    if fm.team_score(pm_anchor, info[anchor]) >= ANCHOR_SCORE]
            if len(hits) != 1:
                continue                    # not forced, or not distinctive

            # A second candidate that merely resembles the anchor means the
            # anchor is not distinctive enough to carry an inference.
            near = [fid for fid, info in cands
                    if fm.team_score(pm_anchor, info[anchor]) > 0
                    and fid != hits[0][0]]
            if near:
                continue

            fid, info = hits[0]
            af_other = info[other]
            # Redundant only if the pair ALREADY clears the matcher's bar. An
            # earlier version skipped on any score above zero, which threw away
            # most of the real work: "Bayern Munich" scores 0.5 against "Bayern
            # München" and "Sheffield United" 0.5 against "Sheffield Utd" —
            # partial agreement is precisely the case an alias is for.
            if fm.team_score(pm_other, af_other) >= fm.MIN_SIDE_SCORE:
                continue
            if not fm.squads_agree(pm_other, af_other):
                continue                    # first team vs its reserve side

            collides = _ambiguous(af_other, universe)
            if collides:
                log.warning(f"skip {pm_other!r} -> {af_other!r}: that name is shared "
                            f"by {collides}")
                continue

            existing = fm.canonical(pm_other)
            if existing is not None and existing != fm._norm_key(af_other):
                log.warning(f"skip {pm_other!r} -> {af_other!r}: already aliased "
                            f"to {existing!r}")
                continue

            key = (fm._norm_key(pm_other), fm._norm_key(af_other))
            entry = proposals.setdefault(key, {
                "pm": pm_other, "af": af_other,
                "league": info.get("league", ""), "evidence": [],
            })
            entry["evidence"].append(f"{fx['title']} ~ {info['home']} vs {info['away']}")

    return list(proposals.values()), {"pm_fixtures": pm_fixtures,
                                      "af_fixtures": af_fixtures,
                                      "before": before}


def _merge(proposals: list[dict]) -> dict:
    """Existing table plus the proposals, both sides pointing at one key."""
    try:
        with open(fm.ALIASES_PATH) as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        doc = {"_comment": "PM <-> api-football club aliases. See "
                           "learn_fixture_aliases.py. Both spellings map to one key.",
               "aliases": {}, "provenance": {}}

    doc.setdefault("aliases", {})
    doc.setdefault("provenance", {})
    for p in proposals:
        # Only the Polymarket spelling is stored, pointing at api-football's
        # literal name. Storing the reverse as well would make the target a key
        # in its own right, which is how a generic name like "Dynamo" would
        # start swallowing its namesakes.
        key = fm._norm_key(p["af"])
        doc["aliases"][p["pm"]] = key
        doc["provenance"][key] = {
            "learned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "league": p["league"],
            "evidence": p["evidence"][:3],
        }
    return doc


def _verify_invariant(doc: dict, ctx: dict) -> tuple[bool, list[str]]:
    """Applying aliases may ADD matches, never CHANGE one.

    Written to a temp path and loaded, so the check runs against exactly the
    file that would be shipped rather than an in-memory approximation.
    """
    tmp = fm.ALIASES_PATH + ".candidate"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False, sort_keys=True)

    real = fm.ALIASES_PATH
    violations: list[str] = []
    try:
        fm.ALIASES_PATH = tmp
        fm.reload_aliases()
        for title, old_fid in ctx["before"].items():
            fx = next(f for f in ctx["pm_fixtures"] if f["title"] == title)
            new_fid = fm.best_match(title, fx.get("kickoff"), ctx["af_fixtures"])
            if new_fid != old_fid:
                violations.append(f"{title}: {old_fid} -> {new_fid}")
    finally:
        fm.ALIASES_PATH = real
        fm.reload_aliases()
        os.unlink(tmp)

    return (not violations), violations


def main() -> None:
    ap = argparse.ArgumentParser(description="Learn PM <-> api-football club aliases")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--apply", action="store_true", help="write the table after verifying")
    args = ap.parse_args()

    proposals, ctx = propose(args.days)
    matched_before = len(ctx["before"])
    total = len(ctx["pm_fixtures"])

    print(f"\nmatched before: {matched_before}/{total} PM fixtures")
    if not proposals:
        print("no new aliases proposed")
        return

    print(f"\n{len(proposals)} proposed aliases:")
    for p in sorted(proposals, key=lambda x: -len(x["evidence"])):
        print(f"  {p['pm'][:34]:34} == {p['af'][:30]:30} "
              f"[{p['league'][:20]:20}] x{len(p['evidence'])}")
        print(f"      {p['evidence'][0]}")

    doc = _merge(proposals)
    ok, violations = _verify_invariant(doc, ctx)
    if not ok:
        print(f"\nREJECTED — {len(violations)} fixture(s) would change their match:")
        for v in violations[:10]:
            print(f"  {v}")
        print("An alias that rewrites an existing pairing is wrong. Nothing written.")
        raise SystemExit(1)
    print(f"\ninvariant holds: all {matched_before} existing matches unchanged")

    if not args.apply:
        print("(dry run — pass --apply to write)")
        return

    with open(fm.ALIASES_PATH, "w") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False, sort_keys=True)
    fm.reload_aliases()

    after = sum(1 for fx in ctx["pm_fixtures"]
                if fm.best_match(fx["title"], fx.get("kickoff"), ctx["af_fixtures"]) is not None)
    print(f"wrote {fm.ALIASES_PATH}")
    print(f"matched after: {after}/{total}  (+{after - matched_before})")


if __name__ == "__main__":
    main()
