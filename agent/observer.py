"""
observer.py — the observation layer.

Continuously records, for EVERY Polymarket market we can price, a snapshot of
(model fair value, PM price, edge, time-to-kickoff) into market_observations.
No threshold, no trades, no money. It reuses the exact sim pricing that
sim_scanner uses to bet — but keeps every market, not just the ones that cross
the edge threshold.

This is the dataset that tells us where/when edge actually lives, measures edge
decay toward kickoff, and (after settlement) whether PM converged to our model
(we were right) or away from it (model wrong).

Designed to run every ~30 min (cron */30). Each run appends one row per
market/outcome it can price.

Usage:
  cd agent && source ../ingest/.venv/bin/activate
  python observer.py                # live — writes observations
  python observer.py --dry-run      # compute + print, no DB writes
  python observer.py --days 3       # window
  python observer.py --report       # quick summary of what's been collected
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

# Reuse all the sim pricing + PM plumbing that the scanners already use to bet.
import sim_scanner as ss  # noqa: E402
import inplay_sim_scanner as iss  # noqa: E402  (live-state + in-play grouping)
import sharp_odds  # noqa: E402   (de-vigged Pinnacle at scan time)
import edge_engine  # noqa: E402  (refined sharp-anchored edge — shadow)
from dixon_coles import DixonColesModel  # noqa: E402
from sim.simulator import simulate, SimConfig  # noqa: E402
from sim.pricer import price_markets  # noqa: E402
from sim.state import MatchState  # noqa: E402

# Only look up the sharp line for matches kicking off within this many hours —
# bounds api-football calls and is where Pinnacle lines are meaningful.
SHARP_LOOKUP_HOURS = float(os.environ.get("OBS_SHARP_LOOKUP_HOURS", "12"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [observer] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("observer")

DATABASE_URL = os.getenv("DATABASE_URL")


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _kickoff(mkt: dict, event: dict) -> datetime | None:
    for src in (mkt.get("gameStartTime"), event.get("gameStartTime"),
                mkt.get("endDate"), event.get("endDate")):
        if not src:
            continue
        try:
            s = str(src).strip().replace("Z", "+00:00").replace(" ", "T", 1)
            import re
            s = re.sub(r"([+-]\d{2})$", r"\1:00", s)
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _attach_refined(rows: list[dict]) -> None:
    """
    Shadow: fill each row's refined-edge fields from edge_engine. Looks up the
    sharp line only for pre-match markets within SHARP_LOOKUP_HOURS of kickoff
    (bounds API calls); everything else is model-only. Mutates rows in place.
    """
    for r in rows:
        exec_price = r.get("pm_ask") if r.get("pm_ask") is not None else r["pm_yes"]
        sp = None
        mtk = r.get("minutes_to_kickoff")
        if r.get("phase") == "pre" and mtk is not None and 0 <= mtk <= SHARP_LOOKUP_HOURS * 60:
            date_str = r["kickoff_utc"].date().isoformat() if r.get("kickoff_utc") else None
            try:
                sp = sharp_odds.sharp_prob(r["home"], r["away"], date_str, r["outcome_key"])
            except Exception:
                sp = None
        res = edge_engine.compute_edge(
            model_prob=r["model_prob"], exec_price=exec_price, sharp_prob=sp,
        )
        r["sharp_prob"] = round(sp, 6) if sp is not None else None
        r["edge_refined_pp"] = res.edge_pp
        r["edge_confidence"] = res.confidence
        r["refined_bet_ok"] = res.bet_ok


def collect(days_ahead: int = ss.DEFAULT_DAYS_AHEAD) -> list[dict]:
    """Price every market and return observation rows (no DB writes)."""
    model = DixonColesModel.load(ss.PARAMS_PATH)
    norm_idx = {ss._norm(t): i for i, t in enumerate(model.teams)}

    events = ss._fetch_pm_events(days_ahead)
    by_match, n_non_football, n_past = ss._group_events_by_match(
        events, norm_idx, model.teams
    )
    log.info(
        f"{len(events)} events → {len(by_match)} match(es) in model "
        f"({n_non_football} non-football, {n_past} past skipped)"
    )

    now = datetime.now(timezone.utc)
    rows: list[dict] = []

    for (home, away), match_events in by_match.items():
        try:
            pred = model.predict(home, away)
            lh, la = pred["lambda_home"], pred["lambda_away"]
        except Exception:
            continue

        sim_res = simulate(lh, la, config=SimConfig(n_sims=ss.SIM_N, seed=ss.SIM_SEED))
        sim_p = price_markets(sim_res)

        for event in match_events:
            for mkt in event.get("markets", []):
                if not mkt.get("active") or mkt.get("closed"):
                    continue
                raw = mkt.get("outcomePrices") or mkt.get("outcome_prices")
                if not raw:
                    continue
                prices = json.loads(raw) if isinstance(raw, str) else raw
                try:
                    yes_p = float(prices[0])
                except (TypeError, ValueError, IndexError):
                    continue

                question = mkt.get("question", "")
                outcome_key = ss._classify_market(question, home, away)
                if not outcome_key or outcome_key not in sim_p:
                    continue

                model_prob = float(sim_p[outcome_key])
                ko = _kickoff(mkt, event)
                mins_to_ko = (ko - now).total_seconds() / 60.0 if ko else None

                rows.append({
                    "model": "sim",
                    "home": home, "away": away,
                    "pm_external_id": str(mkt.get("id") or mkt.get("conditionId") or ""),
                    "pm_token_id": ss._pm_token_id(mkt, "yes"),
                    "question": question,
                    "outcome_key": outcome_key,
                    "market_group": ss.MARKET_GROUP.get(outcome_key, "other"),
                    "model_prob": round(model_prob, 6),
                    "pm_yes": round(yes_p, 6),
                    "pm_bid": _f(mkt.get("bestBid")),
                    "pm_ask": _f(mkt.get("bestAsk")),
                    "edge_pp": round((model_prob - yes_p) * 100, 2),
                    "volume": _f(mkt.get("volume")),
                    "liquidity": _f(mkt.get("liquidity")),
                    "kickoff_utc": ko,
                    "minutes_to_kickoff": round(mins_to_ko, 1) if mins_to_ko is not None else None,
                    "phase": "pre",
                    "live_minute": None, "live_score": None,
                    "home_reds": None, "away_reds": None,
                })

    _attach_refined(rows)
    return rows


def collect_live() -> list[dict]:
    """
    In-play observation: price every live match from its current state
    (minute/score/reds) and record model-vs-PM-price for every market.
    Reuses inplay_sim_scanner's live-state + grouping plumbing.
    """
    model = DixonColesModel.load(ss.PARAMS_PATH)
    norm_idx = {ss._norm(t): i for i, t in enumerate(model.teams)}

    live_state = iss._fetch_live_state(norm_idx, model.teams)
    if not live_state:
        log.info("No live football right now.")
        return []

    events = iss._fetch_pm_events(iss.DEFAULT_HOURS_WINDOW // 24 + 1)
    by_match, n_nf, n_out = iss._group_pm_events_for_inplay(
        events, norm_idx, model.teams, iss.DEFAULT_HOURS_WINDOW
    )
    log.info(f"{len(live_state)} live in model → {len(by_match)} with PM markets in window")

    rows: list[dict] = []
    for (home, away), match_events in by_match.items():
        ls = live_state.get((home, away))
        if not ls or ls["minute"] >= 92:
            continue
        try:
            pred = model.predict(home, away)
            lh, la = pred["lambda_home"], pred["lambda_away"]
        except Exception:
            continue

        initial = MatchState.at(n_sims=ss.SIM_N, minute=ls["minute"],
                                home=ls["home_score"], away=ls["away_score"],
                                red_h=ls["home_reds"], red_a=ls["away_reds"])
        sim_res = simulate(lh, la, initial_state=initial,
                           config=SimConfig(n_sims=ss.SIM_N, seed=None))
        sim_p = price_markets(sim_res)
        score = f"{ls['home_score']}-{ls['away_score']}"

        for event in match_events:
            for mkt in event.get("markets", []):
                if not mkt.get("active") or mkt.get("closed"):
                    continue
                raw = mkt.get("outcomePrices") or mkt.get("outcome_prices")
                if not raw:
                    continue
                prices = json.loads(raw) if isinstance(raw, str) else raw
                try:
                    yes_p = float(prices[0])
                except (TypeError, ValueError, IndexError):
                    continue
                question = mkt.get("question", "")
                outcome_key = ss._classify_market(question, home, away)
                if not outcome_key or outcome_key not in sim_p:
                    continue
                # Halftime markets are stale once 1H is over (sim assumes
                # current score == HT score, only valid to ~minute 46).
                if ss.MARKET_GROUP.get(outcome_key) == "halftime" and ls["minute"] >= 47:
                    continue
                model_prob = float(sim_p[outcome_key])
                rows.append({
                    "model": "sim", "home": home, "away": away,
                    "pm_external_id": str(mkt.get("id") or mkt.get("conditionId") or ""),
                    "pm_token_id": ss._pm_token_id(mkt, "yes"),
                    "question": question, "outcome_key": outcome_key,
                    "market_group": ss.MARKET_GROUP.get(outcome_key, "other"),
                    "model_prob": round(model_prob, 6), "pm_yes": round(yes_p, 6),
                    "pm_bid": _f(mkt.get("bestBid")), "pm_ask": _f(mkt.get("bestAsk")),
                    "edge_pp": round((model_prob - yes_p) * 100, 2),
                    "volume": _f(mkt.get("volume")), "liquidity": _f(mkt.get("liquidity")),
                    "kickoff_utc": None, "minutes_to_kickoff": None,
                    "phase": "live", "live_minute": ls["minute"], "live_score": score,
                    "home_reds": ls["home_reds"], "away_reds": ls["away_reds"],
                })
    _attach_refined(rows)
    return rows


def _write(rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = ["model", "home", "away", "pm_external_id", "pm_token_id", "question",
            "outcome_key", "market_group", "model_prob", "pm_yes", "pm_bid",
            "pm_ask", "edge_pp", "volume", "liquidity", "kickoff_utc",
            "minutes_to_kickoff", "phase", "live_minute", "live_score",
            "home_reds", "away_reds", "sharp_prob", "edge_refined_pp",
            "edge_confidence", "refined_bet_ok"]
    conn = _conn()
    try:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO market_observations ({','.join(cols)}) VALUES %s",
            [[r.get(c) for c in cols] for r in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def run(mode: str = "pre", days_ahead: int = ss.DEFAULT_DAYS_AHEAD,
        dry_run: bool = False) -> dict:
    rows: list[dict] = []
    if mode in ("pre", "both"):
        rows += collect(days_ahead)
    if mode in ("live", "both"):
        rows += collect_live()

    if dry_run:
        for r in sorted(rows, key=lambda x: -abs(x["edge_pp"]))[:25]:
            if r["phase"] == "live":
                ctx = f"LIVE {r['live_score']} @{r['live_minute']}'"
            else:
                ctx = (f"KO {r['minutes_to_kickoff']:.0f}m"
                       if r["minutes_to_kickoff"] is not None else "KO ?")
            log.info(f"  {r['home'][:15]:15} vs {r['away'][:15]:15} | "
                     f"{r['outcome_key']:18} | model {r['model_prob']*100:5.1f}% "
                     f"PM {r['pm_yes']*100:5.1f}% edge {r['edge_pp']:+5.1f}pp | {ctx}")
        log.info(f"[dry-run] {len(rows)} observations (not written)")
        return {"observations": len(rows), "written": 0}
    n = _write(rows)
    log.info(f"Wrote {n} observations ({mode})")

    # PM-CLV: now that fresh snapshots are in, settle "did we beat Polymarket's own
    # close?" for any newly-settled trades. Best-effort — must never break the
    # observation write above. Only newly-settled trades are processed (pm_clv NULL),
    # so this is cheap after the initial backfill.
    try:
        import pm_clv_backfill
        res = pm_clv_backfill.backfill(
            limit=int(os.environ.get("OBS_PM_CLV_LIMIT", "60")), quiet=True
        )
        if res.get("updated"):
            log.info(f"PM-CLV: updated {res['updated']} settled trade(s) {res['by_source']}")
    except Exception as e:
        log.warning(f"PM-CLV backfill skipped: {e}")

    return {"observations": len(rows), "written": n}


def report() -> None:
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT COUNT(*) n, COUNT(DISTINCT pm_external_id) markets,
               COUNT(DISTINCT (home, away)) matches,
               MIN(observed_at) first_obs, MAX(observed_at) last_obs,
               ROUND(AVG(edge_pp),2) avg_edge
        FROM market_observations
    """)
    s = dict(cur.fetchone())
    print(f"\nobservations : {s['n']}")
    print(f"markets      : {s['markets']}   matches: {s['matches']}")
    print(f"window       : {s['first_obs']} → {s['last_obs']}")
    print(f"avg edge_pp  : {s['avg_edge']}")
    cur.execute("""
        SELECT phase, market_group, COUNT(*) n, ROUND(AVG(edge_pp),2) avg_edge,
               ROUND(AVG(ABS(edge_pp)),2) avg_abs_edge
        FROM market_observations GROUP BY phase, market_group
        ORDER BY phase, n DESC
    """)
    print("\nby phase / market group:")
    for r in cur.fetchall():
        print(f"  [{r['phase']:4}] {r['market_group'] or '?':10} n={r['n']:>5}  "
              f"avg {r['avg_edge']:+6}pp  |avg| {r['avg_abs_edge']:>5}pp")
    # The thesis check: is |edge| bigger in-play, and does it grow late?
    cur.execute("""
        SELECT CASE WHEN live_minute < 45 THEN '00-45'
                    WHEN live_minute < 70 THEN '45-70'
                    ELSE '70+' END bucket,
               COUNT(*) n, ROUND(AVG(ABS(edge_pp)),2) avg_abs_edge
        FROM market_observations WHERE phase='live' AND live_minute IS NOT NULL
        GROUP BY bucket ORDER BY bucket
    """)
    live_rows = cur.fetchall()
    if live_rows:
        print("\nin-play |edge| by minute bucket (the post-70' thesis):")
        for r in live_rows:
            print(f"  {r['bucket']:6} n={r['n']:>5}  |avg| {r['avg_abs_edge']:>5}pp")

    # Shadow: naive (edge_pp vs mid >= 3) vs refined engine decision.
    cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE edge_pp >= 3)                                    AS naive_bet,
          COUNT(*) FILTER (WHERE refined_bet_ok)                                  AS refined_bet,
          COUNT(*) FILTER (WHERE refined_bet_ok AND edge_confidence='sharp')      AS refined_sharp,
          COUNT(*) FILTER (WHERE refined_bet_ok AND edge_confidence='model')      AS refined_model,
          COUNT(*) FILTER (WHERE edge_pp >= 3 AND NOT COALESCE(refined_bet_ok,false)) AS killed,
          COUNT(*) FILTER (WHERE sharp_prob IS NOT NULL)                          AS had_sharp
        FROM market_observations
    """)
    s2 = dict(cur.fetchone())
    print("\nshadow — naive vs refined (no money):")
    print(f"  naive would-bet (≥3pp vs mid): {s2['naive_bet']}")
    print(f"  refined would-bet            : {s2['refined_bet']}  "
          f"(sharp {s2['refined_sharp']}, model {s2['refined_model']})")
    print(f"  naive-bet KILLED by refined  : {s2['killed']}")
    print(f"  observations with a sharp line: {s2['had_sharp']}")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="Observation layer")
    ap.add_argument("--mode", choices=["pre", "live", "both"], default="pre")
    ap.add_argument("--live", action="store_true", help="shorthand for --mode live")
    ap.add_argument("--days", type=int, default=ss.DEFAULT_DAYS_AHEAD)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        report()
    else:
        mode = "live" if args.live else args.mode
        run(mode=mode, days_ahead=args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
