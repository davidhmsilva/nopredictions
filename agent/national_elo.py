#!/usr/bin/env python3
"""
National-team Elo ratings for the World Cup agent.

A self-contained World-Football-Elo built from Stage G international results
(INT-* leagues). No sharp-book reference anywhere — this is our own
*alternative* line for national teams, because the Dixon-Coles model shrinks
small nations toward the mean and prices internationals poorly.

Pipeline:
  1. Walk every international match in time order, updating Elo with a
     tournament-importance K and a goal-difference multiplier.
  2. Home advantage applies only to genuine home/away fixtures (qualifiers,
     Nations League, friendlies). Finals tournaments (WC / Euro / Copa /
     AFCON / Asian Cup) are treated as neutral venues.
  3. Fit the Elo->goals mapping (beta, baseline totals) on the same history,
     so wc_pricer can turn a rating gap into Poisson lambdas for the sim.

Output: agent/national_elo_ratings.json

CLI:
  cd agent && source ../ingest/.venv/bin/activate
  python national_elo.py --build          # build + save ratings json
  python national_elo.py --top 30         # print current top nations
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make agent/ importable when run directly (so `tools.db` resolves).
sys.path.insert(0, str(Path(__file__).parent))

RATINGS_PATH = Path(__file__).parent / "national_elo_ratings.json"

DEFAULT_RATING = 1500.0
DEFAULT_HOME_ADV = 65.0     # Elo points added to a genuine (non-neutral) home side
DEFAULT_K = 30.0            # fallback importance for unknown competitions

# Tournament importance (K) and neutrality by our INT-* league codes.
# Finals tournaments are at neutral venues; qualifiers / Nations League /
# friendlies are real home-and-away fixtures.
COMPETITIONS: Dict[str, Dict[str, Any]] = {
    "INT-WC":       {"k": 60, "neutral": True,  "finals": True},
    "INT-WCQ":      {"k": 40, "neutral": False, "finals": False},
    "INT-EURO":     {"k": 50, "neutral": True,  "finals": True},
    "INT-EUROQ":    {"k": 40, "neutral": False, "finals": False},
    "INT-UNL":      {"k": 40, "neutral": False, "finals": False},
    "INT-COPA":     {"k": 50, "neutral": True,  "finals": True},
    "INT-CONCACAF": {"k": 45, "neutral": True,  "finals": True},
    "INT-AFCON":    {"k": 50, "neutral": True,  "finals": True},
    "INT-AFCONQ":   {"k": 40, "neutral": False, "finals": False},
    "INT-AFC":      {"k": 50, "neutral": True,  "finals": True},
    "INT-AFCQ":     {"k": 40, "neutral": False, "finals": False},
    "INT-FR":       {"k": 20, "neutral": False, "finals": False},
}


def k_for(code: str) -> float:
    return float(COMPETITIONS.get(code, {}).get("k", DEFAULT_K))


def is_neutral(code: str) -> bool:
    return bool(COMPETITIONS.get(code, {}).get("neutral", True))


def is_finals(code: str) -> bool:
    return bool(COMPETITIONS.get(code, {}).get("finals", False))


def expected_home(elo_diff_eff: float) -> float:
    """Logistic expected score for the home side given the effective Elo gap."""
    return 1.0 / (1.0 + 10.0 ** (-elo_diff_eff / 400.0))


def goal_mult(goal_diff: int) -> float:
    """World-Football-Elo goal-difference multiplier."""
    n = abs(int(goal_diff))
    if n <= 1:
        return 1.0
    if n == 2:
        return 1.5
    return (11.0 + n) / 8.0


def match_update(
    ratings: Dict[str, float],
    games: Dict[str, int],
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    code: str,
    home_adv: float = DEFAULT_HOME_ADV,
) -> Dict[str, Any]:
    """
    Apply one Elo update in place and return the PRE-update prediction context
    (so a walk-forward backtest can score the match before the rating moves).
    """
    rh = ratings.get(home, DEFAULT_RATING)
    ra = ratings.get(away, DEFAULT_RATING)
    pre_games_min = min(games.get(home, 0), games.get(away, 0))

    adv = 0.0 if is_neutral(code) else home_adv
    diff_eff = (rh + adv) - ra
    exp_h = expected_home(diff_eff)

    if home_score > away_score:
        w_h = 1.0
    elif home_score < away_score:
        w_h = 0.0
    else:
        w_h = 0.5

    k = k_for(code) * goal_mult(home_score - away_score)
    delta = k * (w_h - exp_h)
    ratings[home] = rh + delta
    ratings[away] = ra - delta
    games[home] = games.get(home, 0) + 1
    games[away] = games.get(away, 0) + 1

    return {
        "home": home,
        "away": away,
        "code": code,
        "diff_eff": diff_eff,
        "exp_home": exp_h,
        "goal_diff": home_score - away_score,
        "total": home_score + away_score,
        "neutral": is_neutral(code),
        "finals": is_finals(code),
        "pre_games_min": pre_games_min,
    }


def load_international_matches() -> List[dict]:
    """All settled INT-* matches in time order, with canonical team names."""
    from tools.db import run_analysis_query
    return run_analysis_query(
        """
        SELECT m.kickoff_utc, l.code AS league_code,
               th.canonical_name AS home, ta.canonical_name AS away,
               m.home_score AS home_score, m.away_score AS away_score
        FROM matches m
        JOIN seasons s ON s.id = m.season_id
        JOIN leagues l ON l.id = s.league_id
        JOIN teams   th ON th.id = m.home_team_id
        JOIN teams   ta ON ta.id = m.away_team_id
        WHERE l.code LIKE 'INT-%'
          AND m.home_score IS NOT NULL
          AND m.away_score IS NOT NULL
        ORDER BY m.kickoff_utc ASC, m.id ASC
        """
    )


def build(
    matches: List[dict],
    home_adv: float = DEFAULT_HOME_ADV,
    min_games_for_calib: int = 3,
) -> Tuple[Dict[str, float], Dict[str, int], Dict[str, Any], List[dict]]:
    """Walk-forward build. Returns (ratings, games, last_played, calib_samples)."""
    ratings: Dict[str, float] = {}
    games: Dict[str, int] = {}
    last_played: Dict[str, Any] = {}
    samples: List[dict] = []

    for m in matches:
        ctx = match_update(
            ratings, games,
            m["home"], m["away"],
            int(m["home_score"]), int(m["away_score"]),
            m["league_code"], home_adv=home_adv,
        )
        ko = m["kickoff_utc"]
        last_played[m["home"]] = ko
        last_played[m["away"]] = ko
        # Only keep well-anchored matches for the goals calibration.
        if ctx["pre_games_min"] >= min_games_for_calib:
            samples.append(ctx)

    return ratings, games, last_played, samples


def fit_calibration(samples: List[dict]) -> Dict[str, Any]:
    """
    Fit the Elo->goals mapping used by wc_pricer:
      supremacy (home_goals - away_goals) ~ beta * effective_elo_diff
      baseline total goals = historical mean (finals subset tracked separately).
    """
    sxx = sum(s["diff_eff"] ** 2 for s in samples)
    sxy = sum(s["diff_eff"] * s["goal_diff"] for s in samples)
    beta = (sxy / sxx) if sxx > 0 else 0.0

    totals = [s["total"] for s in samples]
    finals_totals = [s["total"] for s in samples if s["finals"]]
    mu_total = (sum(totals) / len(totals)) if totals else 2.6
    mu_total_finals = (
        sum(finals_totals) / len(finals_totals) if finals_totals else mu_total
    )

    return {
        "beta_goals_per_elo": beta,
        "mu_total": mu_total,
        "mu_total_finals": mu_total_finals,
        "n_samples": len(samples),
        "n_finals": len(finals_totals),
    }


def _iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def save_ratings(
    path: Path,
    ratings: Dict[str, float],
    games: Dict[str, int],
    last_played: Dict[str, Any],
    calibration: Dict[str, Any],
    home_adv: float,
    n_matches: int,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_matches": n_matches,
        "n_teams": len(ratings),
        "params": {
            "home_adv": home_adv,
            "default_rating": DEFAULT_RATING,
            "competitions": COMPETITIONS,
        },
        "calibration": calibration,
        "ratings": {
            team: {
                "rating": round(ratings[team], 2),
                "games": games.get(team, 0),
                "last_played": _iso(last_played.get(team)),
            }
            for team in ratings
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def load_ratings(path: Path = RATINGS_PATH) -> dict:
    return json.loads(Path(path).read_text())


def _print_top(ratings: Dict[str, float], games: Dict[str, int], n: int) -> None:
    ranked = sorted(ratings.items(), key=lambda kv: -kv[1])
    # Only show teams with a meaningful sample.
    ranked = [(t, r) for t, r in ranked if games.get(t, 0) >= 10][:n]
    print(f"\n  {'#':>3}  {'Nation':<28}{'Elo':>8}{'games':>8}")
    print("  " + "-" * 50)
    for i, (team, r) in enumerate(ranked, 1):
        print(f"  {i:>3}  {team:<28}{r:>8.0f}{games.get(team, 0):>8}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="National-team Elo builder")
    ap.add_argument("--build", action="store_true", help="build + save ratings json")
    ap.add_argument("--top", type=int, default=0, help="print top-N nations and exit")
    ap.add_argument("--home-adv", type=float, default=DEFAULT_HOME_ADV)
    args = ap.parse_args()

    # `--top` with an existing file just prints; otherwise we (re)build.
    if args.top and RATINGS_PATH.exists() and not args.build:
        data = load_ratings()
        ratings = {t: v["rating"] for t, v in data["ratings"].items()}
        games = {t: v["games"] for t, v in data["ratings"].items()}
        _print_top(ratings, games, args.top)
        return 0

    print("Loading international matches from DB ...")
    matches = load_international_matches()
    print(f"  {len(matches)} settled international matches")
    if not matches:
        print("  no matches — is Stage G ingested?")
        return 1

    ratings, games, last_played, samples = build(matches, home_adv=args.home_adv)
    calib = fit_calibration(samples)

    save_ratings(RATINGS_PATH, ratings, games, last_played,
                 calib, args.home_adv, len(matches))
    print(f"  saved {len(ratings)} nations -> {RATINGS_PATH.name}")
    print(f"  calibration: beta={calib['beta_goals_per_elo']:.5f} goals/Elo, "
          f"mu_total={calib['mu_total']:.3f}, "
          f"mu_total_finals={calib['mu_total_finals']:.3f} "
          f"(n={calib['n_samples']}, finals={calib['n_finals']})")

    _print_top(ratings, games, args.top or 20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
