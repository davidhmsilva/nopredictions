"""
Dixon-Coles trainer — loads match data from Supabase, fits the model,
evaluates calibration vs Pinnacle closing line, and saves parameters.

Usage:
  cd agent
  source ../ingest/.venv/bin/activate
  python dc_trainer.py                        # full train + eval
  python dc_trainer.py --half-life 90        # custom decay
  python dc_trainer.py --min-date 2018-01-01 # use data from this date forward
  python dc_trainer.py --eval-only           # skip fit, just evaluate saved model
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import numpy as np
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

sys.path.insert(0, os.path.dirname(__file__))
from dixon_coles import DixonColesModel

DATABASE_URL = os.getenv("DATABASE_URL")
PARAMS_PATH = os.path.join(os.path.dirname(__file__), "dc_model_params.json")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DATABASE_URL)


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def load_training_data(min_date: str = "2012-01-01") -> list[dict]:
    """
    Load all finished matches with goals + xG (where available).
    Returns list of dicts ready to pass to DixonColesModel.fit().
    """
    print(f"Loading matches from {min_date}...")
    sql = """
        SELECT
            m.id,
            m.kickoff_utc,
            m.home_score,
            m.away_score,
            ht.canonical_name AS home_team,
            at.canonical_name AS away_team,
            ms.home_xg,
            ms.away_xg
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        LEFT JOIN match_stats ms ON ms.match_id = m.id
        WHERE m.status = 'finished'
          AND m.home_score IS NOT NULL
          AND m.away_score IS NOT NULL
          AND m.kickoff_utc >= %s
        ORDER BY m.kickoff_utc
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (min_date,))
        rows = _rows(cur)

    matches = []
    n_xg = 0
    for r in rows:
        has_xg = r["home_xg"] is not None and r["away_xg"] is not None

        if has_xg:
            # Use xG rounded to nearest integer as the training signal
            hg = int(round(float(r["home_xg"])))
            ag = int(round(float(r["away_xg"])))
            n_xg += 1
        else:
            hg = int(r["home_score"])
            ag = int(r["away_score"])

        ko = r["kickoff_utc"]
        if isinstance(ko, str):
            ko = datetime.fromisoformat(ko)
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)

        matches.append({
            "home_team": r["home_team"],
            "away_team": r["away_team"],
            "home_goals": hg,
            "away_goals": ag,
            "kickoff_utc": ko,
            "has_xg": has_xg,
        })

    print(f"  {len(matches):,} matches loaded  ({n_xg:,} with xG)")
    return matches


def load_pinnacle_closing(min_date: str = "2020-01-01", limit: int = 15_000) -> list[dict]:
    """
    Load Pinnacle closing 1X2 odds for validation.
    Returns list of dicts with home_prob, draw_prob, away_prob, result.
    """
    print(f"Loading Pinnacle closing odds for validation (from {min_date})...")
    sql = """
        SELECT
            ht.canonical_name AS home_team,
            at.canonical_name AS away_team,
            mo.home_odds,
            mo.draw_odds,
            mo.away_odds,
            m.home_score,
            m.away_score,
            m.kickoff_utc
        FROM match_odds mo
        JOIN matches m ON m.id = mo.match_id
        JOIN bookmakers b ON b.id = mo.bookmaker_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE b.code = 'PSC'
          AND mo.home_odds IS NOT NULL
          AND mo.draw_odds IS NOT NULL
          AND mo.away_odds IS NOT NULL
          AND m.status = 'finished'
          AND m.home_score IS NOT NULL
          AND m.kickoff_utc >= %s
        ORDER BY m.kickoff_utc DESC
        LIMIT %s
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (min_date, limit))
        rows = _rows(cur)

    print(f"  {len(rows):,} Pinnacle closing records loaded")
    return rows


# ── Calibration metrics ───────────────────────────────────────────────────────

def vig_remove(h: float, d: float, a: float) -> tuple[float, float, float]:
    """Remove bookmaker margin from 3-way decimal odds."""
    ih, id_, ia = 1 / h, 1 / d, 1 / a
    total = ih + id_ + ia
    return ih / total, id_ / total, ia / total


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean squared error between predicted probs and one-hot outcomes."""
    return float(np.mean((probs - outcomes) ** 2))


def log_loss(probs: np.ndarray, outcomes: np.ndarray, eps: float = 1e-9) -> float:
    return float(-np.mean(np.log(np.clip(probs, eps, 1)) * outcomes))


