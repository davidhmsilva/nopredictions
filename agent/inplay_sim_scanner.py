"""
Sim Model In-Play Scanner — re-prices PM markets every poll cycle using
the Monte Carlo simulator started from the current match state (score,
minute, red cards).

Replaces the patchwork in poisson_trader.py v2: same engine as
sim_scanner.py but with `initial_state=MatchState.at(...)`. Pre-match and
in-play prices come from a single coherent model.

Usage:
    cd agent && source ../ingest/.venv/bin/activate
    python inplay_sim_scanner.py            # one cycle, writes to DB
    python inplay_sim_scanner.py --dry-run  # no DB writes
    python inplay_sim_scanner.py --threshold 6.0
    python inplay_sim_scanner.py --hours 6  # widen PM event window

Designed to be called every 5-10 min by inplay_daemon.sh.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

sys.path.insert(0, os.path.dirname(__file__))
from dixon_coles import DixonColesModel  # noqa: E402
import resolver as _resolver  # noqa: E402

from dc_scanner import (  # noqa: E402
    _norm,
    _find_team,
    _is_football_event,
    _fetch_pm_events,
    NON_FOOTBALL_NAME_TOKENS,
)
from sim_scanner import (  # noqa: E402
    _classify_market,
    _extract_teams,
    _upsert_pm_market,
    MARKET_GROUP,
    _group_outcomes,
    _SKIP_EVENT_TITLE,
)

from sim.simulator import simulate, SimConfig  # noqa: E402
from sim.state import MatchState  # noqa: E402
from sim.pricer import price_markets  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
from db import find_match_id  # noqa: E402


DATABASE_URL = os.getenv("DATABASE_URL")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
PARAMS_PATH = os.path.join(os.path.dirname(__file__), "dc_model_params.json")
STRATEGY_NAME = "Sim Model In-Play"
STAKE_UNITS = 1.0
DEFAULT_EDGE_THRESHOLD_PP = 5.0   # higher than pre-match — prices move fast
SIM_N = 30_000                     # fewer sims for tighter latency
DEDUPE_WINDOW_MIN = 15             # allow re-entry after state changes
DEFAULT_HOURS_WINDOW = 4           # PM events within ±4h of now
_PAST_GRACE = timedelta(hours=3)   # keep matches whose nominal endDate just passed
                                   # (still live: stoppage/HT delays) — live_state gates the rest

# force=True overrides anything dc_scanner/sim_scanner's basicConfig already set.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [inplay_sim] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("inplay_sim")


# ── Live state from api-football ──────────────────────────────────────────────


def _fetch_live_state(
    norm_idx: dict[str, int],
    team_names: list[str],
) -> dict[tuple[str, str], dict]:
    """
    Fetch all currently-live football matches from api-football, resolving
    api-football team names against the DC model's alias table so keys are
    CANONICAL model names. Matches not in the DC model are dropped.
    """
    if not FOOTBALL_API_KEY:
        log.warning("FOOTBALL_API_KEY missing — no live state available")
        return {}

    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"live": "all"},
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning(f"api-football HTTP {resp.status_code}")
            return {}
        data = resp.json()
    except Exception as exc:
        log.warning(f"api-football error: {exc}")
        return {}

    out: dict[tuple[str, str], dict] = {}
    n_total = 0
    n_in_model = 0
    for fix in data.get("response", []):
        teams = fix.get("teams", {})
        home = teams.get("home", {}).get("name", "")
        away = teams.get("away", {}).get("name", "")
        goals = fix.get("goals", {})
        status = fix.get("fixture", {}).get("status", {})

        if not home or not away:
            continue
        hg, ag = goals.get("home"), goals.get("away")
        elapsed = status.get("elapsed")
        if hg is None or ag is None or elapsed is None:
            continue
        n_total += 1

        # Resolve to canonical model names — drops matches not in the model
        h_idx = _find_team(home, norm_idx)
        a_idx = _find_team(away, norm_idx)
        if h_idx is None or a_idx is None:
            continue
        n_in_model += 1

        reds_h = reds_a = 0
        for ev in fix.get("events", []):
            if ev.get("type") == "Card" and ev.get("detail") == "Red Card":
                team_name = ev.get("team", {}).get("name", "")
                if team_name == home:
                    reds_h += 1
                elif team_name == away:
                    reds_a += 1

        out[(team_names[h_idx], team_names[a_idx])] = {
            "minute": int(elapsed),
            "home_score": int(hg),
            "away_score": int(ag),
            "home_reds": reds_h,
            "away_reds": reds_a,
            "home_raw": home,
            "away_raw": away,
        }

    log.info(
        f"  Live state: {n_total} live match(es), {n_in_model} in DC model"
    )
    return out


# ── DB helpers ────────────────────────────────────────────────────────────────


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _get_or_create_strategy(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE name = %s LIMIT 1", (STRATEGY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO research_hypotheses
            (title, description, rationale, source, status, created_by)
        VALUES (
            'Monte Carlo Sim In-Play Edge Scanner',
            'Polymarket in-play prices diverge from MC simulator started at current match state.',
            'In-play markets are less efficient than pre-match — retail bias and latency '
            'compound. Same MC engine as the pre-match scanner, but initial_state '
            'reflects current score, minute, and red cards. State dynamics (trailing-team '
            'push, leader sit-back, late-game amplification) naturally generate edges as '
            'the match unfolds.',
            'agent', 'live', 'agent'
        ) RETURNING id
        """
    )
    hyp_id = cur.fetchone()[0]
    conn.commit()

    cur.execute(
        """
        INSERT INTO strategies (hypothesis_id, name, rules, promoted_at)
        VALUES (%s, %s, %s::jsonb, NOW())
        RETURNING id
        """,
        (
            hyp_id,
            STRATEGY_NAME,
            json.dumps(
                {
                    "model": "MC simulator from in-play state",
                    "edge_threshold_pp": DEFAULT_EDGE_THRESHOLD_PP,
                    "n_sims": SIM_N,
                    "dedupe_window_min": DEDUPE_WINDOW_MIN,
                    "stake": "1u flat",
                    "phase": "paper-only",
                    "markets": "1X2, halftime (pre-HT), totals 0.5-4.5, BTTS, handicaps",
                    "live_source": "api-football",
                }
            ),
        ),
    )
    strat_id = cur.fetchone()[0]
    conn.commit()
    log.info(f"Created strategy '{STRATEGY_NAME}' (id={strat_id})")
    return strat_id


