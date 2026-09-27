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

- **CLV is a fast estimator, not the judge (rule 5).** Against a SHARP close (Pinnacle pre-match), positive yield + zero/negative CLV is *probably* variance at small n. Where there is no sharp close (in-play, post-whistle, PM's own thin close, structural edges like rebates or settlement rules) CLV does not apply and net yield with a CI is the only evidence. Entry and close on the same side of the book. Real Pinnacle CLV exists for only ~20% of trades (most markets are too
  obscure for a sharp line); `clv_source='model'` is circular garbage, never report
  it as CLV.
- **Size the sample to the claim (rule 4).** n ≈ (1.96·sd/effect)². At ~2.0 odds a ±2pp yield needs ~10,000 bets and 200 resolve only ±14pp; CLV needs tens. Below the n the claim needs, the verdict is "insufficient", whatever the sign. Every strategy is below it. Do not call a subgroup good/bad off a thin slice — compute a CI
  and ask "how many outcome-flips would reverse this?" before asserting. See the
  `feedback-sample-sufficiency` memory.
- **Live vs paper.** The real-money book carries essentially all of the loss; the
  paper book is ~break-even. Always separate the two when reporting.
- **Don't oversell.** A non-broken model is necessary, not sufficient — edge is
  unproven until the metric that applies has a CI clear of zero out-of-sample.

## Deeper dives (when the snapshot raises a question)
- A strategy looks miscalibrated → check the calibration table buckets in
  `evaluate.py` (model says 0.51, reality 0.29 = betting into model optimism).
- Predictions look wrong (~53% home everywhere) → the DC fit is collapsed; use the
  `np-dc-retrain` skill.
- Want to know if a board has edge → use the `np-edge-scan` skill.
