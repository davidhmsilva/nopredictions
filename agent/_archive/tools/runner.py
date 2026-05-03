"""
Safe Python execution sandbox for the backtester.

The agent generates Python backtest code as a string.
This module executes it in a controlled namespace with:
  - Read-only DB access via run_analysis_query
  - pandas, numpy, scipy for calculations
  - No file system writes, no network, no imports outside whitelist

All statistical calculations happen here — never inside the LLM.
"""

from __future__ import annotations

import io
import sys
import traceback
from contextlib import redirect_stdout
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .db import run_analysis_query

# ---------------------------------------------------------------------------
# Whitelisted modules available inside backtest scripts
# ---------------------------------------------------------------------------
SAFE_GLOBALS = {
    '__builtins__': {
        'abs': abs, 'all': all, 'any': any, 'bool': bool,
        'dict': dict, 'enumerate': enumerate, 'filter': filter,
        'float': float, 'int': int, 'isinstance': isinstance,
        'len': len, 'list': list, 'map': map, 'max': max,
        'min': min, 'print': print, 'range': range, 'round': round,
        'set': set, 'sorted': sorted, 'str': str, 'sum': sum,
        'tuple': tuple, 'type': type, 'zip': zip,
    },
    'np':     np,
    'pd':     pd,
    'stats':  stats,
    'query':  run_analysis_query,   # the only DB access point
}


def run_backtest(code: str) -> dict:
    """
    Execute a backtest script and return its results.

    The script must assign a dict to the variable `results` containing
    at minimum:
        n               int   — number of qualifying selections
        yield_pct       float — profit / total staked * 100
        avg_clv         float — average closing line value
        p_value         float — p-value vs null hypothesis (yield=0)
        discovery_yield float — yield on discovery period only
        validation_yield float — yield on validation period only

    Returns:
        {
            'success': bool,
            'results': dict | None,
            'stdout': str,
            'error': str | None,
        }
    """
    buf    = io.StringIO()
    local  = {}

    try:
        with redirect_stdout(buf):
            exec(compile(code, '<backtest>', 'exec'), SAFE_GLOBALS.copy(), local)

        if 'results' not in local:
            return {
                'success': False,
                'results': None,
                'stdout': buf.getvalue(),
                'error': "Script must assign a dict to variable 'results'",
            }

        return {
            'success': True,
            'results': local['results'],
            'stdout': buf.getvalue(),
            'error': None,
        }

    except Exception:
        return {
            'success': False,
            'results': None,
            'stdout': buf.getvalue(),
            'error': traceback.format_exc(),
        }


# ---------------------------------------------------------------------------
# Statistical helpers the backtester can use directly
# ---------------------------------------------------------------------------

def compute_clv(entry_odds: float, closing_odds: float) -> float:
    """
    CLV = (1/entry_odds - 1/closing_odds) * 100
    Positive = beat the closing line (real edge).
    Negative = faded by sharp money (bad sign).
    """
    if entry_odds <= 1 or closing_odds <= 1:
        return 0.0
    return (1 / entry_odds - 1 / closing_odds) * 100


def compute_yield(stakes: list[float], payouts: list[float]) -> float:
    total_staked = sum(stakes)
    if total_staked == 0:
        return 0.0
    return (sum(payouts) - total_staked) / total_staked * 100


def binomial_p_value(wins: int, n: int, implied_prob: float) -> float:
    """
    One-sided binomial test: P(X >= wins | p = implied_prob).
    Tests whether win rate is significantly above what the odds imply.
    """
    if n == 0:
        return 1.0
    return stats.binom_test(wins, n, implied_prob, alternative='greater')


def kelly_fraction(edge: float, odds: float) -> float:
    """
    Full Kelly fraction. Use a fraction of this in practice (e.g. 25% Kelly).
    edge = estimated probability of winning - implied probability
    odds = decimal odds
    """
    b = odds - 1  # net odds
    p = edge + (1 / odds)  # estimated win prob
    q = 1 - p
    if b <= 0 or p <= 0:
        return 0.0
    k = (b * p - q) / b
    return max(0.0, k)
