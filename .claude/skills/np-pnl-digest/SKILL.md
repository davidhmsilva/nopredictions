---
name: np-pnl-digest
description: Generate the NOPREDICTIONS P&L digest / content for the site and Twitter — settled results, per-strategy yield, real Pinnacle CLV, and the "what Polymarket got wrong/right" highlights. Use for the weekly recap, a P&L summary, or drafting transparency content. Honest by construction (separates live vs paper, real CLV only, no overselling).
---

# Weekly P&L Digest

Produces the public-facing recap in the brand voice ("No predictions. Just edges.")
without fabricating an edge story. The content angle is *transparency* — every
position, every failure, posted before it resolves — so the digest must be honest:
report losses plainly, never dress circular model-CLV up as real CLV.

## Workflow

1. **Generate the digest** (bundled script → markdown):
   ```bash
   cd /Users/davidsilva/agente && ingest/.venv/bin/python \
     .claude/skills/np-pnl-digest/pnl_digest.py --days 7
   ```
   Output: settled record, net P&L split into live (real money) vs paper, a
   per-strategy table with real-CLV, and the biggest settled wins/losses.

2. **Shape it for the channel:**
   - **Weekly thread (X/Twitter):** lead with the honest headline number (incl. the
     sign), then the per-strategy table, then 1–2 "what PM got wrong/right" lines.
     Sign with the period. No hype, no tips.
   - **Site digest:** the markdown is close to ready; add a one-paragraph narrative
     of what was learned (a fixed bug, a killed hypothesis — the "research graveyard").
   - **Research graveyard:** when a strategy/hypothesis is retired, write what it
     was, why it failed, and the evidence (CIs, CLV). Failures are the content.

3. **Mandatory honesty checks before publishing:**
   - Lead with the real, signed number — including when it's negative.
   - Keep live (real money) and paper separate; don't blend them into a flattering total.
   - Real Pinnacle CLV only. State that it covers ~20% of trades.
   - No "edge confirmed" claims — edge is unproven until positive CLV over 200+
     selections. Frame as a live experiment, not a tipster record.

## Optional richer artefacts
- For a polished spreadsheet (charts/pivots) use the `xlsx` skill on the digest data.
- For a slide recap use the `pptx` skill. Keep the same honesty rules.
