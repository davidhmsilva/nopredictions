"""
Backtester agent.

Takes a hypothesis and:
1. Generates Python code to test it (using the LLM)
2. Executes that code in a safe sandbox (pure Python/pandas/numpy)
3. Returns structured results to the orchestrator

The LLM designs the analysis. Python does all the maths.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic

from .tools.db import (
    get_data_summary, run_analysis_query,
    save_backtest_run, log_agent_run,
)
from .tools.runner import run_backtest

client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
MODEL  = 'claude-opus-4-6'

BACKTEST_SYSTEM = """You are the backtester for Alpha Football.

You receive a hypothesis and must write a Python script that tests it rigorously.

## Your job

Write a Python script that:
1. Queries the database to get the relevant matches
2. Applies the hypothesis filter
3. Computes performance metrics split into discovery (2010-2020) and validation (2020-2023) periods
4. Returns a `results` dict with all required fields

## Available tools in the script namespace

- `query(sql)` — executes a SELECT query, returns list of dicts
- `pd` — pandas
- `np` — numpy
- `stats` — scipy.stats

## Database schema (key tables)

```sql
matches (id, season_id, home_team_id, away_team_id, kickoff_utc, home_score, away_score)
match_odds (match_id, bookmaker_id, home_odds, draw_odds, away_odds, over_odds, under_odds)
match_stats (match_id, home_xg, away_xg, home_shots, away_shots, home_shots_on_tgt, away_shots_on_tgt, home_corners, away_corners)
bookmakers (id, name)  -- 'Pinnacle (legacy)' = opening, 'Pinnacle (closing)' = closing
leagues (id, code, name, country)
seasons (id, league_id, year_start)
teams (id, canonical_name, country)
club_elo (club_name, elo, from_date, to_date)
```

## Required `results` dict fields

```python
results = {
    # Core metrics
    'n_total':           int,    # total qualifying selections
    'n_discovery':       int,    # selections in discovery period (2010-2020)
    'n_validation':      int,    # selections in validation period (2020-2023)

    # Discovery period
    'discovery_yield':   float,  # % yield on discovery data
    'discovery_wins':    int,
    'discovery_clv':     float,  # avg CLV on discovery

    # Validation period (the real test)
    'validation_yield':  float,  # % yield on validation data
    'validation_wins':   int,
    'validation_clv':    float,  # avg CLV on validation
    'validation_pvalue': float,  # p-value vs ROI=0 on validation period

    # Overall
    'avg_clv':           float,  # avg CLV across all periods
    'avg_entry_odds':    float,  # avg odds at entry
    'implied_win_rate':  float,  # 1/avg_entry_odds
    'actual_win_rate':   float,  # actual wins / n

    # By season (for trend analysis)
    'by_year':           list,   # [{year, n, yield_pct, avg_clv}, ...]

    # Qualitative
    'notes':             str,    # any observations worth flagging
}
```

## Critical rules

1. Use `Pinnacle (legacy)` bookmaker for entry price (opening)
2. Use `Pinnacle (closing)` bookmaker for CLV calculation
3. CLV = (1/entry_odds - 1/closing_odds) * 100 — positive = beat the closing line
4. Discovery period: kickoff_utc < '2020-07-01'
5. Validation period: '2020-07-01' <= kickoff_utc < '2023-07-01'
6. Every filter must use only data available BEFORE kickoff
7. If a match has no Pinnacle odds, exclude it — don't impute

Write clean, commented code. The script must be self-contained and runnable."""


def _serial(obj):
    """JSON serialiser for dates/decimals."""
    from datetime import date, datetime
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, '__float__'):
        return float(obj)
    raise TypeError(f"Not serialisable: {type(obj)}")


def generate_backtest_code(hypothesis: dict, data_summary: dict) -> str:
    """Ask the LLM to write backtest code for a hypothesis."""
    prompt = f"""Write a Python backtest script for this hypothesis:

{json.dumps(hypothesis, indent=2, default=_serial)}

Available data summary:
{json.dumps(data_summary, indent=2, default=_serial)}

Write the complete Python script. It must assign a dict to `results` at the end.
Return ONLY the Python code — no markdown, no explanation, just the script."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=BACKTEST_SYSTEM,
        messages=[{'role': 'user', 'content': prompt}],
    )

    code = response.content[0].text.strip()
    # Strip markdown code blocks if the LLM wrapped it
    if code.startswith('```'):
        lines = code.split('\n')
        code = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])
    return code


def run(hypothesis_id: int, hypothesis: dict) -> dict:
    """
    Full backtest pipeline for one hypothesis.
    Returns the backtest results dict.
    """
    log_agent_run('backtester', 'start', f"Backtesting: {hypothesis.get('title', '')}")

    data_summary = get_data_summary()

    # Step 1: Generate backtest code
    print(f"[backtester] Generating code for: {hypothesis.get('title', '')}")
    code = generate_backtest_code(hypothesis, data_summary)

    # Step 2: Save code to file for inspection/debugging
    backtests_dir = Path(__file__).parent / 'backtests'
    backtests_dir.mkdir(exist_ok=True)
    code_file = backtests_dir / f"hypothesis_{hypothesis_id}.py"
    code_file.write_text(code)
    print(f"[backtester] Code saved to {code_file}")

    # Step 3: Execute in sandbox
    print("[backtester] Running backtest...")
    execution = run_backtest(code)

    if not execution['success']:
        print(f"[backtester] FAILED: {execution['error']}")
        # Save failed run
        run_id = save_backtest_run(
            hypothesis_id=hypothesis_id,
            parameters=hypothesis,
            results={'error': execution['error'], 'stdout': execution['stdout']},
            code_used=code,
        )
        log_agent_run('backtester', 'error', execution['error'][:500])
        return {'success': False, 'error': execution['error']}

    results = execution['results']
    print(f"[backtester] Results: n={results.get('n_total')}, "
          f"val_yield={results.get('validation_yield'):.2f}%, "
          f"avg_clv={results.get('avg_clv'):.4f}, "
          f"val_pvalue={results.get('validation_pvalue'):.4f}")

    # Step 4: Persist
    run_id = save_backtest_run(
        hypothesis_id=hypothesis_id,
        parameters=hypothesis,
        results=results,
        code_used=code,
    )

    log_agent_run(
        'backtester', 'complete',
        f"n={results.get('n_total')}, CLV={results.get('avg_clv'):.4f}",
        {'run_id': run_id, 'hypothesis_id': hypothesis_id},
    )

    return {'success': True, 'results': results, 'run_id': run_id}
