---
name: np-status
description: One-glance health of the NOPREDICTIONS trading agent — realized/unrealized P&L, live real-money exposure, last edge scans and what they found, settled & open positions, per-strategy yield/CLV/calibration. Use when asked "how is the agent doing", current P&L, whether a strategy works, or before/after a trading session.
---

# NOPREDICTIONS Agent Status

Two read-only reports. Run both; they answer different questions.

## Workflow

1. **Operational snapshot** — P&L + bankroll (incl. live real money), last scans,
   settled/open entries by kickoff, observation/shadow stats:
   ```bash
   cd agent && ../ingest/.venv/bin/python status.py
   ```

2. **Edge evaluation** — per-strategy yield + 95% CI, Brier/calibration, and
   **real Pinnacle CLV** (circular model-CLV excluded):
   ```bash
   cd agent && ../ingest/.venv/bin/python evaluate.py
   ```

## How to read it (honesty rules — do not skip)

- **CLV is king.** Positive yield with zero/negative CLV = luck, not edge (project
  rule). Real Pinnacle CLV exists for only ~20% of trades (most markets are too
  obscure for a sharp line); `clv_source='model'` is circular garbage, never report
  it as CLV.
- **Sample sufficiency before any claim.** Every strategy is far below the 200-
  selection rule. Do not call a subgroup good/bad off a thin slice — compute a CI
  and ask "how many outcome-flips would reverse this?" before asserting. See the
  `feedback-sample-sufficiency` memory.
- **Live vs paper.** The real-money book carries essentially all of the loss; the
  paper book is ~break-even. Always separate the two when reporting.
- **Don't oversell.** A non-broken model is necessary, not sufficient — edge is
  unproven until CLV is consistently positive out-of-sample.

## Deeper dives (when the snapshot raises a question)
- A strategy looks miscalibrated → check the calibration table buckets in
  `evaluate.py` (model says 0.51, reality 0.29 = betting into model optimism).
- Predictions look wrong (~53% home everywhere) → the DC fit is collapsed; use the
  `np-dc-retrain` skill.
- Want to know if a board has edge → use the `np-edge-scan` skill.