def _already_traded_recent(
    conn,
    strategy_id: int,
    market_group: str,
    match_id: Optional[int],
    home: str,
    away: str,
    window_min: int = DEDUPE_WINDOW_MIN,
) -> bool:
    """Skip if we logged a trade for (match, market_group) in the last `window_min`."""
    group_outcomes = _group_outcomes(market_group)
    if not group_outcomes:
        return False
    cur = conn.cursor()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)

    if match_id:
        cur.execute(
            """
            SELECT id FROM paper_trades
            WHERE strategy_id = %s AND match_id = %s AND outcome = ANY(%s)
              AND placed_at > %s
            LIMIT 1
            """,
            (strategy_id, match_id, group_outcomes, cutoff),
        )
        if cur.fetchone():
            return True

    pattern = f"%{home} vs {away}%"
    cur.execute(
        """
        SELECT id FROM paper_trades
        WHERE strategy_id = %s AND reasoning LIKE %s AND outcome = ANY(%s)
          AND placed_at > %s
        LIMIT 1
        """,
        (strategy_id, pattern, group_outcomes, cutoff),
    )
    return cur.fetchone() is not None


def _write_trade(
    conn,
    strategy_id: int,
    market_db_id: int,
    outcome: str,
    entry_price: float,
    sim_prob: float,
    edge_pp: float,
    sim_se: float,
    reasoning: str,
    match_id: Optional[int],
    home: str,
    away: str,
    score: tuple[int, int],
    minute: int,
    reds: tuple[int, int],
) -> Optional[int]:
    group = MARKET_GROUP.get(outcome, "other")
    if _already_traded_recent(conn, strategy_id, group, match_id, home, away):
        return None

    entry_odds = round(1 / entry_price, 4) if entry_price > 0 else None
    confidence = min(edge_pp / 15.0, 1.0)
    if sim_se > 0:
        edge_to_se = (edge_pp / 100.0) / sim_se
        if edge_to_se < 2.0:
            confidence *= edge_to_se / 2.0
    confidence = round(confidence, 3)

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
            strategy_id,
            market_db_id,
            match_id,
            outcome,
            entry_price,
            entry_odds,
            sim_prob,
            sim_prob,
            json.dumps(
                {
                    "source": "MC Sim In-Play",
                    "market_group": group,
                    "sim_prob": round(sim_prob, 4),
                    "sim_se_pp": round(sim_se * 100, 3),
                    "n_sims": SIM_N,
                    "score": f"{score[0]}-{score[1]}",
                    "minute": minute,
                    "red_cards": f"{reds[0]}-{reds[1]}",
                }
            ),
            round(edge_pp / 100, 6),
            confidence,
            STAKE_UNITS,
            reasoning,
        ),
    )
    trade_id = cur.fetchone()[0]
    conn.commit()
    return trade_id


