#!/usr/bin/env python3
"""
World Cup structural / sentiment priors.

Adjustments layered on top of the Elo line in wc_pricer. Each prior is
calibrated on Stage G past *finals* tournaments (WC18/22, EURO16/20/24,
COPA19/21/24, AFCON, AFC) and gated by sample-sufficiency: we only apply a
tilt if the calibration set has n>=MIN_N and the Wilson 95% CI excludes the
neutral value.

Signals
-------
1. draw_bias       — finals matches draw more often than the prior says when
                     the two teams are evenly matched (close to 50/50). We
                     boost p(draw) toward the observed empirical when the
                     model trio is near-uniform.
2. ko_unders       — knockout matches go under 2.5 more often than groups
                     (cagey football, extra-time avoidance). We don't have
                     group/KO labels on every row, so we approximate from the
                     finals-pool over-rate: if it's clearly below 0.5, every
                     pick gets a small under-tilt.
3. public_fade     — Polymarket overprices popular nations (USA on a US-
                     skewed platform, BRA / ARG / ENG / FRA / GER / ESP /
                     POR). When such a team is the YES side and PM > model,
                     down-weight model fair for that selection — pushes the
                     selector toward fading the public when the price is
                     already stretched.
4. host_bonus      — small Elo bump for USA / MEX / CAN at WC 2026. Applied
                     upstream in wc_pricer via `host=` if the agent knows
                     where the match is played; not a structural adjustment
                     here.

Each public function returns a dict of multiplicative tilts in the same key
space as wc_pricer.price() (home_win / draw / away_win / over_N_5 / under_N_5).
Tilts default to 1.0 (no-op) and are renormalised per market group by the
caller.

CLI:
  cd agent && source ../ingest/.venv/bin/activate
  python wc_structural.py --calibrate
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

import national_elo as ne  # noqa: E402

CALIB_PATH = Path(__file__).parent / "wc_structural_calib.json"

# Finals-only competition codes (no qualifiers / friendlies — those are not
# tournament dynamics).
FINALS_CODES = {"INT-WC", "INT-EURO", "INT-COPA", "INT-CONCACAF", "INT-AFCON", "INT-AFC"}

# Public-fade list. PM is US-skewed and the well-known footballing nations
# trade richer than their model fair. Lower-case match against canonical name.
POPULAR_NATIONS = {
    "united states", "brazil", "argentina", "england", "france", "germany",
    "spain", "portugal", "italy", "netherlands", "mexico",
}

MIN_N = 200          # sample-sufficiency gate (matches the project rule)
WILSON_Z = 1.96

# Soft tilt magnitudes — large enough to nudge the selector, small enough not
# to dominate the Elo line.
MAX_DRAW_BOOST = 0.06    # add up to +6pp to p(draw) when teams are evenly matched
MAX_UNDER_TILT = 0.05    # multiplicative boost to under_2_5 up to +5pp absolute
PUBLIC_FADE_PP = 0.04    # subtract up to 4pp from model fair for popular YES sides


# ── helpers ──────────────────────────────────────────────────────────────────

def _wilson(k: int, n: int, z: float = WILSON_Z) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (centre - half, centre + half)


# ── calibration on Stage G ───────────────────────────────────────────────────

def calibrate() -> Dict[str, Any]:
    """Walk past finals tournaments, compute base rates + the close-match draw
    rate. We DON'T have group/KO labels per match, so the under-2.5 rate is
    pooled across both — that's the limitation."""
    matches = ne.load_international_matches()
    n_total = n_draw = n_over25 = 0
    # close-match draw rate: rated within ±50 Elo at kickoff (proxy for "even")
    ratings: Dict[str, float] = {}
    games: Dict[str, int] = {}
    n_close = n_close_draw = 0

    for m in matches:
        code = m["league_code"]
        hs, as_ = int(m["home_score"]), int(m["away_score"])

        if code in FINALS_CODES:
            n_total += 1
            if hs == as_:
                n_draw += 1
            if hs + as_ > 2:
                n_over25 += 1

            rh = ratings.get(m["home"], ne.DEFAULT_RATING)
            ra = ratings.get(m["away"], ne.DEFAULT_RATING)
            if (games.get(m["home"], 0) >= 8 and games.get(m["away"], 0) >= 8
                    and abs(rh - ra) <= 50):
                n_close += 1
                if hs == as_:
                    n_close_draw += 1

        ne.match_update(ratings, games, m["home"], m["away"], hs, as_, code)

    draw_lo, draw_hi = _wilson(n_draw, n_total)
    over_lo, over_hi = _wilson(n_over25, n_total)
    close_lo, close_hi = _wilson(n_close_draw, n_close)

    return {
        "n_finals": n_total,
        "draw_rate":      n_draw / max(n_total, 1),
        "draw_ci":        [draw_lo, draw_hi],
        "over25_rate":    n_over25 / max(n_total, 1),
        "over25_ci":      [over_lo, over_hi],
        "n_close":        n_close,
        "close_draw_rate": n_close_draw / max(n_close, 1),
        "close_draw_ci":   [close_lo, close_hi],
        "min_n":          MIN_N,
    }


