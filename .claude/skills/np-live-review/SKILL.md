---
name: np-live-review
description: Human-in-the-loop review of proposed Polymarket real-money orders before they execute. Use before a live betting session, before enabling/relying on live execution, or to audit what the agent is about to bet with real money. Surfaces model-only (unvalidated) bets, internationals, large notionals, and guardrail-rejected orders for explicit approval.
---

# Live Order Review (human-in-the-loop gate)

Real money is at stake. The cron can auto-execute via `live_executor`
(`PM_LIVE_MODE=1`), but for any non-routine session a human should see the
proposed orders first. This is the managed-agents "gate / human-in-the-loop"
pattern: the agent *proposes*, a human *approves*, then it *executes*.

Context that makes this matter: a broken model bled real money on +20–45pp bogus
international edges before the guardrails (`edge_engine` sharp gate + 12pp
model-only cap + No-Bias live-disable) were added. The guardrails now contain it,
but they're a safety net, not a substitute for looking.

## Workflow

1. **Generate proposals without executing** — run the scanner with live execution
   OFF so trades are logged as paper and you can inspect what *would* be sent:
   ```bash
   cd agent && PM_LIVE_MODE=0 ../ingest/.venv/bin/python dc_scanner.py   # and/or sim_scanner.py
   ```

2. **Pull the proposed live-eligible orders and review them** (edge, whether a
   sharp validated them, notional, market):
   ```sql
   SELECT s.name, pm.title, pt.outcome, pt.expected_edge,
          pt.sharp_consensus_sources->>'source' AS source,
          ROUND((pt.pm_order_size*pt.pm_order_price)::numeric,2) AS notional
   FROM paper_trades pt JOIN strategies s ON s.id=pt.strategy_id
   LEFT JOIN pm_markets pm ON pm.id=pt.market_id
   WHERE pt.placed_at > NOW()-INTERVAL '1 hour'
   ORDER BY pt.expected_edge DESC;
   ```

3. **Flag and present for approval.** Call out, per order:
   - **MODEL-ONLY** (`source` is "DC Model xG"/"MC Sim", no real sharp) → unvalidated; the model is alone. Highest suspicion.
   - **International** match (national teams) → DC can't price these; recommend skip.
   - **Edge > 12pp model-only** → should be refused by the guardrail; if present, the guardrail isn't active — STOP and investigate.
   - **Large notional** vs `PM_MAX_NOTIONAL_PER_ORDER` ($2.50).
   Summarise as a table and **ask the user to approve, with a default of NOT betting internationals / model-only.**

4. **Execute only the approved set.** Promote the chosen paper trades:
   ```bash
   cd agent && PM_LIVE_MODE=1 ../ingest/.venv/bin/python promote_paper_to_live.py
   ```
   It refetches PM prices, drops edge-gone/dead markets, re-checks the refined gate,
   and submits. Then poll fills:
   ```bash
   cd agent && ../ingest/.venv/bin/python check_fills_retry.py
   ```

5. **Report** what filled vs rested vs failed, total notional, and free CLOB balance.

## Guardrail invariants to verify (defense in depth)
- No live order with a model-only raw edge > 12pp (`PM_MAX_MODEL_EDGE_PP`).
- No live order on a goals market (totals/BTTS) — disabled in `live_executor`.
- No-Bias (strategy id 6) stays paper-only (`PM_DISABLED_STRATEGY_IDS`).
- No duplicate live exposure on the same `pm_token_id`.
If any invariant is violated, the deployed guardrail code is stale — halt and fix
before betting.
