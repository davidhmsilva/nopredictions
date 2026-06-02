---
name: np-risk
description: Bankroll and position-sizing discipline for NOPREDICTIONS — Kelly/fractional-Kelly stake from a fair probability and executable price, plus live-exposure-vs-bankroll checks. Use when deciding how much to stake on a bet, reviewing total real-money exposure, or setting risk limits. Defaults to conservative sizing because our edge is unproven.
---

# Risk & Bankroll Management

A complete trader sizes positions and bounds exposure; we currently bet 1u flat
with per-order caps and no portfolio view. This skill adds principled sizing —
but stays deliberately conservative, because **Kelly assumes you know the true
edge and we don't** (no consistent +CLV over 200+ selections). Over-betting a
mis-estimated edge is the fastest path to ruin.

## Workflow

1. **Size a single bet:**
   ```bash
   cd /Users/davidsilva/Documents/agente && ingest/.venv/bin/python \
     .claude/skills/np-risk/risk_calc.py --fair 0.55 --price 0.45 --bankroll 20
   ```
   Reports full Kelly, the recommended quarter-Kelly (hard-capped at 5% of
   bankroll), and warnings (no edge / implausible >20pp edge).

2. **Check live exposure vs bankroll:**
   ```bash
   cd /Users/davidsilva/Documents/agente && ingest/.venv/bin/python \
     .claude/skills/np-risk/risk_calc.py --exposure
   ```

## Sizing rules (non-negotiable until edge is proven)
- **Fractional Kelly only** — quarter-Kelly at most, hard-capped at ~5% of bankroll
  per bet. Full Kelly is for known edges; ours is a hypothesis.
- **No positive edge → no bet.** Edge measured at the *executable* price (ask), not
  the mid.
- **Edge > 20pp model-only = model error, not edge** — refuse or demand a sharp line
  (matches the live `edge_engine` 12pp cap; the live P&L audit showed everything
  >10pp bled).
- **Total open notional < ~30–40% of bankroll** — survive a losing streak. A
  −47% live yield over 134 bets is a real drawdown; size to outlast it.
- **One position per outcome** — no duplicate live exposure on the same token
  (the executor dedups; don't override it).

## Connects to
- Sharp validation before sizing up → `np-edge-scan`.
- Whether the edge is even real → `np-edge-eval` (CLV is king).
- Reviewing what's about to be staked → `np-live-review`.
