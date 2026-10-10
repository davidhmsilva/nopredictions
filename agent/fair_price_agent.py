"""Pre-match sharp-move lag agent for Polymarket football 1X2.

Static PM-versus-sharp value already failed in this project. This strategy
therefore requires a *new* information event: Pinnacle and Betfair must move
up together, while Polymarket's executable YES ask fails to follow. It records
its own timestamped observations and only paper-enters when the movement gap
and current after-fee value both clear fixed thresholds.

Run from the repository root:
    python -m agent.fair_price_agent --dry-run
    python -m agent.fair_price_agent

The strategy never places real orders.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg2
import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "../ingest/.env"))

from . import paper_trader as pmdata  # shared market discovery and name matching
from .edge_engine import FEE_RATE
from .tools.db import find_match_id

log = logging.getLogger("fair_price_agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DATABASE_URL = os.getenv("DATABASE_URL")
CLOB = os.getenv("POLYMARKET_CLOB_API", "https://clob.polymarket.com").rstrip("/")
STRATEGY_NAME = "Luna Sharp-Move Lag 1X2 v1"
STRATEGY_RULES = {
    "sport": "football",
    "markets": "full-time 1X2 binary YES tokens",
    "fair_value": "lower de-vigged probability of Pinnacle and Betfair",
    "entry_signal": "dual-sharp probability rises >=2pp and PM ask captures <50% of move",
    "minimum_new_gap_pp": 1.5,
    "observation_pair_age_minutes": [10, 45],
    "requires_both_sharps": True,
    "max_sharp_disagreement_pp": 4.0,
    "minimum_net_ev_pct": 3.0,
    "pm_taker_fee_rate": FEE_RATE,
    "price_band": [0.08, 0.92],
    "max_spread": 0.05,
    "minimum_best_ask_depth_usd": 25.0,
    "kickoff_window_minutes": [30, 720],
    "position_limit": "one best selection per fixture, 1u flat",
    "execution": "paper only; entry marked at current CLOB best ask",
}
STAKE_UNITS = 1.0
MIN_NET_EV_PCT = 3.0
MAX_SHARP_DISAGREEMENT_PP = 4.0
MIN_PRICE, MAX_PRICE = 0.08, 0.92
MAX_SPREAD = 0.05
MIN_ASK_DEPTH_USD = 25.0
MIN_MINUTES_TO_KO, MAX_MINUTES_TO_KO = 30.0, 720.0
SHARP_KEYS = ("pinnacle", "betfair_ex_eu")
STATE_PATH = os.path.join(HERE, ".luna_sharp_move_state.json")
MIN_PAIR_AGE_MIN, MAX_PAIR_AGE_MIN = 10.0, 45.0
MIN_SHARP_MOVE_PP = 2.0
MIN_NEW_GAP_PP = 1.5


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    ask_depth_usd: float


@dataclass(frozen=True)
class Pick:
    market: dict
    sharp: dict
    outcome_key: str
    label: str
    fair: float
    pinnacle_prob: float
    betfair_prob: float
    disagreement_pp: float
    quote: Quote
    net_ev_pct: float
    minutes_to_ko: float
    token_id: str
    sharp_move_pp: float = 0.0
    lag_gain_pp: float = 0.0


def _prob(event: dict, source: str, outcome_key: str) -> float | None:
    """Return one bookmaker's already de-vigged probability."""
    row = (event.get("sources") or {}).get(source)
    if not isinstance(row, dict):
        return None
    key = {"home": "home", "draw": "draw", "away": "away"}.get(outcome_key)
    value = row.get(key) if key else None
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    return p if 0.0 < p < 1.0 else None


def conservative_fair(event: dict, outcome_key: str) -> tuple[float, float, float, float] | None:
    """Require both sharp books; use the lower probability as our fair value.

    The lower envelope is deliberately conservative: disagreement cannot
    inflate the estimated edge, and a one-source quote cannot create a trade.
    """
    pin = _prob(event, "pinnacle", outcome_key)
    bf = _prob(event, "betfair_ex_eu", outcome_key)
    if pin is None or bf is None:
        return None
    disagreement = abs(pin - bf) * 100.0
    if disagreement > MAX_SHARP_DISAGREEMENT_PP:
        return None
    return min(pin, bf), pin, bf, disagreement


def net_ev_pct(fair: float, ask: float, fee_rate: float = FEE_RATE) -> float:
    """Return expected return on cash, including Polymarket taker fee."""
    fee = fee_rate * ask * (1.0 - ask)
    total_cost = ask + fee
    return 100.0 * (fair / total_cost - 1.0)


