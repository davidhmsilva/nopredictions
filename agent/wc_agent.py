#!/usr/bin/env python3
"""
World Cup 2026 paper sub-agent — one bet per game on every fixture.

Pipeline (per run)
  1. Fetch live PM football events for the window.
  2. Keep only WC 2026 events (Soccer tag + WC-shaped title/series).
  3. For each event, resolve the two nations against the national-team Elo
     ratings, build λ_h / λ_a via wc_pricer, run the MC sim once, apply
     structural priors (wc_structural), and walk the event's 1X2 + O/U
     markets.
  4. Per match, score every candidate market with
        score = edge_pp  - variance_penalty * (1 - yes_p)
                          + favorite_tilt   * yes_p
                          - public_fade_pp(YES side)
     Variance cap drops yes_p outside [VAR_FLOOR, VAR_CEIL]. Pick the single
     best-score market and write ONE paper_trade per match (strategy_id=10,
     stake=1u flat). All candidates are logged to market_observations for
     the adaptive layer + post-mortem content.
  5. Update wc_agent_state.n_settled / pl_units from the resolver.

CLI:
  cd agent && source ../ingest/.venv/bin/activate
  python wc_agent.py                     # scan next 2 days, write to DB
  python wc_agent.py --days 1            # narrower window
  python wc_agent.py --dry-run           # no DB writes, print picks
  python wc_agent.py --date 2026-06-11   # focus on a kickoff date (YYYY-MM-DD)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

sys.path.insert(0, os.path.dirname(__file__))
import national_elo  # noqa: E402
import wc_pricer  # noqa: E402
import wc_structural as wcs  # noqa: E402
from sim.simulator import simulate, SimConfig  # noqa: E402
from sim.pricer import price_markets  # noqa: E402
from dc_scanner import (  # noqa: E402
    _fetch_pm_events, _is_football_event, _norm, _pm_token_id,
    NON_FOOTBALL_NAME_TOKENS,
)
from sim_scanner import _extract_teams, _classify_market, _upsert_pm_market  # noqa: E402
import resolver as _resolver  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
from db import find_match_id  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [wc_agent] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("wc_agent")

DATABASE_URL = os.getenv("DATABASE_URL")
STRATEGY_ID = 10
STAKE_UNITS = 1.0
SIM_N = 50_000
SIM_SEED = 7

# Markets the WC agent is allowed to bet (1X2 + O/U only, per user spec).
ALLOWED_OUTCOMES = {
    "home_win", "draw", "away_win",
    "over_1_5", "over_2_5", "over_3_5",
    "under_1_5", "under_2_5", "under_3_5",
}

# Selector scoring knobs.
VAR_FLOOR = 0.08          # drop YES below 8% — pure longshot
VAR_CEIL = 0.92           # drop YES above 92% — short price, low EV variance
VARIANCE_PENALTY = 4.0    # pp penalty multiplier on (1 - yes_p)
FAVORITE_TILT = 3.0       # pp bonus multiplier on yes_p
MIN_EDGE_PP = 0.0         # must bet every game; no floor

# WC2026 host nations (carry home adv in wc_pricer if they're playing).
HOST_NATIONS = {"United States", "Mexico", "Canada"}

# PM uses FIFA-style names; our national_elo ratings inherit Stage G's
# canonicalisation (which mostly follows the international-results dataset).
# Where the two diverge, map here.
PM_TO_RATING_ALIAS = {
    "Korea Republic": "South Korea",
    "South Korea": "South Korea",
    "Korea DPR": "North Korea",
    "Czechia": "Czech Republic",
    "USA": "United States",
    "Ivory Coast": "Cote d'Ivoire",
    "Côte d'Ivoire": "Cote d'Ivoire",
    "Cape Verde": "Cape Verde Islands",
    "Curacao": "Curaçao",
    "Türkiye": "Turkey",
    "Republic of Ireland": "Ireland",
    "Wales": "Wales",
    "England": "England",
}


def _resolve_with_alias(name: str, nat: dict) -> Optional[str]:
    direct = PM_TO_RATING_ALIAS.get(name)
    if direct and direct in nat["ratings"]:
        return direct
    return wc_pricer.resolve_team(name, nat)

# Event-level WC filter — match by tag, series slug or title shape. PM tags WC
# events with the FIFA World Cup label and series slug usually contains "world-
# cup". Title alone is unreliable.
_WC_TITLE_RE = re.compile(r"world\s*cup", re.I)

# PM siblings that look like 1X2 but aren't (the team name in the question lets
# the generic classifier fall through to home_win / away_win — wrong). Skip
# their event titles wholesale, and any individual question that matches.
_SKIP_EVENT_SUFFIX = re.compile(
    r"-\s*(exact\s+score|first\s+team\s+to\s+score|total\s+corners|corners|"
    r"cards|player\s+props|method\s+of|to\s+score|qualifies|advance|win\s+group)",
    re.I,
)
_SKIP_QUESTION_RE = re.compile(
    r"exact\s+score|to\s+score\s+first|first\s+to\s+score|neither\s+team\s+to\s+score"
    r"|total\s+corners|corners|cards|method\s+of|qualif|advance|win\s+group"
    r"|relocat",
    re.I,
)

# Team-prefix O/U lines (e.g. "Mexico vs. South Africa: South Africa O/U 1.5")
# are per-team goal totals, NOT match totals. The shared classifier turns them
# into over_1_5 because it sees "O/U 1.5"; we filter them out here so we only
# bet *match* totals.
_TEAM_OU_RE = re.compile(
    r":\s*[A-Z][A-Za-z .'\-]+\s+(O/U|OU|Over|Under)\s+\d", re.I
)


def _is_wc_event(event: dict) -> bool:
    if not _is_football_event(event):
        return False
    tags = event.get("tags") or []
    tag_labels = {(t.get("label") or "").lower() for t in tags}
    tag_slugs = {(t.get("slug") or "").lower() for t in tags}
    series_slug = (event.get("seriesSlug") or "").lower()
    title = event.get("title", "")
    if any("world cup" in lbl for lbl in tag_labels):
        return True
    if any("world-cup" in s or "world_cup" in s for s in tag_slugs):
        return True
    if "world-cup" in series_slug or "worldcup" in series_slug:
        return True
    if _WC_TITLE_RE.search(title):
        return True
    return False


def _ko_from_event(event: dict) -> bool:
    """Heuristic group-vs-knockout flag from event metadata."""
    title = (event.get("title", "") + " " + event.get("slug", "") + " "
             + event.get("description", "")).lower()
    for token in ("round of 16", "round-of-16", "quarterfinal", "quarter-final",
                  "semifinal", "semi-final", "final", "knockout", "ko"):
        if token in title:
            return True
    return False


# ── DB helpers ───────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DATABASE_URL)


def _ensure_strategy(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE id = %s", (STRATEGY_ID,))
    if cur.fetchone():
        return STRATEGY_ID
    raise RuntimeError(
        f"Strategy id={STRATEGY_ID} not found — apply db/022_wc_agent.sql first."
    )


def _already_traded_match(conn, match_id: Optional[int],
                          home: str, away: str) -> bool:
    """One bet per (match, strategy) — the WC agent bets every fixture EXACTLY
    once. If a row already exists (open or settled), skip."""
    cur = conn.cursor()
    if match_id:
        cur.execute(
            "SELECT 1 FROM paper_trades WHERE strategy_id = %s AND match_id = %s LIMIT 1",
            (STRATEGY_ID, match_id),
        )
        if cur.fetchone():
            return True
    cur.execute(
        "SELECT 1 FROM paper_trades WHERE strategy_id = %s AND reasoning LIKE %s LIMIT 1",
        (STRATEGY_ID, f"%WC: {home} vs {away}%"),
    )
    return cur.fetchone() is not None


def _write_trade(conn, market_db_id: int, outcome: str, entry_price: float,
                 model_prob: float, edge_pp: float, reasoning: str,
                 match_id: Optional[int], home: str, away: str,
                 score: float, lambdas: Tuple[float, float]) -> Optional[int]:
    entry_odds = round(1 / entry_price, 4) if entry_price > 0 else None
    confidence = round(max(min(score / 10.0, 1.0), 0.0), 3)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO paper_trades (
            strategy_id, market_id, match_id, outcome,
            entry_price, entry_odds,
            model_probability, sharp_consensus_price, sharp_consensus_sources,
            expected_edge, confidence, stake_units, reasoning, placed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, NOW())
        RETURNING id
        """,
        (
            STRATEGY_ID, market_db_id, match_id, outcome,
            entry_price, entry_odds,
            model_prob, model_prob,
            json.dumps({
                "source": "WC agent (national Elo + MC sim + structural priors)",
                "lambda_home": round(lambdas[0], 4),
                "lambda_away": round(lambdas[1], 4),
                "score": round(score, 3),
                "no_sharp": True,
            }),
            round(edge_pp / 100, 6),
            confidence,
            STAKE_UNITS,
            reasoning,
        ),
    )
    trade_id = cur.fetchone()[0]
    conn.commit()
    return trade_id


