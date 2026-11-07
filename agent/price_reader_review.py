#!/usr/bin/env python3
"""
Price Reader — the daily review. Once a day the agent reads its own record and
changes how it trades. The rules are fixed here, in advance, and nothing else
may change an arm: no model, no LLM, no hand-picked winner.

WHAT IT READS
-------------
Every settled position of every arm, booked or shadow (an arm's record does not
depend on whether it was on the tab), and the agent's tab itself (the booked
ones = its paper trades).

WHAT IT MAY CHANGE, AND THE EVIDENCE EACH NEEDS
-----------------------------------------------
  stop      An active arm with 50+ settled bets whose 90% interval on yield is
            wholly below zero stops booking. It keeps running in shadow, so the
            evidence keeps coming in.
  promote   A shadow arm takes over its group (its base arm and the variants of
            it) when, on the positions entered since the variant began,
              - both have 100+ settled bets,
              - its yield is above zero, and
              - it beats the group's active arm by more than 1.645 standard
                errors of the difference.
            The arm it replaces goes to shadow. With no active arm in the group
            (all stopped), a shadow arm comes back when its record since it was
            last moved has 100+ bets and a 90% interval wholly above zero.
  exclude   An active arm stops booking one sport, or one phase (before the
            start / in play), where that segment alone has 40+ bets and a 90%
            interval wholly below zero; it books it again when the last 30 days
            of that segment, in that arm, have 40+ bets and an interval wholly
            above zero. A phase is stored as "phase:live" / "phase:pre".
  cap       How many positions an active arm may open a day: 25 while it has
            fewer than 30 bets, then 10 + 30 × P(edge > 0), between 5 and 40.

An arm moved in the last COOLDOWN_D days is not moved again: one bad evening
must not swing it back and forth. 🔑 Promotion and resumption need a positive
interval or a clear margin, never "the best of the lot": picking the top of 21
noisy records is how the strategy factory went +11.6pp in training and −12.6pp
forward (strategy_factory).

Every change is written with its reason, and the day's diary goes to
price_reader_reviews, which the agent's page shows.

    python price_reader_review.py --dry-run    # what it would change, nothing written
    python price_reader_review.py              # the daily run (np-price-reader-review.timer)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_txn  # noqa: E402
import price_reader as pr  # noqa: E402

log = logging.getLogger("price_reader_review")

DATABASE_URL = os.getenv("DATABASE_URL")

STOP_MIN_N = 50
PROMOTE_MIN_N = 100
RESUME_MIN_N = 100
SPORT_MIN_N = 40
SPORT_RECENT_D = 30
COOLDOWN_D = 3
CAP_EXPLORE, CAP_EXPLORE_N = 25, 30
CAP_MIN, CAP_MAX = 5, 40
VERDICT_N = 200                 # CLAUDE.md rule 5: under this it is a record, not a result
Z90 = 1.645


def _settled(ps: list[dict]) -> list[dict]:
    return [p for p in ps if p["status"] in ("sold", "settled")]


def rec_of(ps: list[dict]) -> dict:
    s = _settled(ps)
    return pr.record([float(p["pl"]) for p in s], [float(p["stake"]) for p in s])


def _seg(key: str) -> str:
    return {"phase:live": "in-play bets", "phase:pre": "pre-match bets",
            "phase:post": "after-the-whistle bets"}.get(key, key)


def _pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{d}f}%"


def _ci(r: dict) -> str:
    if r.get("lo90") is None:
        return ""
    return f" (90%: {r['lo90'] * 100:+.1f}% to {r['hi90'] * 100:+.1f}%)"


def decide(arms: list[dict], positions: list[dict], now: datetime) -> tuple[list[dict], dict]:
    """(changes, per-arm records). Pure: the caller applies the changes.
    A change is {arm_id, key, what, field values…, why}."""
    by_arm: dict[int, list[dict]] = defaultdict(list)
    for p in positions:
        by_arm[p["arm_id"]].append(p)
    recs = {a["id"]: rec_of(by_arm[a["id"]]) for a in arms}
    changes: list[dict] = []
    cool = now - timedelta(days=COOLDOWN_D)
    moved: set[int] = set()

    def can_move(a):
        return a["id"] not in moved and a["mode_since"] <= cool

    groups: dict[str, list[dict]] = defaultdict(list)
    for a in arms:
        groups[a["base_key"]].append(a)

    for base, members in groups.items():
        active = [a for a in members if a["mode"] == "active"]
        shadow = [a for a in members if a["mode"] == "shadow"]

        # stop: an active arm clearly losing
        for a in active:
            r = recs[a["id"]]
            if r["n"] >= STOP_MIN_N and r["hi90"] is not None and r["hi90"] < 0 and can_move(a):
                changes.append({"arm_id": a["id"], "key": a["key"], "what": "stop", "mode": "shadow",
                                "why": f"{r['n']} settled bets, yield {_pct(r['yield'])}{_ci(r)}: "
                                       f"the whole range is below zero. It stops booking and keeps "
                                       f"being watched."})
                moved.add(a["id"])
        still_active = [a for a in active if a["id"] not in moved]

        # promote: a shadow arm clearly better than the active one, on the same days
        if still_active:
            cur = still_active[0]
            best = None
            for v in shadow:
                if not can_move(v) or not can_move(cur):
                    continue
                since = v["created_at"]
                rv = rec_of([p for p in by_arm[v["id"]] if p["entered_at"] >= since])
                rc = rec_of([p for p in by_arm[cur["id"]] if p["entered_at"] >= since])
                if rv["n"] < PROMOTE_MIN_N or rc["n"] < PROMOTE_MIN_N:
                    continue
                if rv["yield"] is None or rc["yield"] is None or rv["yield"] <= 0:
                    continue
                se = (rv["se"] ** 2 + rc["se"] ** 2) ** 0.5
                margin = rv["yield"] - rc["yield"]
                if margin > Z90 * se and (best is None or margin / se > best[0]):
                    best = (margin / se, v, rv, rc)
            if best:
                _, v, rv, rc = best
                changes.append({"arm_id": v["id"], "key": v["key"], "what": "promote", "mode": "active",
                                "why": f"on the same days it made {_pct(rv['yield'])} over {rv['n']} bets "
                                       f"against {_pct(rc['yield'])} over {rc['n']} for "
                                       f"\"{cur['label']}\", a margin larger than chance explains. "
                                       f"It takes over; the other is watched."})
                changes.append({"arm_id": cur["id"], "key": cur["key"], "what": "replaced", "mode": "shadow",
                                "why": f"replaced by \"{v['label']}\"."})
                moved.update({v["id"], cur["id"]})
        else:
            # resume: nothing in the group trades; one that has proved itself again comes back
            best = None
            for v in shadow:
                if not can_move(v):
                    continue
                r = rec_of([p for p in by_arm[v["id"]] if p["entered_at"] >= v["mode_since"]])
                if r["n"] >= RESUME_MIN_N and r["lo90"] is not None and r["lo90"] > 0:
                    if best is None or r["lo90"] > best[0]:
                        best = (r["lo90"], v, r)
            if best:
                _, v, r = best
                changes.append({"arm_id": v["id"], "key": v["key"], "what": "resume", "mode": "active",
                                "why": f"since it was last moved: {r['n']} bets, yield {_pct(r['yield'])}"
                                       f"{_ci(r)}, the whole range above zero. It books again."})
                moved.add(v["id"])

    # per sport, inside each arm that books (after today's mode changes)
    mode_after = {a["id"]: a["mode"] for a in arms}
    for c in changes:
        mode_after[c["arm_id"]] = c["mode"]
    recent = now - timedelta(days=SPORT_RECENT_D)
    for a in arms:
        if mode_after[a["id"]] != "active":
            continue
        excl = set(a.get("excluded_sports") or [])
        by_sport: dict[str, list[dict]] = defaultdict(list)
        for p in by_arm[a["id"]]:
            by_sport[p["sport"]].append(p)
            if p.get("phase"):
                by_sport[f"phase:{p['phase']}"].append(p)
        new_excl = set(excl)
        notes = []
        for sport, ps in sorted(by_sport.items()):
            if sport in excl:
                r = rec_of([p for p in ps if p["entered_at"] >= recent])
                if r["n"] >= SPORT_MIN_N and r["lo90"] is not None and r["lo90"] > 0:
                    new_excl.discard(sport)
                    notes.append(f"books {_seg(sport)} again: last {SPORT_RECENT_D} days {r['n']} bets, "
                                 f"{_pct(r['yield'])}{_ci(r)}")
            else:
                r = rec_of(ps)
                if r["n"] >= SPORT_MIN_N and r["hi90"] is not None and r["hi90"] < 0:
                    new_excl.add(sport)
                    notes.append(f"stops booking {_seg(sport)}: {r['n']} bets, {_pct(r['yield'])}{_ci(r)}")
        if new_excl != excl:
            changes.append({"arm_id": a["id"], "key": a["key"], "what": "sports",
                            "excluded_sports": sorted(new_excl), "why": "; ".join(notes) + "."})

    # daily caps
    for a in arms:
        r = recs[a["id"]]
        if mode_after[a["id"]] == "active":
            if r["n"] < CAP_EXPLORE_N:
                cap = CAP_EXPLORE
            else:
                pp = pr.prob_positive(r)
                cap = CAP_EXPLORE if pp is None else round(CAP_MIN + 5 + 30 * pp)
            cap = max(CAP_MIN, min(CAP_MAX, cap))
        else:
            cap = CAP_EXPLORE
        if cap != a["daily_cap"]:
            changes.append({"arm_id": a["id"], "key": a["key"], "what": "cap", "daily_cap": cap,
                            "why": f"{a['daily_cap']} → {cap} a day"
                                   + (f": P(edge > 0) ≈ {pr.prob_positive(r):.0%} on {r['n']} bets"
                                      if mode_after[a["id"]] == "active" and r["n"] >= CAP_EXPLORE_N
                                      and pr.prob_positive(r) is not None else ".")})
    return changes, recs


def diary(arms: list[dict], positions: list[dict], recs: dict, changes: list[dict],
          now: datetime, n_open: int, started: datetime | None) -> str:
    """The day in words, for the agent's page. Numbers a bettor uses first."""
    lab = {a["id"]: a["label"] for a in arms}
    booked = [p for p in positions if p["booked"]]
    tab = rec_of(booked)
    allr = rec_of(positions)
    day = (now.date() - started.date()).days + 1 if started else 1
    lines = [f"Day {day}. On the tab: {tab['n']} settled bets, {tab['pl']:+.2f}u, yield {_pct(tab['yield'])}"
             + (f"; {n_open} open." if n_open else ".")]
    if tab["n"] < VERDICT_N:
        lines.append(f"{tab['n']} of the {VERDICT_N} settled bets a verdict needs: a record, not a result yet.")
    else:
        lines.append(f"Past {VERDICT_N} bets{_ci(tab)}.")
    lines.append(f"Counting the approaches it only watches: {allr['n']} positions settled, "
                 f"yield {_pct(allr['yield'])}.")
    judged = [(a, recs[a["id"]]) for a in arms if recs[a["id"]]["n"] >= 20 and recs[a["id"]]["yield"] is not None]
    if judged:
        best = max(judged, key=lambda x: x[1]["yield"])
        worst = min(judged, key=lambda x: x[1]["yield"])
        lines.append(f"Best so far: {best[0]['label']}, {best[1]['n']} bets, {_pct(best[1]['yield'])}{_ci(best[1])}.")
        if worst[0]["id"] != best[0]["id"]:
            lines.append(f"Worst so far: {worst[0]['label']}, {worst[1]['n']} bets, "
                         f"{_pct(worst[1]['yield'])}{_ci(worst[1])}.")
    real = [c for c in changes if c["what"] != "cap"]
    if real:
        lines.append("Changed today:")
        verb = {"stop": "Stopped booking", "promote": "Promoted", "replaced": "Moved to watching",
                "resume": "Booking again", "sports": "Changed what it books for"}
        for c in real:
            lines.append(f"• {verb[c['what']]} \"{lab[c['arm_id']]}\": {c['why']}")
    else:
        need = min((STOP_MIN_N - recs[a["id"]]["n"] for a in arms if a["mode"] == "active"), default=0)
        lines.append("No change to what it trades today"
                     + (f": the first verdict on an approach needs {STOP_MIN_N} settled bets, "
                        f"the closest is {max(need, 0)} away." if need > 0 else ": nothing cleared the bar."))
    return "\n".join(lines)


