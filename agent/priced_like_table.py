"""
"Matches priced like this one" — the Game Center's measured base rates.

Given the price of ONE market (the de-vigged Pinnacle closing Over 2.5, or the
de-vigged Pinnacle closing home win), what did every other market do in the
matches the sharpest book priced the same way? That is the one comparison on the
Game Center that no stats site can make: they have results, we have results
joined to closing prices.

Also writes per-league base rates, so a streak on the page can say how often a
run like it happens by chance ("BTTS in 7 straight — the league rate is 52%").

Why a static table and not a query: the bucketed read takes ~7s on the pooler,
and the answer moves only when a season of matches lands. Same convention as
first_half.json and late_goals.json.

    python priced_like_table.py            # rebuild site/app/lib/priced_like.json
    python priced_like_table.py --dry-run  # print, write nothing

⚠️ De-vig is proportional on BOTH sides of the O/U and across all three 1X2
outcomes. Pinnacle's closing overround is small (~2-3%), so the method choice
moves a bucket by well under a point — but the page compares these rates to
Polymarket ASKS, which carry spread and fee, and that comparison is only fair if
it is said out loud. The page does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "app" / "lib" / "priced_like.json"

TOTAL_STEP = 0.02      # Over 2.5 bucket width
HOME_STEP = 0.05       # home-win bucket width
MIN_N = 300            # a bucket below this is not published
LEAGUE_SINCE = "2023-07-01"
LEAGUE_MIN_MATCHES = 150


def _rate(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def fetch(cur):
    cur.execute(
        """
        select m.id, m.kickoff_utc, l.name,
               m.home_score, m.away_score, m.home_score_ht, m.away_score_ht,
               pc.home_odds, pc.draw_odds, pc.away_odds, pc.over_2_5_odds, pc.under_2_5_odds
        from matches m
        join seasons s on s.id = m.season_id
        join leagues l on l.id = s.league_id
        left join lateral (
            select mo.home_odds, mo.draw_odds, mo.away_odds, mo.over_2_5_odds, mo.under_2_5_odds
            from match_odds mo
            join bookmakers b on b.id = mo.bookmaker_id
            where mo.match_id = m.id and mo.snapshot_type = 'closing' and b.name = 'Pinnacle (closing)'
            limit 1
        ) pc on true
        where m.home_score is not null and m.away_score is not null
          and not coalesce(l.is_international, false)
          and l.name <> 'NBA'
        """
    )
    return cur.fetchall()


def build(rows):
    totals = defaultdict(list)
    homes = defaultdict(list)
    league_rows = defaultdict(list)

    for (_id, ko, league, hs, as_, hht, aht, ho, do, ao, oo, uo) in rows:
        # numeric columns arrive as Decimal; the arithmetic below is float
        ho, do, ao, oo, uo = (float(x) if x is not None else None for x in (ho, do, ao, oo, uo))
        has_ht = hht is not None and aht is not None
        rec = dict(hs=hs, as_=as_, hht=hht, aht=aht, has_ht=has_ht)

        if oo and uo and oo > 1 and uo > 1:
            p = (1 / oo) / ((1 / oo) + (1 / uo))
            totals[int(p / TOTAL_STEP)].append(rec)
        if ho and do and ao and ho > 1 and do > 1 and ao > 1:
            s = 1 / ho + 1 / do + 1 / ao
            ph = (1 / ho) / s
            homes[int(ph / HOME_STEP)].append(rec)

        if ko and ko.isoformat() >= LEAGUE_SINCE:
            league_rows[league].append(rec)

    def totals_row(k, rs):
        ht = [r for r in rs if r["has_ht"]]
        g = [r["hs"] + r["as_"] for r in rs]
        return {
            "lo": round(k * TOTAL_STEP, 2), "hi": round((k + 1) * TOTAL_STEP, 2),
            "n": len(rs), "n_ht": len(ht),
            "o15": _rate([x > 1 for x in g]),
            "o25": _rate([x > 2 for x in g]),
            "o35": _rate([x > 3 for x in g]),
            "btts": _rate([r["hs"] > 0 and r["as_"] > 0 for r in rs]),
            "ht_o05": _rate([r["hht"] + r["aht"] > 0 for r in ht]),
            "ht_o15": _rate([r["hht"] + r["aht"] > 1 for r in ht]),
            "sh_o05": _rate([(r["hs"] + r["as_"]) - (r["hht"] + r["aht"]) > 0 for r in ht]),
            "avg_goals": _mean(g),
            "avg_ht_goals": _mean([r["hht"] + r["aht"] for r in ht]),
        }

    def home_row(k, rs):
        ht = [r for r in rs if r["has_ht"]]
        return {
            "lo": round(k * HOME_STEP, 2), "hi": round((k + 1) * HOME_STEP, 2),
            "n": len(rs), "n_ht": len(ht),
            "home": _rate([r["hs"] > r["as_"] for r in rs]),
            "draw": _rate([r["hs"] == r["as_"] for r in rs]),
            "away": _rate([r["hs"] < r["as_"] for r in rs]),
            "ht_home": _rate([r["hht"] > r["aht"] for r in ht]),
            "ht_draw": _rate([r["hht"] == r["aht"] for r in ht]),
            "ht_away": _rate([r["hht"] < r["aht"] for r in ht]),
            "home_scores": _rate([r["hs"] > 0 for r in rs]),
            "away_scores": _rate([r["as_"] > 0 for r in rs]),
            "btts": _rate([r["hs"] > 0 and r["as_"] > 0 for r in rs]),
        }

    # League base rates at the TEAM-MATCH level — every match counted once from
    # each side — because that is the unit a team's streak is made of.
    def league_row(rs):
        tm = []
        for r in rs:
            for gf, ga, hf, ha in ((r["hs"], r["as_"], r["hht"], r["aht"]),
                                   (r["as_"], r["hs"], r["aht"], r["hht"])):
                tm.append(dict(gf=gf, ga=ga, hf=hf, ha=ha, has_ht=r["has_ht"]))
        ht = [t for t in tm if t["has_ht"]]
        return {
            "n": len(rs),
            "win": _rate([t["gf"] > t["ga"] for t in tm]),
            "draw": _rate([t["gf"] == t["ga"] for t in tm]),
            "loss": _rate([t["gf"] < t["ga"] for t in tm]),
            "unbeaten": _rate([t["gf"] >= t["ga"] for t in tm]),
            "winless": _rate([t["gf"] <= t["ga"] for t in tm]),
            "scored": _rate([t["gf"] > 0 for t in tm]),
            "failed_to_score": _rate([t["gf"] == 0 for t in tm]),
            "clean_sheet": _rate([t["ga"] == 0 for t in tm]),
            "conceded": _rate([t["ga"] > 0 for t in tm]),
            "btts": _rate([t["gf"] > 0 and t["ga"] > 0 for t in tm]),
            "no_btts": _rate([not (t["gf"] > 0 and t["ga"] > 0) for t in tm]),
            "o15": _rate([t["gf"] + t["ga"] > 1 for t in tm]),
            "o25": _rate([t["gf"] + t["ga"] > 2 for t in tm]),
            "u25": _rate([t["gf"] + t["ga"] < 3 for t in tm]),
            "ht_goal": _rate([t["hf"] + t["ha"] > 0 for t in ht]),
            "ht_no_goal": _rate([t["hf"] + t["ha"] == 0 for t in ht]),
            "scored_1h": _rate([t["hf"] > 0 for t in ht]),
            "conceded_1h": _rate([t["ha"] > 0 for t in ht]),
        }

    return {
        "totals": [totals_row(k, rs) for k, rs in sorted(totals.items()) if len(rs) >= MIN_N],
        "home": [home_row(k, rs) for k, rs in sorted(homes.items()) if len(rs) >= MIN_N],
        "leagues": {lg: league_row(rs) for lg, rs in sorted(league_rows.items())
                    if len(rs) >= LEAGUE_MIN_MATCHES},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_dotenv(ROOT / "ingest" / ".env")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            rows = fetch(cur)
    finally:
        conn.close()

    table = build(rows)
    table["meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Pinnacle closing, proportionally de-vigged; results from matches",
        "matches": len(rows),
        "matches_totals": sum(r["n"] for r in table["totals"]),
        "matches_home": sum(r["n"] for r in table["home"]),
        "league_since": LEAGUE_SINCE,
        "min_n": MIN_N,
    }

    print(f"{len(rows):,} matches read")
    print(f"totals buckets: {len(table['totals'])}  ({table['meta']['matches_totals']:,} matches)")
    for r in table["totals"]:
        print(f"  O2.5 {r['lo']:.2f}-{r['hi']:.2f}  n={r['n']:>5}  o25={r['o25']}  btts={r['btts']}  ht_o05={r['ht_o05']}")
    print(f"home buckets: {len(table['home'])}  ({table['meta']['matches_home']:,} matches)")
    print(f"leagues: {len(table['leagues'])}")

    if args.dry_run:
        return 0
    OUT.write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