def _log_observation(conn, *, home: str, away: str, ext_id: str,
                     token_id: Optional[str], question: str, outcome_key: str,
                     model_prob: float, pm_yes: float, pm_bid: Optional[float],
                     pm_ask: Optional[float], edge_pp: float,
                     kickoff_utc: Optional[datetime]) -> None:
    """Best-effort observation row — never blocks a trade write."""
    try:
        mtk = None
        if kickoff_utc is not None:
            mtk = (kickoff_utc - datetime.now(timezone.utc)).total_seconds() / 60.0
        market_group = (
            "1x2" if outcome_key in ("home_win", "draw", "away_win") else "totals"
        )
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO market_observations
                (model, home, away, pm_external_id, pm_token_id, question,
                 outcome_key, market_group, model_prob, pm_yes, pm_bid, pm_ask,
                 edge_pp, kickoff_utc, minutes_to_kickoff)
            VALUES ('wc_agent', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s)
            """,
            (home, away, ext_id, token_id, question, outcome_key, market_group,
             round(model_prob, 6), round(pm_yes, 6), pm_bid, pm_ask,
             round(edge_pp, 4), kickoff_utc, mtk),
        )
        conn.commit()
    except Exception as exc:
        log.debug(f"observation insert skipped: {exc}")


def _log_run(conn, n_events: int, n_matches: int, n_picks: int, dry: bool):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_runs
            (run_type, started_at, finished_at, input_context, output_summary, status)
        VALUES ('wc_agent:daily', NOW(), NOW(), %s::jsonb, %s, 'completed')
        """,
        (
            json.dumps({"events": n_events, "matches": n_matches,
                        "picks": n_picks, "dry_run": dry}),
            f"{'[DRY] ' if dry else ''}Scanned {n_events} PM events, "
            f"{n_matches} WC matches, wrote {n_picks} picks.",
        ),
    )
    conn.commit()


