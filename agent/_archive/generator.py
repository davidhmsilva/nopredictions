"""
Hypothesis Generator agent.

Reads current pipeline state and generates new, non-obvious hypotheses
to test. Creative and domain-driven — but constrained by what's testable.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import anthropic

from .tools.db import (
    get_data_summary, get_hypothesis_titles, get_leagues,
    get_research_state, save_hypothesis, log_agent_run,
)

client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
MODEL  = 'claude-opus-4-6'

PROMPT_PATH = Path(__file__).parent / 'prompts' / 'generator.md'


def _serial(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, '__float__'):
        return float(obj)
    raise TypeError(f"Not serialisable: {type(obj)}")


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text()


def run(n: int = 3) -> list[dict]:
    """
    Generate N new hypotheses and save them to the DB.
    Returns list of saved hypotheses with their DB ids.
    """
    log_agent_run('generator', 'start', f"Generating {n} hypotheses")

    # Gather context
    data_summary    = get_data_summary()
    existing_titles = get_hypothesis_titles()
    leagues         = get_leagues()
    state           = get_research_state()

    user_prompt = f"""Generate {n} new hypothesis specifications.

## Current pipeline state
{json.dumps(state['hypothesis_counts'], indent=2, default=_serial)}

## Already tested ({len(existing_titles)} hypotheses) — DO NOT REPEAT:
{json.dumps(existing_titles[-50:], indent=2, default=_serial)}

## Available data
{json.dumps(data_summary, indent=2, default=_serial)}

## Leagues available
{json.dumps([f"{l['code']}: {l['name']} ({l['match_count']} matches)" for l in leagues[:15]], indent=2, default=_serial)}

Return a JSON array of {n} hypothesis objects. Each must match the specification structure exactly.
Think deeply about non-obvious angles. Prioritise hypotheses with expected_n > 500."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=_load_system_prompt(),
        messages=[{'role': 'user', 'content': user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Parse JSON — handle markdown wrapping
    if '```' in raw:
        import re
        match = re.search(r'```(?:json)?\s*([\s\S]+?)```', raw)
        raw = match.group(1).strip() if match else raw

    hypotheses = json.loads(raw)
    if isinstance(hypotheses, dict):
        hypotheses = [hypotheses]

    saved = []
    for h in hypotheses:
        hid = save_hypothesis(
            title=h['title'],
            description=h['description'],
            rationale=h['rationale'],
            source='agent',
            status='pending',
        )
        h['id'] = hid
        saved.append(h)
        print(f"[generator] Saved hypothesis #{hid}: {h['title']}")

    log_agent_run(
        'generator', 'complete',
        f"Generated {len(saved)} hypotheses",
        {'hypothesis_ids': [h['id'] for h in saved]},
    )

    return saved
