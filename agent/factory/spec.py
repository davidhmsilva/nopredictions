"""
spec.py — the strategy language.

    {
      "name": "Next goal 70-79', level, pre-match over >= 0.60",
      "universe": "soccer_inplay_next_goal",
      "market": null,              # pre-match universes: "1x2" | "ou25" | "ml" | "spread" | "total"
      "side": null,                # pre-match universes: "home" | "draw" | "away" | "over" | "under"
      "where": [["minute", "between", [70, 79]],
                ["abs_diff", "==", 0],
                ["pre_over25", ">=", 0.60]],
      "price": {"odds_min": 1.5, "odds_max": 2.2, "max_spread": 0.03, "min_depth_usd": 200},
      "exit":  {"type": "hold"},   # or {"type": "cash_out", "minute": 85}
      "stake": {"type": "flat", "units": 1}
    }

Entry is the FIRST row of a fixture where every condition holds — the moment a
bot watching the tape would have fired — so a spec is a trigger, not a filter
over outcomes. `where` may only name features of the universe, and every
feature is something known at that moment (universes.py): the language has no
way to say "the final score".

Operators: == != >= <= > < between in notnull isnull.
"""
from __future__ import annotations

import hashlib
import json

from .universes import UNIVERSES

OPS = ("==", "!=", ">=", "<=", ">", "<", "between", "in", "notnull", "isnull")
PRICE_KEYS = ("odds_min", "odds_max", "max_spread", "min_depth_usd")
MAX_UNITS = 10.0


class SpecError(ValueError):
    pass


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SpecError(f"{what} must be a number, got {v!r}")
    return float(v)


def normalize(spec: dict) -> dict:
    """Validate and canonicalise; raises SpecError with a reason a person can act on."""
    if not isinstance(spec, dict):
        raise SpecError("a spec is a JSON object")
    U = UNIVERSES.get(spec.get("universe"))
    if U is None:
        raise SpecError(f"unknown universe {spec.get('universe')!r} — one of {sorted(UNIVERSES)}")
    out: dict = {"name": (str(spec.get("name") or "").strip() or None), "universe": U.name,
                 "market": spec.get("market") or None, "side": spec.get("side") or None,
                 "where": [], "price": {}, "exit": {"type": "hold"},
                 "stake": {"type": "flat", "units": 1.0}}
    if spec.get("template"):
        out["template"] = str(spec["template"])

    if U.market_sides:
        if out["market"] not in U.market_sides:
            raise SpecError(f"{U.name} needs a market: one of {list(U.market_sides)}")
        if out["side"] not in U.market_sides[out["market"]]:
            raise SpecError(f"market {out['market']} takes a side in {list(U.market_sides[out['market']])}")
    elif out["market"] or out["side"]:
        raise SpecError(f"{U.name} is a single market — no market/side")

    for clause in spec.get("where") or []:
        if not isinstance(clause, (list, tuple)) or len(clause) not in (2, 3):
            raise SpecError(f"a where clause is [feature, op, value]: {clause!r}")
        col, op = clause[0], clause[1]
        val = clause[2] if len(clause) == 3 else None
        if col not in U.features:
            raise SpecError(f"{col!r} is not a feature of {U.name} — known: {', '.join(U.features)}")
        if op not in OPS:
            raise SpecError(f"unknown operator {op!r} — one of {OPS}")
        if op == "between":
            if not isinstance(val, (list, tuple)) or len(val) != 2:
                raise SpecError(f"between takes [lo, hi]: {clause!r}")
            lo, hi = _num(val[0], "between lo"), _num(val[1], "between hi")
            if lo > hi:
                raise SpecError(f"between lo > hi: {clause!r}")
            val = [lo, hi]
        elif op == "in":
            if not isinstance(val, (list, tuple)) or not val:
                raise SpecError(f"in takes a non-empty list: {clause!r}")
            val = list(val)
        elif op in ("notnull", "isnull"):
            val = None
        elif not isinstance(val, str):
            val = _num(val, f"value of {col}")
        out["where"].append([col, op, val])

    price = spec.get("price") or {}
    for k, v in price.items():
        if k not in PRICE_KEYS:
            raise SpecError(f"unknown price key {k!r} — one of {PRICE_KEYS}")
        if v is not None:
            out["price"][k] = _num(v, k)
    if out["price"].get("odds_min", 1) < 1.0:
        raise SpecError("odds_min is decimal odds (>= 1.0)")

    ex = spec.get("exit") or {"type": "hold"}
    if ex.get("type") not in U.exits:
        raise SpecError(f"{U.name} supports exits {U.exits}, not {ex.get('type')!r}")
    if ex["type"] == "cash_out":
        out["exit"] = {"type": "cash_out", "minute": _num(ex.get("minute"), "cash_out minute")}

    st = spec.get("stake") or {"type": "flat", "units": 1.0}
    if st.get("type", "flat") != "flat":
        raise SpecError("stake: only {'type': 'flat', 'units': u} for now")
    units = _num(st.get("units", 1.0), "stake units")
    if not 0 < units <= MAX_UNITS:
        raise SpecError(f"stake units in (0, {MAX_UNITS}]")
    out["stake"] = {"type": "flat", "units": units}

    if out["name"] is None:
        out["name"] = auto_name(out)
    return out


def auto_name(spec: dict) -> str:
    bits = [spec["universe"]]
    if spec.get("market"):
        bits.append(f"{spec['market']} {spec['side']}")
    for col, op, val in spec["where"]:
        v = f"{val[0]:g}-{val[1]:g}" if op == "between" else ("" if val is None else val)
        bits.append(f"{col}{'' if op == 'between' else op}{v}")
    p = spec["price"]
    if "odds_min" in p or "odds_max" in p:
        bits.append(f"@{p.get('odds_min', 1):g}-{p.get('odds_max', 1000):g}")
    if spec["exit"]["type"] != "hold":
        bits.append(f"out@{spec['exit']['minute']:g}'")
    return " · ".join(str(b) for b in bits)


def spec_hash(spec: dict) -> str:
    """Identity of a strategy: everything except its name and template tag."""
    core = {k: v for k, v in spec.items() if k not in ("name", "template")}
    return hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16]
