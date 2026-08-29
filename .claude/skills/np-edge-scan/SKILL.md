---
name: np-edge-scan
description: Scan a Polymarket board (a competition / tag) for REAL edge — PM price vs our DC model vs the sharp line (Pinnacle/Betfair), gated by the sharp-anchored consensus engine. Use when asked "is there edge in <competition>", to check a league/tournament board, or to sanity-check whether PM is mispriced vs sharp before betting.
---

# Sharp-Anchored Edge Scan

Answers "is there edge here?" for a whole PM board the honest way: an edge is
only real where PM diverges from the price **and our DC model and the sharp line
agree** it's wrong (`fair = min(model, sharp)`). Where the sharp says the price
is fair, the consensus collapses and the edge vanishes — that's a model delusion,
not edge, and it's reported separately.

This is the generalised version of the World Cup scan that exposed the broken
model: on all 72 WC games PM ≈ sharp (avg gap −0.3pp), 0 sharp-validated edges,
while the model "found" 61 fake +20–45pp edges — all correctly killed by the gate.

## Workflow

1. **Find the inputs:**
   - PM tag: from the `polymarket.com/sports/<tag>/...` URL.
   - Odds API sport key (optional but strongly recommended — it's the validator):
     `curl "https://api.the-odds-api.com/v4/sports?apiKey=$THE_ODDS_API_KEY"` (free
     call) and grep for the league. Common ones are mapped automatically.

2. **Run the scan** (bundled script, resolves the repo + venv itself):
   ```bash
   cd /Users/davidsilva/agente && ingest/.venv/bin/python \
     .claude/skills/np-edge-scan/edge_scan.py --pm-tag epl --odds-sport soccer_epl
   ```
   Default (no args) scans the World Cup. Pass `--odds-sport ""` to skip sharp and
   run a model-only scan (results are then unvalidated — treat with suspicion).

3. **Read the output honestly:**
   - **sharp-validated BET signals** — the only ones that count. Usually few/zero;
     mainstream boards (big leagues, World Cup) are efficiently priced and PM ≈ sharp.
   - **model-only signals** — no sharp line existed; the DC model is alone. On
     obscure/minor boards this is where the model's errors leak. Suspect by default.
   - **model delusions** — DC screams edge but sharp says the price is fair. High
     counts here mean the model disagrees with the market, not that there's edge.

## Interpreting results
- Many sharp-validated edges on a liquid board → suspicious; re-check the sharp
  mapping (a wrong line direction on handicaps/totals manufactures fake CLV).
- Zero sharp-validated + many delusions → PM is efficient and our model is off
  here. Correct conclusion: **no edge**, don't bet. (This is the common case.)
- No sharp available at all → you cannot confirm edge; this is measurement, not a
  green light. The real inefficiency, if any, lives in markets too obscure for a
  sharp line — which is exactly where you can't validate. Acknowledge that gap.

## Notes
- No settled outcomes are needed — these are forward candidate edges, not proven
  P&L. Proof still requires positive out-of-sample CLV (see `np-edge-eval`).
- Costs 1 Odds API request per run (quota printed). The DC model and PM data are free.
- Extend `TAG_TO_SPORT` in `edge_scan.py` to map more leagues automatically.
