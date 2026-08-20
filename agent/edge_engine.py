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

...and, since 2026-07-22, the POLYMARKET TAKER FEE. We buy at the ask, so we are
always the taker, and takers pay. Ignoring it meant every threshold in this file
was quietly ~1pp too generous at mid prices.

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

# --- Polymarket taker fee -------------------------------------------------
# fee_usdc = shares * FEE_RATE * p * (1 - p), charged on top of notional for a BUY.
# Verified 2026-07-22 against 91,707 real fills from wallet 0x204f...5e14: the
# implied rate is exactly 0.050000 on sports markets (maker fills pay nothing).
# The p*(1-p) shape means the fee peaks at 1.25pp at p=0.50 and collapses toward
# the extremes — 0.19pp at p=0.96.
FEE_RATE = float(os.environ.get("EDGE_FEE_RATE", "0.05"))

# Fraction of the taker fee handed back by the tiered Taker Rebate Program.
# Default 0: we are in no tier and must not assume one. (For reference, the wallet
# above recovered ~50% — but it does ~$7M/day. Only raise this from measured data.)
FEE_REBATE_FRAC = float(os.environ.get("EDGE_FEE_REBATE_FRAC", "0.0"))


def taker_fee_pp(price: float, *, rate: float = None, rebate_frac: float = None) -> float:
    """Polymarket taker fee for buying `price`, expressed in percentage points of
    probability — directly subtractable from an edge measured in pp.

    Because the fee is charged in USDC on top of the notional, the effective price
    paid is p + rate*p*(1-p), so the fee in pp is exactly 100*rate*p*(1-p).
    """
    r = FEE_RATE if rate is None else rate
    rb = FEE_REBATE_FRAC if rebate_frac is None else rebate_frac
    p = min(max(price, 0.0), 1.0)
    return 100.0 * r * p * (1.0 - p) * (1.0 - rb)


@dataclass
class EdgeResult:
    bet_ok: bool
    edge_pp: float          # adjusted edge, NET OF FEE (what the decision uses)
    edge_raw_pp: float      # fair - price, before haircut and fee
    fair_value: float       # consensus / model fair prob
    exec_price: float       # price we measure against (ask if given)
    haircut_pp: float       # uncertainty haircut only
    fee_pp: float           # Polymarket taker fee, net of any rebate
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
    is_taker: bool = True,
    fee_rate: Optional[float] = None,
) -> EdgeResult:
    """
    model_prob  our fair prob for the side being bought
    exec_price  the price we'd actually pay (ask), as a probability 0..1
    sharp_prob  de-vigged sharp prob for this side, or None if unavailable
    sim_se      MC standard error of model_prob (fraction), or None
    is_taker    True (default) if we cross the spread — we always do, and only
                takers pay. Pass False only for a resting maker order.
    fee_rate    override the category fee rate (default: sports, 0.05)
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

    fee = taker_fee_pp(exec_price, rate=fee_rate) if is_taker else 0.0

    edge_raw = (fair - exec_price) * 100.0
    edge_adj = round(edge_raw - haircut - fee, 2)
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
                  f"− fee {fee:.2f}pp = {edge_adj:+.1f}pp vs {threshold:.1f}pp "
                  f"[{confidence}] → {'BET' if bet_ok else 'skip'}")

    return EdgeResult(
        bet_ok=bet_ok,
        edge_pp=edge_adj,
        edge_raw_pp=round(edge_raw, 2),
        fair_value=round(fair, 6),
        exec_price=round(exec_price, 6),
        haircut_pp=round(haircut, 2),
        fee_pp=round(fee, 3),
        threshold_pp=threshold,
        confidence=confidence,
        reason=reason,
    )