def evaluate(model: DixonColesModel, validation: list[dict]) -> dict:
    """
    Compare model predictions to Pinnacle closing probabilities.

    Metrics:
      - Brier score (model vs Pinnacle — lower = closer to sharp line)
      - Log loss
      - Mean edge vs Pinnacle (how much the model disagrees on average)
      - Calibration: actual win rate vs predicted win rate by bucket
    """
    model_probs, pinnacle_probs, outcomes = [], [], []
    skipped = 0

    for r in validation:
        pred = model.predict_or_none(r["home_team"], r["away_team"])
        if pred is None:
            skipped += 1
            continue

        ph, pd, pa = vig_remove(
            float(r["home_odds"]), float(r["draw_odds"]), float(r["away_odds"])
        )

        hs, as_ = int(r["home_score"]), int(r["away_score"])
        if hs > as_:
            result = [1, 0, 0]
        elif hs == as_:
            result = [0, 1, 0]
        else:
            result = [0, 0, 1]

        model_probs.append([pred["home_win"], pred["draw"], pred["away_win"]])
        pinnacle_probs.append([ph, pd, pa])
        outcomes.append(result)

    if not model_probs:
        return {"error": "No predictions made"}

    mp = np.array(model_probs)
    pp = np.array(pinnacle_probs)
    oc = np.array(outcomes, dtype=float)

    # Edge: model vs Pinnacle (positive = model thinks more likely than sharp)
    edge = mp - pp
    mean_abs_edge = float(np.abs(edge).mean() * 100)
    mean_edge = float(edge.mean() * 100)

    return {
        "n_evaluated": len(model_probs),
        "n_skipped": skipped,
        "brier_model": round(brier_score(mp, oc), 5),
        "brier_pinnacle": round(brier_score(pp, oc), 5),
        "log_loss_model": round(log_loss(mp, oc), 5),
        "log_loss_pinnacle": round(log_loss(pp, oc), 5),
        "mean_abs_edge_vs_sharp_pp": round(mean_abs_edge, 2),
        "mean_edge_vs_sharp_pp": round(mean_edge, 2),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train Dixon-Coles model")
    parser.add_argument("--half-life", type=float, default=90.0,
                        help="Time decay half-life in days (default: 90)")
    parser.add_argument("--xg-multiplier", type=float, default=1.5,
                        help="Weight multiplier for xG matches (default: 1.5)")
    parser.add_argument("--min-date", default="2012-01-01",
                        help="Earliest training match date (default: 2012-01-01)")
    parser.add_argument("--eval-only", action="store_true",
                        help="Skip training, just evaluate saved model")
    parser.add_argument("--output", default=PARAMS_PATH,
                        help=f"Output path for model params (default: {PARAMS_PATH})")
    args = parser.parse_args()

    if args.eval_only:
        print(f"Loading saved model from {args.output}...")
        model = DixonColesModel.load(args.output)
        print(f"  Fitted on {model.n_matches:,} matches  ({model.fit_date})")
    else:
        matches = load_training_data(min_date=args.min_date)

        model = DixonColesModel(
            half_life_days=args.half_life,
            xg_multiplier=args.xg_multiplier,
        )

        print(f"\nFitting Dixon-Coles model ({len(model.teams) if model.fitted else '?'} teams)...")
        print(f"  half_life={args.half_life}d  xg_multiplier={args.xg_multiplier}x")
        model.fit(matches)

        n_teams = len(model.teams)
        print(f"\nFit complete:")
        print(f"  Teams: {n_teams}")
        print(f"  Matches: {model.n_matches:,}")
        print(f"  Home advantage: {model.home_adv:.4f}  (×{round(float(np.exp(model.home_adv)), 3)} goals)")
        print(f"  Rho (DC correction): {model.rho:.4f}")
        print(f"  Weighted log-loss: {model.log_loss:.5f}")

        # Top 10 strongest teams
        ratings = model.team_ratings()[:10]
        print("\nTop 10 teams by strength:")
        for r in ratings:
            print(f"  {r['team']:<30}  atk={r['attack']:+.3f}  dfn={r['defense']:+.3f}  str={r['strength']:+.3f}")

        model.save(args.output)
        print(f"\nModel saved → {args.output}")

    # Evaluate vs Pinnacle closing
    print("\nEvaluating vs Pinnacle closing line...")
    validation = load_pinnacle_closing()
    if validation:
        metrics = evaluate(model, validation)
        print(f"\nCalibration vs Pinnacle closing ({metrics.get('n_evaluated', 0):,} matches):")
        if "error" in metrics:
            print(f"  Error: {metrics['error']}")
        else:
            print(f"  Brier (model):     {metrics['brier_model']:.5f}")
            print(f"  Brier (Pinnacle):  {metrics['brier_pinnacle']:.5f}  ← target")
            print(f"  Log-loss (model):  {metrics['log_loss_model']:.5f}")
            print(f"  Log-loss (Pinnacle): {metrics['log_loss_pinnacle']:.5f}  ← target")
            print(f"  Mean |edge| vs sharp: {metrics['mean_abs_edge_vs_sharp_pp']:.2f}pp")
            gap_brier = metrics["brier_model"] - metrics["brier_pinnacle"]
            gap_ll = metrics["log_loss_model"] - metrics["log_loss_pinnacle"]
            print(f"\n  Gap vs Pinnacle (lower = closer to sharp):")
            print(f"    Brier: +{gap_brier:.5f}")
            print(f"    LogL:  +{gap_ll:.5f}")
            skipped = metrics.get("n_skipped", 0)
            if skipped:
                print(f"  ({skipped} matches skipped — teams not in model)")

    print("\nDone.")


if __name__ == "__main__":
    main()
