"""
Sim Model Pre-Match Scanner — uses the Monte Carlo simulator to price
Polymarket markets across a richer set than DC: 1X2, totals 0.5-4.5,
BTTS, half-time 1X2, and home/away spread handicaps.

PM splits each match into ~5 sibling events (main 1X2, Halftime Result,
More Markets, Exact Score, Player Props). We group them by match, run the
sim ONCE per match, and emit up to one trade per (match, market_group):
1x2 / halftime / totals / btts / handicap.

Runs alongside dc_scanner; distinct strategy_id makes trades A/B-comparable.

Usage:
  cd agent && source ../ingest/.venv/bin/activate
  python sim_scanner.py              # live (writes to DB)
  python sim_scanner.py --dry-run    # no DB writes
  python sim_scanner.py --days 5     # 5-day window
  python sim_scanner.py --threshold 3.5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

sys.path.insert(0, os.path.dirname(__file__))
from dixon_coles import DixonColesModel  # noqa: E402
import resolver as _resolver  # noqa: E402

# Reuse the well-tested PM plumbing from dc_scanner
from dc_scanner import (  # noqa: E402
    _norm,
    _find_team,
    _is_football_event,
    _fetch_pm_events,
    _pm_token_id,
    NON_FOOTBALL_NAME_TOKENS,
)
import live_executor  # noqa: E402

# Sim engine
from sim.simulator import simulate, SimConfig  # noqa: E402
from sim.pricer import price_markets  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
from db import find_match_id  # noqa: E402


DATABASE_URL = os.getenv("DATABASE_URL")
PARAMS_PATH = os.path.join(os.path.dirname(__file__), "dc_model_params.json")
STRATEGY_NAME = "Sim Model Pre-Match"
STAKE_UNITS = 1.0
DEFAULT_EDGE_THRESHOLD_PP = 5.0
DEFAULT_DAYS_AHEAD = 3
SIM_N = 50_000
SIM_SEED = 42

# ── Live-eligible pockets (updated 2026-06-06) ───────────────────────────────
# P&L audit 2026-06-05, n=384 settled sim trades:
#   Profitable pockets: draw +36% (incl. ht_draw), ht_away_win +28%,
#                       home_wins_by_2plus +12%
#   Losing pockets:     btts −27.7%, totals −49.1%, ht_home_win −23%
#                       (these are blocked via DISABLED_GROUPS above)
# Edge band: 5-15pp is the sweet spot; <5pp noise, >15pp overconfident.
# Earlier (2026-06-01) analysis said draws lose — that was based on a smaller
# sample with a different edge filter; the June-05 audit supersedes it.
SIM_LIVE_MIN_EDGE_PP = float(os.environ.get("PM_SIM_LIVE_MIN_EDGE_PP", "5.0"))
SIM_LIVE_EXCLUDE_DRAWS = os.environ.get("PM_SIM_LIVE_EXCLUDE_DRAWS", "0") == "1"


def _sim_live_eligible(outcome_key: str, edge_pp: float) -> bool:
    """True if this Sim pick may be submitted with real money. Goals (totals/
    BTTS) are blocked via DISABLED_GROUPS; the 15pp top by MAX_EDGE_THRESHOLD_PP.
    Here we add the 5pp floor. Draws ARE eligible (draw +36%, ht_draw included
    in halftime group)."""
    if SIM_LIVE_EXCLUDE_DRAWS and outcome_key in ("draw", "ht_draw"):
        return False
    return edge_pp >= SIM_LIVE_MIN_EDGE_PP

# Re-configure with force=True so our format wins over dc_scanner's
# basicConfig that fired on import.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sim_scanner] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("sim_scanner")


# ── Market keys & groups ──────────────────────────────────────────────────────

SIM_MARKETS = {
    # 1X2
    "home_win", "draw", "away_win",
    # Totals
    "over_0_5", "over_1_5", "over_2_5", "over_3_5", "over_4_5", "over_5_5",
    "under_2_5", "under_3_5",
    # BTTS
    "btts", "no_btts",
    # Half-time
    "ht_home_win", "ht_draw", "ht_away_win",
    # Half-time totals (observation only — see DISABLED_GROUPS)
    "ht_over_0_5", "ht_over_1_5", "ht_over_2_5",
    "ht_under_0_5", "ht_under_1_5", "ht_under_2_5",
    # Handicap (yes side of "Spread: X (-N.5)")
    "home_wins_by_2plus", "home_wins_by_3plus",
    "away_wins_by_2plus", "away_wins_by_3plus",
}

MARKET_GROUP: dict[str, str] = {
    "home_win": "1x2", "draw": "1x2", "away_win": "1x2",
    "ht_home_win": "ht_home_win", "ht_draw": "halftime", "ht_away_win": "halftime",
    "btts": "btts", "no_btts": "btts",
    "home_wins_by_2plus": "handicap", "home_wins_by_3plus": "handicap",
    "away_wins_by_2plus": "handicap", "away_wins_by_3plus": "handicap",
}
for _line in ("0_5", "1_5", "2_5", "3_5", "4_5", "5_5"):
    MARKET_GROUP[f"over_{_line}"] = "totals"
    MARKET_GROUP[f"under_{_line}"] = "totals"
for _line in ("0_5", "1_5", "2_5"):
    MARKET_GROUP[f"ht_over_{_line}"] = "ht_totals"
    MARKET_GROUP[f"ht_under_{_line}"] = "ht_totals"

# Groups that only describe the first half — in-play they are resolved once 1H
# is over, and the sim's "current score == HT score" assumption stops holding.
# ht_home_win sits in its own group, so an `== "halftime"` check would miss it.
HALF_SCOPED_GROUPS: set[str] = {"halftime", "ht_home_win", "ht_totals"}

# Market groups disabled based on P&L audit (2026-06-05, n=384):
#   btts    −27.7%  (n=59)  — sim sobreestima golos
#   totals  −49.1%  (n=29)  — idem
#   ht_home_win −23.0% (n=55) — sistematicamente mal calibrado
# Profitable: ht_away_win (+28%), draw (+36%), home_wins_by_2plus (+12%)
# Override individual groups via SIM_ENABLE_GOALS_MARKETS=1 or SIM_ENABLE_HT_HOME=1.
DISABLED_GROUPS: set[str] = set()
if os.environ.get("SIM_ENABLE_GOALS_MARKETS") != "1":
    DISABLED_GROUPS |= {"btts", "totals"}
if os.environ.get("SIM_ENABLE_HT_HOME") != "1":
    DISABLED_GROUPS.add("ht_home_win")
# Half-time totals are priced for the observation layer only — never bet.
# The 236-match study (2026-07-21) says PM overprices the over ~4pp during 0-0,
# but every CI crosses zero and book liquidity is ~$900. Observe first.
if os.environ.get("SIM_ENABLE_HT_TOTALS") != "1":
    DISABLED_GROUPS.add("ht_totals")

# Edge cap: above 15pp the model is likely overconfident (wrong team match, women's game,
# etc.). P&L audit: 20pp+ yield = −76% (n=29). Below 5pp is noise (−46%, n=71).
MAX_EDGE_THRESHOLD_PP = float(os.environ.get("SIM_MAX_EDGE_PP", "15.0"))


def _group_outcomes(group: str) -> list[str]:
    return [k for k, g in MARKET_GROUP.items() if g == group]


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
            'Monte Carlo Sim Pre-Match Edge Scanner',
            'Polymarket pre-match prices diverge from Monte Carlo simulator fair value '
            'across 1X2, halftime 1X2, BTTS, totals 0.5-4.5, and spread handicaps.',
            'Possession-based MC simulator with state-dependent dynamics (red cards, '
            'late-game push, leader sit-back). Same DC lambdas, much richer pricing.',
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
                    "model": "Monte Carlo simulator (DC lambdas + state dynamics)",
                    "benchmark": "Sim fair probability",
                    "edge_threshold_pp": DEFAULT_EDGE_THRESHOLD_PP,
                    "n_sims": SIM_N,
                    "stake": "1u flat",
                    "phase": "paper-only",
                    "markets": "1X2, halftime 1X2, BTTS, totals 0.5-4.5, handicaps",
                    "trade_policy": "best edge per (match, market_group)",
                }
            ),
        ),
    )
    strat_id = cur.fetchone()[0]
    conn.commit()
    log.info(f"Created strategy '{STRATEGY_NAME}' (id={strat_id})")
    return strat_id


def _mtype_for(question: str) -> str:
    t = question.lower()
    if "both teams" in t and "score" in t:
        return "btts"
    if "halftime" in t or "half-time" in t or "first half" in t or "1st half" in t:
        return "halftime"
    if "o/u" in t or "over" in t or "under" in t:
        return "over_under"
    if "spread" in t:
        return "handicap"
    if "win" in t or "draw" in t:
        return "1x2"
    return "other"


def _upsert_pm_market(
    conn, ext_id: str, title: str, resolution_time: Optional[str]
) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pm_markets (platform, external_id, title, market_type,
                                resolution_time, status, ingested_at)
        VALUES ('polymarket', %s, %s, %s, %s, 'active', NOW())
        ON CONFLICT (platform, external_id) DO UPDATE SET
            title = EXCLUDED.title,
            resolution_time = EXCLUDED.resolution_time,
            ingested_at = NOW()
        RETURNING id
        """,
        (ext_id, title, _mtype_for(title), resolution_time),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _already_traded_in_group(
    conn,
    strategy_id: int,
    market_group: str,
    match_id: Optional[int],
    home: Optional[str],
    away: Optional[str],
) -> bool:
    """One trade per (match, market_group) — allow 1X2 + halftime + totals on the same match."""
    group_outcomes = _group_outcomes(market_group)
    if not group_outcomes:
        return False

    cur = conn.cursor()
    if match_id:
        cur.execute(
            """
            SELECT id FROM paper_trades
            WHERE strategy_id = %s AND match_id = %s AND outcome = ANY(%s)
              AND result IS NULL
            LIMIT 1
            """,
            (strategy_id, match_id, group_outcomes),
        )
        if cur.fetchone():
            return True
    if home and away:
        pattern = f"%{home} vs {away}%"
        cur.execute(
            """
            SELECT id FROM paper_trades
            WHERE strategy_id = %s AND reasoning LIKE %s AND outcome = ANY(%s)
              AND result IS NULL
            LIMIT 1
            """,
            (strategy_id, pattern, group_outcomes),
        )
        if cur.fetchone():
            return True
    return False


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
) -> Optional[int]:
    group = MARKET_GROUP.get(outcome, "other")
    if _already_traded_in_group(conn, strategy_id, group, match_id, home, away):
        return None

    entry_odds = round(1 / entry_price, 4) if entry_price > 0 else None

    # Confidence: scale by edge size, then by edge/SE separation
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
                    "source": "MC Sim (DC lambdas + dynamics)",
                    "market_group": group,
                    "sim_prob": round(sim_prob, 4),
                    "sim_se_pp": round(sim_se * 100, 3),
                    "n_sims": SIM_N,
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