def _within_tape_retention(sample: dict, now: datetime) -> bool:
    try:
        stamp = datetime.fromisoformat(sample["at"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_s = (now - stamp).total_seconds()
        return 0.0 <= age_s <= 48 * 3600
    except (KeyError, TypeError, ValueError):
        return False


def _token_ids(market: dict) -> tuple[str, str] | None:
    raw_outcomes = market.get("outcomes") or []
    raw_tokens = market.get("clobTokenIds") or market.get("clob_token_ids") or []
    try:
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
        tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(outcomes, list) or not isinstance(tokens, list) or len(outcomes) != len(tokens):
        return None
    mapped = {str(outcome).strip().lower(): str(token) for outcome, token in zip(outcomes, tokens)}
    if "yes" not in mapped:
        return None
    return mapped["yes"], mapped.get("no", "")


def fetch_quote(token_id: str) -> Quote | None:
    try:
        response = requests.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=10)
        response.raise_for_status()
        book = response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("CLOB book unavailable for %s: %s", token_id[:12], exc)
        return None
    bids, asks = book.get("bids") or [], book.get("asks") or []
    try:
        bid = max(float(level["price"]) for level in bids)
        best_ask = min(float(level["price"]) for level in asks)
    except (ValueError, TypeError, KeyError):
        return None
    depth = sum(float(level["price"]) * float(level["size"])
                for level in asks if abs(float(level["price"]) - best_ask) < 1e-9)
    if not all(math.isfinite(x) for x in (bid, best_ask, depth)):
        return None
    return Quote(bid=bid, ask=best_ask, ask_depth_usd=depth)


def _parse_market(market: dict, sharp_lookup: dict, now: datetime) -> Pick | None:
    title = market.get("question") or market.get("title") or ""
    if not pmdata._is_1x2_market(title):
        return None
    home, away = market.get("_home_team"), market.get("_away_team")
    if not home or not away:
        pair = pmdata._extract_two_teams(title)
        if pair:
            home, away = pair
    if not home or not away:
        return None
    sharp = pmdata._fuzzy_find_event_two(home, away, sharp_lookup)
    if not sharp:
        return None
    kickoff = sharp.get("commence_time")
    if not isinstance(kickoff, datetime):
        kickoff = pmdata._parse_dt(kickoff)
    if kickoff is None:
        return None
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    minutes = (kickoff - now).total_seconds() / 60.0
    if not MIN_MINUTES_TO_KO <= minutes <= MAX_MINUTES_TO_KO:
        return None
    classified = pmdata._classify_outcome(title, sharp.get("home", home), sharp.get("away", away))
    if not classified:
        return None
    outcome_key, label = classified
    fair_parts = conservative_fair(sharp, outcome_key)
    ids = _token_ids(market)
    if fair_parts is None or ids is None:
        return None
    fair, pin, betfair, disagreement = fair_parts
    quote = fetch_quote(ids[0])
    if quote is None:
        return None
    if not MIN_PRICE <= quote.ask <= MAX_PRICE:
        return None
    if quote.ask - quote.bid > MAX_SPREAD or quote.ask_depth_usd < MIN_ASK_DEPTH_USD:
        return None
    ev = net_ev_pct(fair, quote.ask)
    # This routine gathers clean observations, not picks. The movement trigger
    # is applied only after a prior timestamped observation is found.
    return Pick(market, sharp, outcome_key, label, fair, pin, betfair,
                disagreement, quote, ev, minutes, ids[0])


def scan_inputs(days: int = 1) -> list[Pick]:
    """Fetch today's/tomorrow's football markets and fresh sharp odds."""
    now = datetime.now(timezone.utc)
    pmdata.DAYS_AHEAD = max(1, min(days, 3))
    markets = pmdata.fetch_pm_markets_today()
    if not markets:
        return []
    hints = [m.get("question") or m.get("title") or "" for m in markets]
    tags = [tag for m in markets for tag in m.get("_event_tags", [])]
    lookup = pmdata.fetch_sharp_odds_today(team_hints=hints, tag_hints=tags)
    if not lookup:
        log.warning("No Odds API sharp prices available; this strategy requires Pinnacle and Betfair.")
        return []
    # Timestamp the paired observations after the network fetches, not before
    # Gamma and Odds API latency.
    now = datetime.now(timezone.utc)

    # The local tape lets the strategy distinguish a new sharp information move
    # from the static cross-sectional price gap that failed in prior tests.
    try:
        with open(STATE_PATH, "r") as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}

    # Gather clean market observations, then test each against a prior quote.
    best: dict[str, Pick] = {}
    next_state = dict(state)
    for market in markets:
        observation = _parse_market(market, lookup, now)
        if observation is None:
            continue
        market_id = str(market.get("conditionId") or market.get("id") or "")
        if not market_id:
            continue
        tape_key = f"{market_id}|{observation.outcome_key}|YES"
        tape = state.get(tape_key) or []
        if not isinstance(tape, list):
            tape = []
        tape = [x for x in tape if _within_tape_retention(x, now)]
        prior = None
        for sample in reversed(tape):
            try:
                stamp = datetime.fromisoformat(sample["at"])
                age = (now - stamp).total_seconds() / 60.0
            except (KeyError, TypeError, ValueError):
                continue
            if MIN_PAIR_AGE_MIN <= age <= MAX_PAIR_AGE_MIN:
                prior = sample
                break

        sample = {"at": now.isoformat(), "fair": observation.fair,
                  "ask": observation.quote.ask, "sharp_low": observation.fair}
        tape.append(sample)
        next_state[tape_key] = tape[-96:]

        if prior is None:
            continue
        sharp_move = 100.0 * (observation.fair - float(prior["fair"]))
        lag_gain = 100.0 * ((observation.fair - observation.quote.ask)
                            - (float(prior["fair"]) - float(prior["ask"])))
        # The sharp price must have moved in the direction we buy. PM must have
        # captured less than half that move, leaving a newly created lag gap.
        pm_move = 100.0 * (observation.quote.ask - float(prior["ask"]))
        if sharp_move < MIN_SHARP_MOVE_PP or pm_move > 0.5 * sharp_move:
            continue
        if lag_gain < MIN_NEW_GAP_PP or observation.net_ev_pct < MIN_NET_EV_PCT:
            continue
        pick = Pick(**{**observation.__dict__, "sharp_move_pp": sharp_move,
                       "lag_gain_pp": lag_gain})
        fixture_key = "|".join(sorted((pmdata._norm(pick.sharp.get("home", "")),
                                       pmdata._norm(pick.sharp.get("away", "")))))
        if fixture_key not in best or pick.net_ev_pct > best[fixture_key].net_ev_pct:
            best[fixture_key] = pick

    # Bound the machine-local tape and replace atomically so interrupted scans
    # cannot leave a half-written baseline.
    next_state = {key: [sample for sample in samples if _within_tape_retention(sample, now)]
                  for key, samples in next_state.items() if isinstance(samples, list)}
    temp_path = STATE_PATH + ".tmp"
    with open(temp_path, "w") as fh:
        json.dump(next_state, fh, separators=(",", ":"))
    os.replace(temp_path, STATE_PATH)
    return list(best.values())


