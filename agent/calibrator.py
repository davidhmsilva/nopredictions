"""
calibrator.py — apply the fitted DC probability-calibration map at scan time.

Loads agent/dc_calibration.json (built by calibrate_model.py) and exposes a
single helper that takes the raw DC 1X2 probabilities for a match and returns
the calibrated, renormalised ones. Auto-detects the segment (club vs
international) from the persisted set of international teams, so the caller
doesn't need league metadata.

Outcomes beyond 1X2 (totals/BTTS/halftime) are passed through unchanged — the
calibration is only fit for home_win / draw / away_win, where the
over-confidence was measured. Falls back to identity (no change) if the map is
missing or a segment/outcome wasn't fitted.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import numpy as np


def _norm(s: str) -> str:
    """Team-name normaliser — kept in sync with dc_scanner._norm (copied here to
    avoid a circular import, since dc_scanner imports this module)."""
    return re.sub(r"\s+", " ",
        re.sub(r"[^a-z0-9 ]", "",
        re.sub(r"\b(fc|cf|sc|ac|ss|afc|bsc|rcd|ssc|cd|rc|sl|as|ca|aa|fk|sk|rb|vfb|vfl|sv|bv|1\.)\b", "",
        s.lower()))).strip()


_CALIB_PATH = os.path.join(os.path.dirname(__file__), "dc_calibration.json")
_TRIPLE = ("home_win", "draw", "away_win")


class Calibrator:
    def __init__(self, path: str = _CALIB_PATH):
        self.ok = False
        self._maps: dict = {}
        self._intl: set[str] = set()
        try:
            with open(path) as f:
                blob = json.load(f)
            self._maps = blob.get("segments", {})
            self._intl = {_norm(t) for t in blob.get("intl_teams", [])}
            self.ok = bool(self._maps)
        except (FileNotFoundError, json.JSONDecodeError):
            self.ok = False

    def _segment(self, home: str, away: str) -> str:
        return "intl" if (_norm(home) in self._intl and _norm(away) in self._intl) else "club"

    def _apply_one(self, seg: str, outcome: str, p: float) -> Optional[float]:
        knots = self._maps.get(seg, {}).get(outcome)
        if not knots:
            return None
        return float(np.interp(p, knots["x"], knots["y"]))

    def calibrate_1x2(self, probs: dict, home: str, away: str) -> dict:
        """Return a copy of `probs` with home_win/draw/away_win recalibrated and
        renormalised to sum to 1. Non-1X2 keys are left untouched. No-op if the
        map is unavailable for this segment."""
        if not self.ok:
            return probs
        seg = self._segment(home, away)
        cal = {o: self._apply_one(seg, o, float(probs[o])) for o in _TRIPLE if o in probs}
        if any(v is None for v in cal.values()) or len(cal) != 3:
            return probs  # incomplete map for this segment → leave raw
        s = sum(cal.values())
        if s <= 0:
            return probs
        out = dict(probs)
        for o in _TRIPLE:
            out[o] = cal[o] / s
        out["_calibrated"] = True
        out["_segment"] = seg
        return out


_default: Optional[Calibrator] = None


def get() -> Calibrator:
    global _default
    if _default is None:
        _default = Calibrator()
    return _default
