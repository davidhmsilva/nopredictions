"""
edge_engine.py — one place to turn (model, sharp, market price) into a *refined*,
confidence-aware edge. Replaces the naive `model_prob - pm_price` duplicated across
the scanners.

Philosophy (chosen 2026-05-28): SHARP-ANCHORED + UNCERTAINTY.
  * Where a sharp line (Pinnacle, de-vigged) exists, it anchors and validates: the
    edge is the CONSENSUS of model and sharp via min(model, sharp) - price. If the
    sharp disagrees (sharp ≈ price), the consensus collapses and the edge vanishes
    on its own — no special-casing. This is the original project thesis: sharps are
    the fair-value oracle; trade where PM diverges from them.
  * Where no sharp exists (minor leagues, in-play — where the inefficiency actually
    lives), fall back to the model alone, but with a higher threshold and a
    model-only haircut, so unvalidated edges must be bigger to qualify.

Every edge is then docked an UNCERTAINTY haircut: MC standard error + a penalty at
extreme (longshot) prices where the model is least calibrated.

The engine is side-agnostic: pass the probability of *the side you are buying* and
its executable price. For a BUY-NO, the caller passes the NO prob and NO ask.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Optional

# Thresholds on the *adjusted* edge (after haircut), in percentage points.
SHARP_THRESHOLD_PP = float(os.environ.get("EDGE_SHARP_THRESHOLD_PP", "3.0"))
MODEL_THRESHOLD_PP = float(os.environ.get("EDGE_MODEL_THRESHOLD_PP", "5.0"))

# Haircuts (pp) docked from the raw edge.
MODEL_ONLY_HAIRCUT_PP = float(os.environ.get("EDGE_MODEL_ONLY_HAIRCUT_PP", "1.5"))
SE_HAIRCUT_MULT = float(os.environ.get("EDGE_SE_HAIRCUT_MULT", "1.0"))   # × MC std error
EXTREME_HAIRCUT_PP = float(os.environ.get("EDGE_EXTREME_HAIRCUT_PP", "2.0"))
EXTREME_LO = float(os.environ.get("EDGE_EXTREME_LO", "0.10"))
EXTREME_HI = float(os.environ.get("EDGE_EXTREME_HI", "0.90"))

# A model-only edge bigger than this almost never reflects a real, liquid-market
# mispricing — it's model miscalibration (e.g. Andorra 54% vs PM 8%). With no
# sharp line to validate, we refuse to trust it. (Sharp-validated edges are exempt:
# the sharp already confirmed them.) Until per-segment calibration (Layer B) exists,
# this is our guard against model delusion in unvalidated markets.
IMPLAUSIBLE_MODEL_EDGE_PP = float(os.environ.get("EDGE_IMPLAUSIBLE_MODEL_PP", "20.0"))


@dataclass
class EdgeResult:
    bet_ok: bool
    edge_pp: float          # adjusted edge (what the decision uses)
    edge_raw_pp: float      # fair - price, before haircut
    fair_value: float       # consensus / model fair prob
    exec_price: float       # price we measure against (ask if given)
    haircut_pp: float
    threshold_pp: float
    confidence: str         # 'sharp' | 'model'
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def compute_edge(
    *,
    model_prob: float,
    exec_price: float,
    sharp_prob: Optional[float] = None,
    sim_se: Optional[float] = None,
) -> EdgeResult:
    """
    model_prob  our fair prob for the side being bought
    exec_price  the price we'd actually pay (ask), as a probability 0..1
    sharp_prob  de-vigged sharp prob for this side, or None if unavailable
    sim_se      MC standard error of model_prob (fraction), or None
    """
    # Uncertainty haircut (applies in both branches).
    se_hc = SE_HAIRCUT_MULT * (sim_se * 100.0) if sim_se else 0.0
    extreme_hc = EXTREME_HAIRCUT_PP if (exec_price < EXTREME_LO or exec_price > EXTREME_HI) else 0.0

    if sharp_prob is not None:
        # Consensus: only the edge both sources agree on survives. If the sharp
        # says the price is fair (sharp ≈ price), min collapses the edge to ~0.
        fair = min(model_prob, sharp_prob)
        confidence = "sharp"
        threshold = SHARP_THRESHOLD_PP
        haircut = se_hc + extreme_hc
        src = f"sharp {sharp_prob:.3f} / model {model_prob:.3f} → consensus {fair:.3f}"
    else:
        fair = model_prob
        confidence = "model"
        threshold = MODEL_THRESHOLD_PP
        haircut = MODEL_ONLY_HAIRCUT_PP + se_hc + extreme_hc
        src = f"model {model_prob:.3f} (no sharp)"

    edge_raw = (fair - exec_price) * 100.0
    edge_adj = round(edge_raw - haircut, 2)
    bet_ok = edge_adj >= threshold

    # Model-only delusion guard: refuse implausibly large unvalidated edges.
    implausible = (confidence == "model" and edge_raw > IMPLAUSIBLE_MODEL_EDGE_PP)
    if implausible:
        bet_ok = False

    if implausible:
        reason = (f"{src}; raw {edge_raw:+.1f}pp > {IMPLAUSIBLE_MODEL_EDGE_PP:.0f}pp "
                  f"implausibility cap, no sharp to validate → skip (likely miscalibration)")
    else:
        reason = (f"{src}; raw {edge_raw:+.1f}pp − haircut {haircut:.1f}pp "
                  f"= {edge_adj:+.1f}pp vs {threshold:.1f}pp [{confidence}] "
                  f"→ {'BET' if bet_ok else 'skip'}")

    return EdgeResult(
        bet_ok=bet_ok,
        edge_pp=edge_adj,
        edge_raw_pp=round(edge_raw, 2),
        fair_value=round(fair, 6),
        exec_price=round(exec_price, 6),
        haircut_pp=round(haircut, 2),
        threshold_pp=threshold,
        confidence=confidence,
        reason=reason,
    )