def _log_run(conn, live_count: int, edges: int, trades: int, dry_run: bool):
    summary = (
        f"{'[DRY RUN] ' if dry_run else ''}"
        f"Live: {live_count} match(es), {edges} edges, {trades} trades."
    )
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_runs
            (run_type, started_at, finished_at, input_context, output_summary, status)
        VALUES ('inplay_sim_scanner:cycle', NOW(), NOW(), %s::jsonb, %s, 'completed')
        """,
        (
            json.dumps(
                {
                    "live_matches": live_count,
                    "edges": edges,
                    "trades": trades,
                    "dry_run": dry_run,
                }
            ),
            summary,
        ),
    )
    conn.commit()


# ── Main ──────────────────────────────────────────────────────────────────────


def _group_pm_events_for_inplay(
    events: list[dict],
    norm_idx: dict[str, int],
    team_names: list[str],
    hours_window: int,
) -> tuple[dict[tuple[str, str], list[dict]], int, int]:
    """
    Same as sim_scanner._group_events_by_match, but with a tighter time window:
    only events whose endDate (resolution time) is within `hours_window` from now.

    NOTE: PM `endDate` is the *scheduled* resolution time (~kickoff + 2h). A match
    that is still being played (stoppage time, HT delay) has often already passed
    its nominal endDate while its market stays open — so we must NOT drop recently
    past endDates, or we'd exclude exactly the live matches we want to observe.
    A `_PAST_GRACE` window keeps those; the live_state intersection downstream
    discards anything not actually in progress.
    """
    by_match: dict[tuple[str, str], list[dict]] = {}
    n_non_football = 0
    n_outside_window = 0

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=hours_window)
    window_start = now - _PAST_GRACE

    for event in events:
        title = event.get("title", "")
        if not _is_football_event(event):
            n_non_football += 1
            continue
        if _SKIP_EVENT_TITLE.search(title):
            continue

        end_date = event.get("endDate", "")
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                if end_dt < window_start or end_dt > window_end:
                    n_outside_window += 1
                    continue
            except (ValueError, TypeError):
                pass

        teams = _extract_teams(title)
        if teams is None:
            continue
        home_raw, away_raw = teams

        raw_lower = f"{home_raw} {away_raw}".lower()
        if any(tok in raw_lower.split() for tok in NON_FOOTBALL_NAME_TOKENS):
            n_non_football += 1
            continue

        h_idx = _find_team(home_raw, norm_idx)
        a_idx = _find_team(away_raw, norm_idx)
        if h_idx is None or a_idx is None:
            continue

        key = (team_names[h_idx], team_names[a_idx])
        by_match.setdefault(key, []).append(event)

    return by_match, n_non_football, n_outside_window


def run(
    threshold_pp: float = DEFAULT_EDGE_THRESHOLD_PP,
    dry_run: bool = False,
    hours_window: int = DEFAULT_HOURS_WINDOW,
) -> dict:
    log.info(f"Loading DC model from {PARAMS_PATH}...")
    model = DixonColesModel.load(PARAMS_PATH)
    norm_idx: dict[str, int] = {_norm(t): i for i, t in enumerate(model.teams)}

    log.info("Fetching live state from api-football...")
    live_state = _fetch_live_state(norm_idx, model.teams)
    if not live_state:
        log.info("No live football — nothing to scan.")
        if not dry_run:
            conn = _conn()
            try:
                _log_run(conn, 0, 0, 0, dry_run)
            finally:
                conn.close()
        return {"live_matches": 0, "edges": 0, "trades": 0, "dry_run": dry_run}

    days_window = max(1, (hours_window + 23) // 24)
    log.info(f"Fetching PM events ({hours_window}h window)...")
    events = _fetch_pm_events(days_window)
    log.info(f"  {len(events)} PM events retrieved")

    by_match, n_non_football, n_outside = _group_pm_events_for_inplay(
        events, norm_idx, model.teams, hours_window
    )
    log.info(
        f"  Grouped to {len(by_match)} match(es) in DC model "
        f"({n_non_football} non-football, {n_outside} outside {hours_window}h window)"
    )

    conn = None if dry_run else _conn()

    try:
        strategy_id = None if dry_run else _get_or_create_strategy(conn)

        n_edges = 0
        trades_logged: list[int] = []
        n_with_state = 0

        for (home, away), match_events in by_match.items():
            # Both keys are canonical model team names — direct lookup.
            ls = live_state.get((home, away))
            if not ls:
                continue

            minute = ls["minute"]
            if minute >= 92:
                continue  # essentially full-time; nothing to price

            n_with_state += 1
            home_score = ls["home_score"]
            away_score = ls["away_score"]
            home_reds = ls["home_reds"]
            away_reds = ls["away_reds"]

            try:
                pred = model.predict(home, away)
                lh, la = pred["lambda_home"], pred["lambda_away"]
            except Exception:
                continue

            initial = MatchState.at(
                n_sims=SIM_N,
                minute=minute,
                home=home_score,
                away=away_score,
                red_h=home_reds,
                red_a=away_reds,
            )
            sim_res = simulate(
                lh,
                la,
                initial_state=initial,
                config=SimConfig(n_sims=SIM_N, seed=None),
            )
            sim_p = price_markets(sim_res)

            db_match_id = (
                find_match_id(conn, home, away, datetime.now(timezone.utc).date())
                if not dry_run
                else None
            )

            candidates: list[dict] = []
            for event in match_events:
                for mkt in event.get("markets", []):
                    if not mkt.get("active") or mkt.get("closed"):
                        continue
                    if mkt.get("bestBid") is None or mkt.get("bestAsk") is None:
                        continue

                    raw = mkt.get("outcomePrices") or mkt.get("outcome_prices")
                    if not raw:
                        continue
                    prices = json.loads(raw) if isinstance(raw, str) else raw
                    yes_p = float(prices[0])
                    if yes_p <= 0.04 or yes_p >= 0.96:
                        continue

                    question = mkt.get("question", "")
                    outcome_key = _classify_market(question, home, away)
                    if not outcome_key or outcome_key not in sim_p:
                        continue

                    # Halftime markets are resolved once we're past 1H + stoppage.
                    # The sim assumes "current score == HT score" which is only
                    # valid up to minute 46. Skip past that to avoid false edges
                    # from pre-match PM prices that haven't been locked.
                    if (
                        MARKET_GROUP.get(outcome_key) == "halftime"
                        and minute >= 47
                    ):
                        continue

                    sim_prob = float(sim_p[outcome_key])
                    edge_pp = round((sim_prob - yes_p) * 100, 1)
                    if edge_pp < threshold_pp:
                        continue

                    sim_se = float(((sim_prob * (1 - sim_prob)) / SIM_N) ** 0.5)
                    n_edges += 1

                    reasoning = (
                        f"MC Sim In-Play: {home} vs {away} — {question}. "
                        f"Live: {home_score}-{away_score} @ {minute}' "
                        f"(reds: H={home_reds} A={away_reds}). "
                        f"PM: {yes_p*100:.1f}% ({1/yes_p:.2f}). "
                        f"Sim fair: {sim_prob*100:.1f}% (±{sim_se*100:.2f}pp, n={SIM_N}). "
                        f"Edge: +{edge_pp:.1f}pp. "
                        f"Pre-match λ home={lh:.2f} λ away={la:.2f}."
                    )
                    log.info(
                        f"  EDGE +{edge_pp:.1f}pp | {home} {home_score}-{away_score} {away} "
                        f"@{minute}' | {outcome_key} ({MARKET_GROUP.get(outcome_key,'?')}) | "
                        f"PM={yes_p*100:.1f}% Sim={sim_prob*100:.1f}%"
                    )

                    candidates.append(
                        {
                            "event": event,
                            "mkt": mkt,
                            "outcome_key": outcome_key,
                            "group": MARKET_GROUP.get(outcome_key, "other"),
                            "yes_p": yes_p,
                            "sim_prob": sim_prob,
                            "sim_se": sim_se,
                            "edge_pp": edge_pp,
                            "reasoning": reasoning,
                        }
                    )

            if not candidates or dry_run:
                continue

            best_per_group: dict[str, dict] = {}
            for c in candidates:
                g = c["group"]
                if g not in best_per_group or c["edge_pp"] > best_per_group[g]["edge_pp"]:
                    best_per_group[g] = c

            for g, best in best_per_group.items():
                ext_id = str(
                    best["mkt"].get("id") or best["mkt"].get("conditionId") or ""
                )
                question = best["mkt"].get("question", "")
                market_db_id = _upsert_pm_market(
                    conn, ext_id, question, best["event"].get("endDate")
                )
                if not market_db_id:
                    continue
                trade_id = _write_trade(
                    conn,
                    strategy_id,
                    market_db_id,
                    best["outcome_key"],
                    best["yes_p"],
                    best["sim_prob"],
                    best["edge_pp"],
                    best["sim_se"],
                    best["reasoning"],
                    match_id=db_match_id,
                    home=home,
                    away=away,
                    score=(home_score, away_score),
                    minute=minute,
                    reds=(home_reds, away_reds),
                )
                if trade_id:
                    log.info(f"    → Trade #{trade_id} logged ({g})")
                    trades_logged.append(trade_id)
                else:
                    log.info(
                        f"    → {g}: deduped (within {DEDUPE_WINDOW_MIN}min)"
                    )

        if not dry_run:
            _log_run(conn, n_with_state, n_edges, len(trades_logged), dry_run)
            try:
                resolved = _resolver.run()
                if resolved:
                    log.info(f"[resolver] Settled {len(resolved)} trade(s)")
            except Exception as exc:
                log.warning(f"[resolver] {exc}")

        summary = {
            "live_matches_total": len(live_state),
            "pm_matches_in_window": len(by_match),
            "matched_to_live_state": n_with_state,
            "edges_found": n_edges,
            "trades_logged": len(trades_logged),
            "dry_run": dry_run,
        }
        log.info(
            f"Done — {n_with_state} live in DC model | "
            f"{n_edges} edges, {len(trades_logged)} trades"
        )
        return summary

    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo Sim in-play scanner")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_EDGE_THRESHOLD_PP,
        help=f"Edge threshold in pp (default: {DEFAULT_EDGE_THRESHOLD_PP})",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=DEFAULT_HOURS_WINDOW,
        help=f"PM event window in hours from now (default: {DEFAULT_HOURS_WINDOW})",
    )
    args = parser.parse_args()

    result = run(
        threshold_pp=args.threshold,
        dry_run=args.dry_run,
        hours_window=args.hours,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