def _update_state(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE result IN ('won','lost')),
               COALESCE(SUM(COALESCE(payout_units,0) - stake_units)
                        FILTER (WHERE result IN ('won','lost')), 0)
        FROM paper_trades WHERE strategy_id = %s
        """,
        (STRATEGY_ID,),
    )
    n_settled, pl = cur.fetchone()
    cur.execute(
        """
        UPDATE wc_agent_state
        SET n_settled = %s, pl_units = %s, updated_at = NOW()
        WHERE id = 1
        """,
        (int(n_settled or 0), float(pl or 0)),
    )
    conn.commit()


# ── selector ─────────────────────────────────────────────────────────────────

def _score(edge_pp: float, yes_p: float, fade_pp: float) -> float:
    return (edge_pp
            - VARIANCE_PENALTY * (1 - yes_p)
            + FAVORITE_TILT * yes_p
            - fade_pp * 100)


def _team_for_outcome(outcome: str, home: str, away: str) -> Optional[str]:
    if outcome == "home_win":
        return home
    if outcome == "away_win":
        return away
    return None


def _kickoff_utc_from_event(event: dict) -> Optional[datetime]:
    for key in ("startDate", "startTime", "endDate"):
        v = event.get(key)
        if not v:
            continue
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
    return None


def _resolve_host(home: str, away: str) -> Optional[str]:
    """If exactly one host nation is playing, treat it as carrying home adv —
    we don't know the venue, so this is a soft guess."""
    if home in HOST_NATIONS and away not in HOST_NATIONS:
        return home
    if away in HOST_NATIONS and home not in HOST_NATIONS:
        return away
    return None