# ── the database ─────────────────────────────────────────────────────────────

def _conn():
    return db_txn.connect(DATABASE_URL.replace(":5432/", ":6543/"))


def load(conn) -> tuple[list[dict], list[dict], int]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, key, base_key, label, params, mode, mode_since, daily_cap,
                              excluded_sports, created_at
                         FROM price_reader_arms WHERE mode <> 'stopped' ORDER BY id""")
        arms = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT arm_id, booked, sport, phase, status, pl::float8 AS pl, stake::float8 AS stake,
                              entered_at, clv::float8 AS clv
                         FROM price_reader_positions WHERE status IN ('sold', 'settled')""")
        positions = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT count(*) FROM price_reader_positions WHERE status = 'open' AND booked")
        n_open = cur.fetchone()["count"]
    return arms, positions, n_open


def apply(conn, changes: list[dict], summary: str, recs: dict, arms: list[dict], today: date) -> None:
    by_id = {a["id"]: a for a in arms}
    snapshot = []
    for a in arms:
        r = recs[a["id"]]
        snapshot.append({"key": a["key"], "label": a["label"], "base_key": a["base_key"], "mode": a["mode"],
                         "n": r["n"], "pl": r["pl"], "yield": r["yield"], "lo90": r["lo90"], "hi90": r["hi90"]})
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            for c in changes:
                note = f"{today.isoformat()}: {c['why']}"
                if "mode" in c:
                    cur.execute("""UPDATE price_reader_arms SET mode = %s, mode_since = now(), note = %s
                                    WHERE id = %s""", (c["mode"], note, c["arm_id"]))
                if "excluded_sports" in c:
                    cur.execute("""UPDATE price_reader_arms SET excluded_sports = %s, note = %s
                                    WHERE id = %s""", (c["excluded_sports"], note, c["arm_id"]))
                if "daily_cap" in c:
                    cur.execute("UPDATE price_reader_arms SET daily_cap = %s WHERE id = %s",
                                (c["daily_cap"], c["arm_id"]))
            cur.execute("""INSERT INTO price_reader_reviews (review_date, summary, arms, changes)
                           VALUES (%s, %s, %s::jsonb, %s::jsonb)
                           ON CONFLICT (review_date) DO UPDATE
                              SET summary = EXCLUDED.summary, arms = EXCLUDED.arms,
                                  changes = EXCLUDED.changes, created_at = now()""",
                        (today, summary, json.dumps(snapshot),
                         json.dumps([{**c, "label": by_id[c["arm_id"]]["label"]} for c in changes])))


def run(dry_run: bool = False) -> str:
    now = datetime.now(timezone.utc)
    conn = _conn()
    try:
        arms, positions, n_open = load(conn)
        if not arms:
            log.info("[review] no arms yet")
            return ""
        started = min(a["created_at"] for a in arms)
        changes, recs = decide(arms, positions, now)
        text = diary(arms, positions, recs, changes, now, n_open, started)
        log.info("[review]\n" + text)
        for c in changes:
            log.info(f"[review] {c['what']:<8} {c['key']}: {c['why']}")
        if not dry_run:
            apply(conn, changes, text, recs, arms, now.date())
        return text
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Price Reader daily review")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
    if not DATABASE_URL:
        log.error("DATABASE_URL missing")
        return 1
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