def save_calibration(calib: Dict[str, Any], path: Path = CALIB_PATH) -> None:
    path.write_text(json.dumps(calib, indent=2))


def load_calibration(path: Path = CALIB_PATH) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ── adjustments ──────────────────────────────────────────────────────────────

@dataclass
class StructuralContext:
    """All the inputs the selector hands the priors."""
    home: str
    away: str
    rating_home: float
    rating_away: float
    stage: str = "group"     # "group" or "knockout" — agent decides from PM tag
    is_knockout: bool = False


def adjustments(
    fair: Dict[str, float],
    ctx: StructuralContext,
    calib: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """Return additive shifts (in probability units) to apply on top of `fair`.

    Each key in the returned dict is added to the corresponding outcome in
    `fair`; the caller is responsible for renormalising 1X2 / O-U pairs so
    they sum to 1.
    """
    calib = calib or load_calibration() or {}
    shifts: Dict[str, float] = {k: 0.0 for k in fair}

    # ── 1) Draw bias on close matches ────────────────────────────────────
    n_close = int(calib.get("n_close", 0) or 0)
    close_rate = float(calib.get("close_draw_rate", 0.0) or 0.0)
    ci = calib.get("close_draw_ci") or [0, 1]
    if n_close >= MIN_N and ci[0] > 1 / 3.0:
        # Only fire when the matchup is genuinely close (rating gap < 75) AND
        # the model already prices the draw near a 3-way split.
        gap = abs(ctx.rating_home - ctx.rating_away)
        if gap < 75 and 0.25 <= fair.get("draw", 0) <= 0.40:
            target = min(close_rate, fair.get("draw", 0) + MAX_DRAW_BOOST)
            delta = max(target - fair.get("draw", 0), 0.0)
            shifts["draw"] = delta
            shifts["home_win"] = -delta / 2
            shifts["away_win"] = -delta / 2

    # ── 2) Knockout unders tilt ──────────────────────────────────────────
    n_finals = int(calib.get("n_finals", 0) or 0)
    over_rate = float(calib.get("over25_rate", 0.5) or 0.5)
    over_ci = calib.get("over25_ci") or [0, 1]
    if n_finals >= MIN_N and over_ci[1] < 0.5 and ctx.is_knockout:
        # Finals pool under-rate is below 50% with CI excluding 0.5 — knockouts
        # are usually even cagier, so tilt under_2_5 up a touch.
        for ou_key in ("over_2_5", "under_2_5", "over_1_5", "under_1_5",
                       "over_3_5", "under_3_5"):
            if ou_key not in fair:
                continue
        cur = fair.get("over_2_5")
        if cur is not None:
            target = max(over_rate - 0.02, cur - MAX_UNDER_TILT)  # push toward fewer goals
            shifts["over_2_5"] = target - cur
            shifts["under_2_5"] = -(target - cur)

    return shifts


def apply_shifts(fair: Dict[str, Any],
                 shifts: Dict[str, float]) -> Dict[str, Any]:
    """Apply additive shifts and renormalise 1X2 + O/U pairs.

    Non-numeric values in `fair` (list/dict diagnostic keys like top_scores,
    htft) are passed through untouched.
    """
    out: Dict[str, Any] = {}
    for k, v in fair.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = max(min(float(v) + shifts.get(k, 0.0), 0.999), 0.001)
        else:
            out[k] = v

    # Renormalise 1X2 trio.
    trio = ("home_win", "draw", "away_win")
    if all(k in out for k in trio):
        s = sum(out[k] for k in trio) or 1.0
        for k in trio:
            out[k] /= s

    # Renormalise each O/U pair to 1.
    for line in ("0_5", "1_5", "2_5", "3_5", "4_5", "5_5"):
        a, b = f"over_{line}", f"under_{line}"
        if a in out and b in out:
            s = out[a] + out[b] or 1.0
            out[a] /= s
            out[b] /= s
    return out


def public_fade_pp(outcome_key: str, side_team: Optional[str],
                   model_prob: float, pm_yes: float) -> float:
    """Return a per-trade fair-value penalty (in probability units, >=0).

    Reduces our effective model fair when YES is a popular nation AND PM has
    already bid the price up. Caller subtracts this from edge before scoring.
    """
    if side_team is None:
        return 0.0
    if outcome_key not in ("home_win", "away_win"):
        return 0.0
    if side_team.lower() not in POPULAR_NATIONS:
        return 0.0
    if pm_yes <= model_prob:
        return 0.0
    over_pp = (pm_yes - model_prob)
    return min(over_pp, PUBLIC_FADE_PP)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="WC structural priors")
    ap.add_argument("--calibrate", action="store_true",
                    help="recompute base rates from Stage G + save json")
    args = ap.parse_args()

    if args.calibrate:
        c = calibrate()
        save_calibration(c)
        print(f"Calibrated on n={c['n_finals']} finals matches.")
        print(json.dumps(c, indent=2))
        if c["n_finals"] < MIN_N:
            print(f"\n  WARNING: n < {MIN_N} — priors will not fire.")
        return 0

    c = load_calibration()
    if c is None:
        print("No calibration on disk — run: python wc_structural.py --calibrate")
        return 1
    print(json.dumps(c, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
