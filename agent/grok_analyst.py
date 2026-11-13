"""grok_analyst.py — Grok Analyst (paper).

An autonomous agent that uses Grok (xAI) to decide paper trades across every
sport and esports market on Polymarket. Goal: make money. It sees a compact
board of upcoming/live markets (price, depth, spread, minutes to start, sport),
decides which (if any) to buy, at what stake, and why. Book quality is enforced
before any paper trade is written: the proposed stake must be fillable on the
current CLOB book (depth + spread gates).

This is the Grok side of the Grok vs Claude competition on nopredictions.com.
Paper only. Nothing here places a real order.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

STRATEGY_NAME = "Grok Analyst — all sports / esports"
FEE_RATE = 0.05  # Polymarket sports taker fee (same as other agents)

# Book quality for variable stakes. A stake is only papered if the top of the
# book can absorb it without a silly spread.
MAX_SPREAD = 0.04
MIN_DEPTH_MULT = 1.5          # ask depth must cover stake * this
MAX_STAKE_USD = 5000.0        # hard cap even if Grok asks for more
MIN_STAKE_USD = 5.0


@dataclass
class Opportunity:
    event_id: str
    event_title: str
    sport: str
    league: str | None
    start: datetime | None
    minutes_to_start: float | None
    condition_id: str
    token_id: str
    question: str
    outcome: str
    family: str
    ask: float
    bid: float
    spread: float
    ask_depth_usd: float
    gamma_mid: float | None = None


def net_odds(ask: float) -> float:
    """Decimal odds net of the taker fee."""
    if ask <= 0 or ask >= 1:
        return 1.0
    cost = ask * (1.0 + FEE_RATE * (1.0 - ask))
    return 1.0 / cost if cost > 0 else 1.0


def can_fill(opp: Opportunity, stake_usd: float) -> bool:
    """True if this stake is realistically fillable on the current book."""
    if stake_usd < MIN_STAKE_USD or stake_usd > MAX_STAKE_USD:
        return False
    if opp.spread > MAX_SPREAD or opp.ask <= 0 or opp.ask >= 1:
        return False
    if opp.ask_depth_usd < stake_usd * MIN_DEPTH_MULT:
        return False
    return True


def build_prompt(opportunities: list[Opportunity], recent_pl: float | None = None) -> str:
    """Compact board for Grok. Keep it dense so the model can scan many markets."""
    lines = []
    for o in opportunities[:80]:  # hard cap for prompt size
        mins = f"{o.minutes_to_start:.0f}m" if o.minutes_to_start is not None else "?"
        lines.append(
            f"{o.sport}|{o.event_title}|{o.question}|{o.outcome}|"
            f"ask={o.ask:.3f} bid={o.bid:.3f} spr={o.spread:.3f} "
            f"depth=${o.ask_depth_usd:.0f} start_in={mins}"
        )
    board = "\n".join(lines) if lines else "(no markets in window)"
    pl = f"Recent paper P&L (units): {recent_pl:+.2f}" if recent_pl is not None else "No settled record yet."
    return f"""You are Grok Analyst, an autonomous paper-trading agent on Polymarket sports & esports.
Goal: make money. You see only the board below (prices, depth, spread, time). Decide which tokens (if any) to buy.

Rules you must respect:
- Paper only. Stake is in USD notional.
- Only propose trades you believe have positive expected value after fees.
- Stake size should respect liquidity (don't ask for more than the book can absorb).
- You may propose 0 to 5 trades this cycle.
- Output ONLY valid JSON: {{"trades": [{{"token_id": "...", "stake_usd": 25.0, "reasoning": "short why"}}]}} or {{"trades": []}}
- If you need more data, say so in reasoning and skip the trade; do not invent numbers.

{pl}

BOARD (sport|event|question|outcome|ask bid spr depth start_in):
{board}
"""


def parse_trades(raw: str) -> list[dict[str, Any]]:
    """Extract the JSON object from a Grok reply. Tolerant of markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # try to find the first {...}
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    trades = data.get("trades") if isinstance(data, dict) else None
    if not isinstance(trades, list):
        return []
    out = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("token_id") or "").strip()
        try:
            stake = float(t.get("stake_usd") or 0)
        except (TypeError, ValueError):
            continue
        reason = str(t.get("reasoning") or "")[:500]
        if tid and stake > 0:
            out.append({"token_id": tid, "stake_usd": stake, "reasoning": reason})
    return out
