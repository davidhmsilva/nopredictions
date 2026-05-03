"""
Critic agent — adversarial review of backtest results.

Tries to destroy every hypothesis before it gets promoted.
If a hypothesis survives the critic, it's genuinely robust.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic

from .tools.db import (
    update_hypothesis_verdict, save_strategy,
    log_agent_run, run_analysis_query,
)
from .tools.runner import run_backtest

client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
MODEL  = 'claude-opus-4-6'

PROMPT_PATH = Path(__file__).parent / 'prompts' / 'critic.md'


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text()


def _run_independent_checks(hypothesis: dict, results: dict, code: str) -> str:
    """
    Run additional Python checks the critic needs.
    Returns a string summary of the independent analysis.
    """
    # Check for seasonal stability — does the edge hold per season?
    by_year = results.get('by_year', [])
    if not by_year:
        return "No per-year breakdown available for stability analysis."

    positive_years = sum(1 for y in by_year if y.get('yield_pct', -1) > 0)
    total_years    = len(by_year)
    val_years      = [y for y in by_year if '2020' <= str(y.get('year', '')) < '2023']
    val_positive   = sum(1 for y in val_years if y.get('yield_pct', -1) > 0)

    checks = [
        f"Seasonal stability: {positive_years}/{total_years} years with positive yield",
        f"Validation seasons positive: {val_positive}/{len(val_years)}",
    ]

    # CLV sign consistency
    discovery_clv  = results.get('discovery_clv', 0)
    validation_clv = results.get('validation_clv', 0)
    if discovery_clv > 0 and validation_clv > 0:
        checks.append("CLV positive in BOTH periods — strong signal")
    elif discovery_clv > 0 and validation_clv <= 0:
        checks.append("WARNING: CLV positive in discovery but negative in validation — likely spurious")
    elif discovery_clv <= 0:
        checks.append("FATAL: CLV negative even in discovery period — no edge")

    # Sample size check
    n_val = results.get('n_validation', 0)
    if n_val < 100:
        checks.append(f"FATAL: Only {n_val} validation samples — insufficient")
    elif n_val < 200:
        checks.append(f"WARNING: Only {n_val} validation samples — marginal")
    else:
        checks.append(f"Sample OK: {n_val} validation selections")

    # Yield decay check
    disc_y = results.get('discovery_yield', 0)
    val_y  = results.get('validation_yield', 0)
    if disc_y > 0 and val_y < disc_y * 0.5:
        checks.append(f"WARNING: Severe yield decay — discovery {disc_y:.1f}% vs validation {val_y:.1f}%")

    return '\n'.join(f"  - {c}" for c in checks)


def run(
    hypothesis_id: int,
    hypothesis: dict,
    backtest_results: dict,
    backtest_code: str = '',
) -> dict:
    """
    Run the adversarial critic on a backtest result.
    Returns the verdict dict and updates the DB.
    """
    log_agent_run('critic', 'start', f"Reviewing hypothesis #{hypothesis_id}")

    # Run independent checks
    independent_analysis = _run_independent_checks(hypothesis, backtest_results, backtest_code)

    user_prompt = f"""Review this hypothesis and its backtest results. Try to destroy it.

## Hypothesis
{json.dumps(hypothesis, indent=2)}

## Backtest Results
{json.dumps(backtest_results, indent=2)}

## Independent Analysis (pre-computed)
{independent_analysis}

## Backtest Code Used
```python
{backtest_code[:3000]}{'...(truncated)' if len(backtest_code) > 3000 else ''}
```

Apply your complete checklist. Return your verdict as JSON."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=_load_system_prompt(),
        messages=[{'role': 'user', 'content': user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Parse JSON
    if '```' in raw:
        import re
        match = re.search(r'```(?:json)?\s*([\s\S]+?)```', raw)
        raw = match.group(1).strip() if match else raw

    verdict = json.loads(raw)
    decision = verdict.get('verdict', 'rejected')

    # Format verdict notes for DB
    notes = f"""Verdict: {decision.upper()} (confidence: {verdict.get('confidence')})

CLV: {verdict.get('clv_assessment')}
Sample: {verdict.get('sample_assessment')}
Statistical: {verdict.get('statistical_assessment')}
Lookahead risk: {verdict.get('lookahead_risk')}
Overfitting risk: {verdict.get('overfitting_risk')}

Key objections:
{chr(10).join(f"  - {o}" for o in verdict.get('key_objections', []))}

Key strengths:
{chr(10).join(f"  - {s}" for s in verdict.get('key_strengths', []))}

Recommendation: {verdict.get('recommendation')}"""

    # Update DB
    update_hypothesis_verdict(
        hypothesis_id=hypothesis_id,
        verdict=decision,
        verdict_notes=notes,
    )

    print(f"[critic] Hypothesis #{hypothesis_id}: {decision.upper()}")
    for obj in verdict.get('key_objections', []):
        print(f"  - {obj}")

    # If promoted, create strategy
    if decision == 'promoted':
        strategy_id = save_strategy(
            hypothesis_id=hypothesis_id,
            name=hypothesis.get('title', f'Strategy #{hypothesis_id}'),
            rules={
                'filter_logic': hypothesis.get('filter_logic'),
                'selection':    hypothesis.get('selection'),
                'entry':        hypothesis.get('entry'),
                'kelly_fraction': 0.25,   # start at 25% Kelly
                'min_odds':     1.5,
                'max_odds':     10.0,
            },
        )
        print(f"[critic] PROMOTED → Strategy #{strategy_id} created")
        verdict['strategy_id'] = strategy_id

    log_agent_run(
        'critic', 'complete',
        f"#{hypothesis_id}: {decision}",
        {'hypothesis_id': hypothesis_id, 'verdict': decision},
    )

    return verdict