def run(*, days_ahead: int = 2, focus_date: Optional[date] = None,
        dry_run: bool = False) -> dict:
    log.info("Loading national Elo ratings…")
    if not national_elo.RATINGS_PATH.exists():
        raise SystemExit(
            "national_elo_ratings.json missing — run: python national_elo.py --build")
    nat = national_elo.load_ratings()
    calib = wcs.load_calibration()
    if calib is None:
        log.warning("No wc_structural calibration on disk — priors disabled. "
                    "Run: python wc_structural.py --calibrate")
        calib = {}

    log.info(f"Fetching PM events (next {days_ahead}d)…")
    events = _fetch_pm_events(days_ahead)
    wc_events = [e for e in events if _is_wc_event(e)]
    log.info(f"  {len(events)} events total, {len(wc_events)} WC-shaped")

    # Group by (home, away) — collapse PM sibling events.
    by_match: Dict[Tuple[str, str], List[dict]] = {}
    skipped_unresolved: List[Tuple[str, str]] = []
    for event in wc_events:
        title = event.get("title", "")
        if _SKIP_EVENT_SUFFIX.search(title):
            continue
        teams = _extract_teams(title)
        if teams is None:
            continue
        home_raw, away_raw = teams
        raw_lower = f"{home_raw} {away_raw}".lower()
        if any(tok in raw_lower.split() for tok in NON_FOOTBALL_NAME_TOKENS):
            continue
        home = _resolve_with_alias(home_raw, nat)
        away = _resolve_with_alias(away_raw, nat)
        if home is None or away is None:
            skipped_unresolved.append((home_raw, away_raw))
            continue
        by_match.setdefault((home, away), []).append(event)

    if skipped_unresolved:
        log.info(f"  Skipped {len(skipped_unresolved)} matches w/ unrated team "
                 f"(e.g. {skipped_unresolved[:3]})")

    if focus_date is not None:
        kept = {}
        for k, evs in by_match.items():
            for e in evs:
                ko = _kickoff_utc_from_event(e)
                if ko and ko.date() == focus_date:
                    kept[k] = evs
                    break
        by_match = kept
        log.info(f"  Focused on {focus_date}: {len(by_match)} match(es)")

    conn = None if dry_run else _conn()
    n_picks = 0

    try:
        if not dry_run:
            _ensure_strategy(conn)

        for (home, away), match_events in by_match.items():
            if not dry_run and _already_traded_match(conn, None, home, away):
                log.info(f"  [{home} vs {away}] already bet — skip")
                continue

            # WC venues are neutral; the only "home" effect is when a host
            # nation plays at home. Always pass neutral=True and let the host
            # parameter add the bonus exactly once.
            host = _resolve_host(home, away)
            lh, la = wc_pricer.lambdas(home, away, nat, neutral=True, host=host)
            sim_res = simulate(lh, la, config=SimConfig(n_sims=SIM_N, seed=SIM_SEED))
            fair = price_markets(sim_res)

            # Structural priors
            is_ko = any(_ko_from_event(e) for e in match_events)
            ctx = wcs.StructuralContext(
                home=home, away=away,
                rating_home=nat["ratings"][home]["rating"],
                rating_away=nat["ratings"][away]["rating"],
                stage="knockout" if is_ko else "group",
                is_knockout=is_ko,
            )
            shifts = wcs.adjustments(fair, ctx, calib=calib)
            blended = wcs.apply_shifts(fair, shifts)

            ko_utc = next((_kickoff_utc_from_event(e) for e in match_events
                           if _kickoff_utc_from_event(e)), None)
            ko_date = ko_utc.date() if ko_utc else None
            db_match_id = (
                find_match_id(conn, home, away, ko_date) if not dry_run else None
            )

            # Walk every market on every sibling event.
            candidates: List[dict] = []
            for event in match_events:
                for mkt in event.get("markets", []):
                    if not mkt.get("active") or mkt.get("closed"):
                        continue
                    bid = mkt.get("bestBid")
                    ask = mkt.get("bestAsk")
                    if bid is None or ask is None:
                        continue
                    question = mkt.get("question", "")
                    if _SKIP_QUESTION_RE.search(question):
                        continue
                    if _TEAM_OU_RE.search(question):
                        continue
                    raw = mkt.get("outcomePrices") or mkt.get("outcome_prices")
                    if not raw:
                        continue
                    try:
                        prices = json.loads(raw) if isinstance(raw, str) else raw
                        yes_p = float(prices[0])
                    except (ValueError, IndexError, TypeError):
                        continue

                    outcome_key = _classify_market(question, home, away)
                    if outcome_key is None or outcome_key not in ALLOWED_OUTCOMES:
                        continue
                    if outcome_key not in blended:
                        continue

                    model_prob = float(blended[outcome_key])
                    raw_edge_pp = (model_prob - yes_p) * 100

                    side_team = _team_for_outcome(outcome_key, home, away)
                    fade = wcs.public_fade_pp(outcome_key, side_team,
                                              model_prob, yes_p)
                    eff_edge_pp = raw_edge_pp - fade * 100

                    if not dry_run:
                        _log_observation(
                            conn, home=home, away=away,
                            ext_id=str(mkt.get("id") or mkt.get("conditionId") or ""),
                            token_id=_pm_token_id(mkt, "yes"),
                            question=question, outcome_key=outcome_key,
                            model_prob=model_prob, pm_yes=yes_p,
                            pm_bid=float(bid), pm_ask=float(ask),
                            edge_pp=eff_edge_pp, kickoff_utc=ko_utc,
                        )

                    if yes_p < VAR_FLOOR or yes_p > VAR_CEIL:
                        continue
                    if eff_edge_pp < MIN_EDGE_PP:
                        continue

                    score = _score(eff_edge_pp, yes_p, fade)
                    candidates.append({
                        "event": event, "mkt": mkt,
                        "outcome_key": outcome_key,
                        "yes_p": yes_p, "model_prob": model_prob,
                        "edge_pp": eff_edge_pp, "raw_edge_pp": raw_edge_pp,
                        "fade_pp": fade * 100, "score": score, "question": question,
                    })

            if not candidates:
                log.info(f"  [{home} vs {away}] no eligible market — passing this game")
                continue

            best = max(candidates, key=lambda c: c["score"])
            reasoning = (
                f"WC: {home} vs {away} — {best['question']}. "
                f"PM YES: {best['yes_p']*100:.1f}% ({1/best['yes_p']:.2f}). "
                f"Model fair (Elo+sim+priors): {best['model_prob']*100:.1f}%. "
                f"Edge: {best['raw_edge_pp']:+.1f}pp (fade −{best['fade_pp']:.1f}pp). "
                f"λ_h={lh:.2f} λ_a={la:.2f}. "
                f"Stage: {'KO' if is_ko else 'group'}. "
                f"Score: {best['score']:.2f} (best of {len(candidates)})."
            )
            log.info(
                f"  PICK {home} vs {away}: {best['outcome_key']} @ "
                f"{best['yes_p']*100:.1f}% | model {best['model_prob']*100:.1f}% "
                f"| edge {best['edge_pp']:+.1f}pp | score {best['score']:.2f}"
            )

            if dry_run:
                n_picks += 1
                continue

            ext_id = str(best["mkt"].get("id") or best["mkt"].get("conditionId") or "")
            mkt_db = _upsert_pm_market(conn, ext_id,
                                       best["mkt"].get("question", ""),
                                       best["event"].get("endDate"))
            if not mkt_db:
                log.warning(f"    could not upsert PM market for {ext_id}")
                continue
            tid = _write_trade(
                conn, mkt_db, best["outcome_key"], best["yes_p"],
                best["model_prob"], best["edge_pp"], reasoning,
                match_id=db_match_id, home=home, away=away,
                score=best["score"], lambdas=(lh, la),
            )
            if tid:
                n_picks += 1
                log.info(f"    → paper_trade #{tid} logged")

        if not dry_run:
            _log_run(conn, len(events), len(by_match), n_picks, dry_run)
            try:
                resolved = _resolver.run()
                if resolved:
                    log.info(f"[resolver] settled {len(resolved)} trade(s)")
            except Exception as exc:
                log.warning(f"[resolver] failed: {exc}")
            _update_state(conn)

        return {
            "events_scanned": len(events),
            "wc_events": len(wc_events),
            "matches": len(by_match),
            "picks": n_picks,
            "dry_run": dry_run,
        }
    finally:
        if conn:
            conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="World Cup paper sub-agent")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD — only score matches with this kickoff date")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    focus = None
    if args.date:
        focus = datetime.strptime(args.date, "%Y-%m-%d").date()

    result = run(days_ahead=args.days, focus_date=focus, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