def _connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing from ingest/.env")
    return psycopg2.connect(DATABASE_URL)


def ensure_strategy(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute("""
            INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
            VALUES (%s, %s, %s, 'agent', 'live', 'agent') RETURNING id
        """, (
            "H-LUNA-MOVE-LAG — trade new dual-sharp moves that Polymarket has not followed",
            "A pre-match football 1X2 strategy requiring a timestamped sharp move and a newly widening executable-price gap.",
            "Static PM-vs-sharp value was previously unprofitable. This hypothesis tests whether "
            "a fresh move agreed by Pinnacle and Betfair predicts short-lived Polymarket lag."
        ))
        hypothesis_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO strategies (hypothesis_id, name, rules, source, run_status, theory)
            VALUES (%s, %s, %s::jsonb, 'agent', 'running', %s) RETURNING id
        """, (hypothesis_id, STRATEGY_NAME, json.dumps(STRATEGY_RULES),
              "Conservative dual-sharp 1X2 value; one flat-stake paper selection per fixture."))
        strategy_id = int(cur.fetchone()[0])
    conn.commit()
    return strategy_id


def _write_pick(conn, strategy_id: int, pick: Pick) -> int | None:
    market = pick.market
    market_id = pmdata._upsert_pm_market(conn, market)
    if market_id is None:
        return None
    # The resolver interprets these standard keys against the linked match;
    # persist the orientation, while the human-readable team name stays in the
    # reasoning string. This avoids treating an away team's name as a home win.
    outcome = {"home": "home_win", "draw": "draw", "away": "away_win"}[pick.outcome_key]
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM paper_trades
                         WHERE strategy_id = %s AND market_id = %s AND outcome = %s LIMIT 1""",
                    (strategy_id, market_id, outcome))
        if cur.fetchone():
            return None
        kickoff = pick.sharp.get("commence_time")
        match_id = find_match_id(conn, pick.sharp.get("home", ""), pick.sharp.get("away", ""),
                                 kickoff.date() if isinstance(kickoff, datetime) else None)
        source = {
            "agent": "Luna Sharp-Move Lag 1X2 v1",
            "fair_method": "min(Pinnacle de-vigged, Betfair de-vigged)",
            "outcome_key": pick.outcome_key,
            "pinnacle_prob": round(pick.pinnacle_prob, 6),
            "betfair_prob": round(pick.betfair_prob, 6),
            "sharp_disagreement_pp": round(pick.disagreement_pp, 3),
            "sharp_move_pp": round(pick.sharp_move_pp, 3),
            "pm_lag_gain_pp": round(pick.lag_gain_pp, 3),
            "fair_prob": round(pick.fair, 6),
            "ask": round(pick.quote.ask, 6), "bid": round(pick.quote.bid, 6),
            "best_ask_depth_usd": round(pick.quote.ask_depth_usd, 2),
            "net_ev_pct": round(pick.net_ev_pct, 3), "fee_rate": FEE_RATE,
            "minutes_to_kickoff": round(pick.minutes_to_ko, 1),
            "sharp_sources": list(SHARP_KEYS),
        }
        reasoning = (
            f"Luna Sharp-Move Lag v1 | {pick.sharp.get('home')} vs {pick.sharp.get('away')} | "
            f"{outcome} | CLOB YES ask {pick.quote.ask:.4f}, bid {pick.quote.bid:.4f}, "
            f"ask depth ${pick.quote.ask_depth_usd:.2f}; Pinnacle {pick.pinnacle_prob:.4f}, "
            f"Betfair {pick.betfair_prob:.4f}; conservative fair {pick.fair:.4f}; "
            f"sharp move {pick.sharp_move_pp:+.2f}pp, new PM lag {pick.lag_gain_pp:+.2f}pp; "
            f"book disagreement {pick.disagreement_pp:.2f}pp; net EV {pick.net_ev_pct:.2f}% "
            f"after PM taker fee; {pick.minutes_to_ko:.0f}m to kickoff. PAPER ONLY."
        )
        cur.execute("""
            INSERT INTO paper_trades (strategy_id, market_id, match_id, outcome, entry_price,
                entry_odds, stake_units, model_probability, sharp_consensus_price,
                sharp_consensus_sources, expected_edge, confidence, reasoning, pm_token_id,
                pm_live, placed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,FALSE,now())
            RETURNING id
        """, (strategy_id, market_id, match_id, outcome, pick.quote.ask,
              1.0 / pick.quote.ask, STAKE_UNITS, pick.fair, pick.fair,
              json.dumps(source), pick.net_ev_pct / 100.0, "dual-sharp", reasoning,
              pick.token_id))
        trade_id = int(cur.fetchone()[0])
    conn.commit()
    return trade_id