def _log_run(conn, n_events: int, n_matches: int, n_edges: int, dry_run: bool):
    summary = (
        f"{'[DRY RUN] ' if dry_run else ''}"
        f"Scanned {n_events} PM events, grouped to {n_matches} matches, "
        f"found {n_edges} edges."
    )
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_runs
            (run_type, started_at, finished_at, input_context, output_summary, status)
        VALUES ('sim_scanner:daily', NOW(), NOW(), %s::jsonb, %s, 'completed')
        """,
        (
            json.dumps(
                {
                    "events": n_events,
                    "matches": n_matches,
                    "edges": n_edges,
                    "dry_run": dry_run,
                }
            ),
            summary,
        ),
    )
    conn.commit()


# ── Team extraction & event grouping ──────────────────────────────────────────

# Suffixes PM tacks onto sibling events of the same match.
_SIBLING_SUFFIXES = re.compile(
    r"\s*-\s*(halftime\s+result|more\s+markets|exact\s+score|player\s+props|spread|"
    r"corners|cards|first\s+half|btts|both\s+teams\s+to\s+score)\s*$",
    re.I,
)


def _extract_teams(title: str) -> Optional[tuple[str, str]]:
    """Return (home_raw, away_raw) from a PM event title, stripping sibling suffix."""
    clean = _SIBLING_SUFFIXES.sub("", title).strip()
    # Existing dc_scanner stripping
    clean = re.sub(r"\s+(FC|CF|SC|AFC|RC|CD|RCD|FK|SK)$", "", clean, flags=re.I)
    m = re.match(
        r"(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s*[-:]|$)", clean, re.I
    )
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


# Title-level skip — exclude sibling events that don't add tradeable markets.
# Crucially we DO process "Halftime Result" and "More Markets" siblings.
_SKIP_EVENT_TITLE = re.compile(
    r"exact\s+score|player\s+props|corners|cards|winner|champion|outright|"
    r"top\s+scorer|relegation|promotion",
    re.I,
)


# ── Market classification ─────────────────────────────────────────────────────

_RE_OVER_UNDER = re.compile(r"\b(over|under|o/u|ou)\s+(\d+(?:\.\d+)?)\b", re.I)
_RE_SPREAD = re.compile(
    r"spread:\s*(.+?)\s*\(\s*[-−]\s*(\d+(?:\.\d+)?)\s*\)", re.I
)
_RE_WIN_BY = re.compile(r"win\s+by\s+(\d+)\s*(?:\+|or\s+more)?", re.I)
_RE_HALF_TAG = re.compile(r"\b(?:1st|2nd|first|second)\s+half\b|\bhalf[-\s]?time\b", re.I)


def _totals_scope(question: str, ou_start: int) -> str:
    """
    Scope of an O/U market, read from the text between the title's last ':' and
    the O/U token. PM is rigidly consistent here (checked over 955 live
    questions): '' → whole match, '1st Half' / '2nd Half' → that half, anything
    else → a team total.

    This replaces a team-name-overlap heuristic that silently failed on short
    club names ('Jeju SK FC'), letting team totals through as match totals.
    """
    pre = question[:ou_start].split(":")[-1]
    is_half = bool(_RE_HALF_TAG.search(pre))
    if _RE_HALF_TAG.sub(" ", pre).strip():
        return "team"
    return "half" if is_half else "match"


def _team_match(question_norm: str, home_norm_words: set, away_norm_words: set) -> Optional[str]:
    """Return 'home' / 'away' / None based on token overlap with question.
    BOTH teams matching is ambiguous (e.g. WC sibling titles prefixed
    "Mexico vs. South Africa: ...") — never pick a side from those."""
    q_words = set(question_norm.split())
    h = bool(home_norm_words & q_words)
    a = bool(away_norm_words & q_words)
    if h and a:
        return None
    if h:
        return "home"
    if a:
        return "away"
    return None


def _classify_market(
    question: str, home: str, away: str
) -> Optional[str]:
    """Map a PM question → sim market key, or None if not priced."""
    q = question.lower()
    h_words = {w for w in _norm(home).split() if len(w) >= 5}
    a_words = {w for w in _norm(away).split() if len(w) >= 5}

    # ── Spreads ──────────────────────────────────────────────────────
    sm = _RE_SPREAD.search(q)
    if sm:
        team_in_spread = sm.group(1).strip()
        try:
            line = float(sm.group(2))
        except ValueError:
            return None
        side = _team_match(_norm(team_in_spread), h_words, a_words)
        if side is None:
            return None
        # PM lines: -1.5 → wins by 2+, -2.5 → wins by 3+. Half-goal only.
        if 1.0 < line < 2.0:
            return f"{side}_wins_by_2plus"
        if 2.0 < line < 3.0:
            return f"{side}_wins_by_3plus"
        return None  # other lines not priced

    # ── Second-half markets — NOT priced by the sim (would be mispriced as
    # full-match winner). Reject before the half-time / ML branches.
    if any(p in q for p in ("second half", "2nd half")):
        return None

    # ── Half-time markets ────────────────────────────────────────────
    if any(p in q for p in ("first half", "1st half", "halftime", "half-time", "half time")):
        # Half totals ("…: 1st Half O/U 0.5") live in the -more-markets sub-event
        # and must be read as totals, not as the half-time 1X2. Team-qualified
        # variants ("…: Jeju SK FC 1st Half O/U 0.5") are not priced.
        hm = _RE_OVER_UNDER.search(q)
        if hm:
            if _totals_scope(q, hm.start()) != "half":
                return None
            raw_side = hm.group(1).lower()
            side = "over" if raw_side in ("o/u", "ou") else raw_side
            line = hm.group(2)
            if "." not in line:
                line = line + ".5"
            key = f"ht_{side}_{line.replace('.', '_')}"
            return key if key in SIM_MARKETS else None
        # "Both Teams to Score in First Half" is its own market, not the HT 1X2.
        # Reject before the team-name match below, which would read the trailing
        # team name out of the title prefix and call it ht_away_win.
        if "both teams" in q and "score" in q:
            return None
        if "draw" in q:
            return "ht_draw"
        side = _team_match(_norm(q), h_words, a_words)
        if side == "home":
            return "ht_home_win"
        if side == "away":
            return "ht_away_win"
        return None

    # ── BTTS ─────────────────────────────────────────────────────────
    if ("both teams" in q and "score" in q) or "btts" in q:
        if "no" in q.split() or "not score" in q:
            return "no_btts"
        return "btts"

    # ── Pure draw market (not double-chance, not halftime — checked above) ──
    if "draw" in q and "win" not in q and " or " not in q:
        return "draw"

    # ── Over/Under N.5 ───────────────────────────────────────────────
    m = _RE_OVER_UNDER.search(q)
    if m:
        # Qualified totals are NOT the full-match total: team totals
        # ("…: Mexico O/U 1.5") and half totals ("…: 2nd Half O/U 0.5").
        if _totals_scope(q, m.start()) != "match":
            return None
        raw_side = m.group(1).lower()
        # YES of "O/U N.M" = over. Treat both o/u and ou as over.
        side = "over" if raw_side in ("o/u", "ou") else raw_side
        line = m.group(2)
        if "." not in line:
            line = line + ".5"
        key = f"{side}_{line.replace('.', '_')}"
        # An O/U question is a totals market even when the line isn't priced —
        # never let it fall through to the team-name match below.
        return key if key in SIM_MARKETS else None

    # ── Win-by-N phrasing (alt handicap) ─────────────────────────────
    wb = _RE_WIN_BY.search(q)
    if wb:
        try:
            n_goals = int(wb.group(1))
        except ValueError:
            n_goals = 0
        side = _team_match(_norm(q), h_words, a_words)
        if side and n_goals == 2:
            return f"{side}_wins_by_2plus"
        if side and n_goals == 3:
            return f"{side}_wins_by_3plus"
        return None  # win-by market with unpriced line / ambiguous team

    # ── Match winner (team-name match — last so structural patterns win) ──
    # Tournament-level questions name one team too ("Will Mexico win Group A
    # in the 2026 FIFA World Cup?") — never treat those as the match ML.
    if any(w in q for w in ("group", "world cup", "advance", "qualify",
                            "champion", "trophy", "tournament")):
        return None
    # A moneyline question must actually say "win"/"beat" — bare team mentions
    # ("Portugal to score first?") are some other market.
    if not re.search(r"\b(win|beat)\b", q):
        return None
    side = _team_match(_norm(q), h_words, a_words)
    if side == "home":
        return "home_win"
    if side == "away":
        return "away_win"

    return None


# ── Main ──────────────────────────────────────────────────────────────────────


def _event_is_in_future(event: dict, now: datetime) -> bool:
    """True if the event's endDate (resolution time) is still in the future.

    PM sets endDate ~3-4 hours after kickoff to allow late goals + resolution.
    If it's already past, the match is over and prices reflect resolution
    state — totals/BTTS especially can be wildly mispriced relative to
    pre-match fair value.
    """
    end_date = event.get("endDate", "")
    if not end_date:
        return True  # unknown — give the benefit of the doubt
    try:
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True
    return end_dt > now


def _group_events_by_match(
    events: list[dict],
    norm_idx: dict[str, int],
    team_names: list[str],
) -> tuple[dict[tuple[str, str], list[dict]], int, int]:
    """
    Return ({(home, away): [events…]}, n_non_football_skipped, n_past_skipped).

    Sibling events (Halftime Result, More Markets, etc.) collapse onto the
    same (home, away) key when the team match resolves to the same model teams.
    """
    by_match: dict[tuple[str, str], list[dict]] = {}
    n_non_football = 0
    n_past = 0
    now = datetime.now(timezone.utc)

    for event in events:
        title = event.get("title", "")

        if not _is_football_event(event):
            n_non_football += 1
            continue

        if _SKIP_EVENT_TITLE.search(title):
            continue

        if not _event_is_in_future(event, now):
            n_past += 1
            continue

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

    return by_match, n_non_football, n_past


def run(
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    threshold_pp: float = DEFAULT_EDGE_THRESHOLD_PP,
    dry_run: bool = False,
) -> dict:
    log.info(f"Loading DC model from {PARAMS_PATH}...")
    model = DixonColesModel.load(PARAMS_PATH)
    norm_idx: dict[str, int] = {_norm(t): i for i, t in enumerate(model.teams)}

    log.info(f"Fetching PM events (next {days_ahead} days)...")
    events = _fetch_pm_events(days_ahead)
    log.info(f"  {len(events)} events retrieved")

    by_match, n_non_football, n_past = _group_events_by_match(
        events, norm_idx, model.teams
    )
    log.info(
        f"  Grouped to {len(by_match)} match(es) in model "
        f"({n_non_football} non-football, {n_past} past-event skipped)"
    )

    conn = None if dry_run else _conn()

    try:
        strategy_id = None if dry_run else _get_or_create_strategy(conn)

        n_edges = 0
        trades_logged: list[int] = []

        for (home, away), match_events in by_match.items():
            try:
                pred = model.predict(home, away)
                lh, la = pred["lambda_home"], pred["lambda_away"]
            except Exception:
                continue

            # Single sim per match — prices every market in one pass
            sim_res = simulate(lh, la, config=SimConfig(n_sims=SIM_N, seed=SIM_SEED))
            sim_p = price_markets(sim_res)

            # Pick a kickoff date from the latest sibling endDate (any works)
            end_date = next(
                (e.get("endDate") for e in match_events if e.get("endDate")),
                "",
            )
            kickoff_date = None
            if end_date:
                try:
                    kickoff_date = datetime.fromisoformat(
                        end_date.replace("Z", "+00:00")
                    ).date()
                except (ValueError, TypeError):
                    pass
            db_match_id = (
                find_match_id(conn, home, away, kickoff_date) if not dry_run else None
            )

            # Collect candidates from all sibling events
            candidates: list[dict] = []
            for event in match_events:
                for mkt in event.get("markets", []):
                    if not mkt.get("active") or mkt.get("closed"):
                        continue

                    # Skip markets with no real two-sided order book (one side
                    # is empty → market is at a resolution extreme or never
                    # traded).
                    best_bid = mkt.get("bestBid")
                    best_ask = mkt.get("bestAsk")
                    if best_bid is None or best_ask is None:
                        continue

                    question = mkt.get("question", "")
                    raw = mkt.get("outcomePrices") or mkt.get("outcome_prices")
                    if not raw:
                        continue
                    prices = json.loads(raw) if isinstance(raw, str) else raw
                    yes_p = float(prices[0])
                    if yes_p <= 0.04 or yes_p >= 0.96:
                        continue

                    outcome_key = _classify_market(question, home, away)
                    if not outcome_key or outcome_key not in sim_p:
                        continue
                    if MARKET_GROUP.get(outcome_key, "other") in DISABLED_GROUPS:
                        continue

                    sim_prob = float(sim_p[outcome_key])
                    edge_pp = round((sim_prob - yes_p) * 100, 1)
                    if edge_pp < threshold_pp:
                        continue
                    if edge_pp > MAX_EDGE_THRESHOLD_PP:
                        continue

                    sim_se = float(((sim_prob * (1 - sim_prob)) / SIM_N) ** 0.5)
                    n_edges += 1

                    reasoning = (
                        f"MC Sim: {home} vs {away} — {question}. "
                        f"PM price: {yes_p*100:.1f}% ({1/yes_p:.2f}). "
                        f"Sim fair: {sim_prob*100:.1f}% (±{sim_se*100:.2f}pp, n={SIM_N}). "
                        f"Edge: +{edge_pp:.1f}pp. "
                        f"λ home={lh:.2f} λ away={la:.2f}."
                    )
                    log.info(
                        f"  EDGE +{edge_pp:.1f}pp | {home} vs {away} | "
                        f"{outcome_key} ({MARKET_GROUP.get(outcome_key,'?')}) | "
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

            # Group candidates by market group (1X2 / halftime / totals / btts /
            # handicap). Per group we log the global best (paper measurement) and
            # separately fund the best *eligible* pick — the group's global best
            # may be a draw we don't fund, which used to leave an eligible
            # home/away in the same group unbet.
            by_group: dict[str, list[dict]] = {}
            for c in candidates:
                by_group.setdefault(c["group"], []).append(c)

            def _log_and_maybe_execute(cand: dict, *, execute: bool) -> Optional[int]:
                ext_id = str(cand["mkt"].get("id") or cand["mkt"].get("conditionId") or "")
                mkt_db = _upsert_pm_market(
                    conn, ext_id, cand["mkt"].get("question", ""), cand["event"].get("endDate")
                )
                if not mkt_db:
                    return None
                tid = _write_trade(
                    conn, strategy_id, mkt_db, cand["outcome_key"], cand["yes_p"],
                    cand["sim_prob"], cand["edge_pp"], cand["sim_se"], cand["reasoning"],
                    match_id=db_match_id, home=home, away=away,
                )
                if not tid:
                    return None
                trades_logged.append(tid)
                if execute:
                    live_executor.try_execute(
                        conn, trade_id=tid,
                        token_id=_pm_token_id(cand["mkt"], "yes"),
                        side="BUY", price=cand["yes_p"],
                        ask=cand["mkt"].get("bestAsk"),
                        fair_prob=cand["sim_prob"],
                        home=home, away=away, kickoff_date=kickoff_date,
                        outcome_key=cand["outcome_key"], sim_se=cand["sim_se"],
                    )
                return tid

            for g, group_cands in by_group.items():
                best = max(group_cands, key=lambda c: c["edge_pp"])
                best_live = max(
                    (c for c in group_cands
                     if _sim_live_eligible(c["outcome_key"], c["edge_pp"])),
                    key=lambda c: c["edge_pp"], default=None,
                )
                best_tid = _log_and_maybe_execute(
                    best, execute=(best_live is not None and best_live is best)
                )
                if best_tid:
                    log.info(f"    → Trade #{best_tid} logged ({g}, best of {len(group_cands)} in group)")
                if best_live is not None and best_live is not best:
                    live_tid = _log_and_maybe_execute(best_live, execute=True)
                    if live_tid:
                        log.info(
                            f"    → Trade #{live_tid} live-eligible ({g}: "
                            f"{best_live['outcome_key']} +{best_live['edge_pp']:.1f}pp) — "
                            f"group best ({best['outcome_key']}) not eligible"
                        )
                elif best_live is None:
                    log.info(
                        f"    → {g}: no live-eligible pick "
                        f"(best {best['outcome_key']} {best['edge_pp']:.1f}pp) — paper only"
                    )

        if not dry_run:
            _log_run(conn, len(events), len(by_match), n_edges, dry_run)

        # ── Resolver: settle any open trades whose markets have now closed ──
        resolved: list = []
        if not dry_run:
            try:
                resolved = _resolver.run()
                if resolved:
                    log.info(f"[resolver] Settled {len(resolved)} trade(s) after scan")
            except Exception as exc:
                log.warning(f"[resolver] Failed to run after scan: {exc}")

        summary = {
            "events_scanned": len(events),
            "non_football_skipped": n_non_football,
            "matches_in_model": len(by_match),
            "sim_edges_found": n_edges,
            "sim_trades_logged": len(trades_logged),
            "resolved_trades": len(resolved),
            "dry_run": dry_run,
        }
        log.info(
            f"Done — {n_non_football} non-football | "
            f"{len(by_match)} matches | "
            f"Sim: {n_edges} edges, {len(trades_logged)} trades"
        )
        return summary

    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Monte Carlo Sim daily scanner for Polymarket"
    )
    parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS_AHEAD, help="Days ahead to scan"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_EDGE_THRESHOLD_PP,
        help="Edge threshold in percentage points (default: 3.0)",
    )
    args = parser.parse_args()

    result = run(
        days_ahead=args.days,
        threshold_pp=args.threshold,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