def run(days: int = 1, dry_run: bool = False) -> list[Pick]:
    picks = scan_inputs(days=days)
    log.info("%d qualifying fixture(s) after dual-sharp, CLOB, fee and liquidity gates", len(picks))
    for pick in sorted(picks, key=lambda p: p.net_ev_pct, reverse=True):
        log.info("BUY YES %-14s fair=%.3f ask=%.3f netEV=%+.2f%% pin=%.3f bf=%.3f "
                 "move=%+.2fpp lag=%+.2fpp disagree=%.2fpp depth=$%.0f KO=%+.0fm | %s",
                 pick.label, pick.fair, pick.quote.ask, pick.net_ev_pct,
                 pick.pinnacle_prob, pick.betfair_prob, pick.sharp_move_pp, pick.lag_gain_pp,
                 pick.disagreement_pp,
                 pick.quote.ask_depth_usd, pick.minutes_to_ko,
                 pick.market.get("question", ""))
    if dry_run or not picks:
        if dry_run:
            log.info("dry-run: no strategy/trade records written")
        return picks
    with _connect() as conn:
        strategy_id = ensure_strategy(conn)
        written = 0
        for pick in picks:
            trade_id = _write_pick(conn, strategy_id, pick)
            if trade_id:
                written += 1
                log.info("paper trade #%d recorded", trade_id)
    log.info("recorded %d new paper selection(s)", written)
    return picks


def main() -> None:
    parser = argparse.ArgumentParser(description="Dual-sharp move / Polymarket lag agent (paper only).")
    parser.add_argument("--days", type=int, default=1, help="scan today plus up to this many days ahead (max 3)")
    parser.add_argument("--dry-run", action="store_true", help="scan and print; updates only the local observation tape")
    args = parser.parse_args()
    run(days=args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
