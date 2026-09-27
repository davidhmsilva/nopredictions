---
name: np-edge-eval
description: Rigorously evaluate whether a NOPREDICTIONS strategy has real edge — yield + confidence interval, Brier/calibration, real Pinnacle CLV — with the non-negotiable honesty guards (sample sized to the claim, CLV as the fast estimator against a sharp close, sample-sufficiency before any subgroup claim). Use before claiming a strategy works, before promoting to live, or when reviewing P&L for a verdict.
---

# Edge Evaluation (the honesty harness)

Edge is a claim that must survive scrutiny, not a hopeful yield number. This skill
exists because it's easy to fool yourself: a strategy looked +28% over its first
100 bets while its CLV was ~0 — pure variance, and it then reverted.

## Workflow

1. **Run the harness:**
   ```bash
   cd agent && ../ingest/.venv/bin/python evaluate.py
   ```
   Per strategy it prints: record, yield + 95% CI + p(yield>0), Brier vs base
   (calibration buckets), and **real Pinnacle CLV** (circular model-CLV excluded).

2. **Apply the project's non-negotiable rules before any verdict:**
   - **CLV is a fast estimator, not the judge (rule 5).** Against a SHARP close (Pinnacle pre-match), positive yield + zero/negative CLV is *probably* variance at small n. Where there is no sharp close (in-play, post-whistle, PM's own thin close, structural edges like rebates or settlement rules) CLV does not apply and net yield with a CI is the only evidence. Entry and close on the same side of the book. Real Pinnacle CLV
     covers only ~20% of trades; `clv_source='model'` is circular — never report it.
   - **Size the sample to the claim (rule 4).** n ≈ (1.96·sd/effect)². At ~2.0 odds a ±2pp yield needs ~10,000 bets and 200 resolve only ±14pp; CLV needs tens. Below the n the claim needs, the verdict is "insufficient", whatever the sign.
   - **Sample sufficiency on subgroups.** Before saying any slice (odds bucket,
     market type, competition) is good/bad: bootstrap a CI, and ask "how many
     outcome-flips reverse this?" If 1–2 flips flip the verdict, you have nothing.
     Slicing N bets into K buckets and pointing at the extremes is p-hacking.
   - **Calibration is the tell.** If a strategy predicts 0.51 but wins 0.29 in its
     biggest bucket, it's betting into its own model's optimism — the "edge" is
     model error. Often pairs with favourite-longshot bleed (high-odds bets lose).
   - **Separate live from paper.** The real-money book holds the loss; paper is
     ~break-even. Report them apart.

3. **Verdict vocabulary:** "CLV+ (sig)", "CLV~ (flat)", "no real CLV", "INSUFFICIENT
   DATA (n<30)", "MISCALIBRATED". A strategy is promotable only when the metric
   that applies to it (CLV against a sharp close, else net yield) has a CI clear of
   zero at a sample sized for the effect — nothing currently clears that bar.

## Deeper analyses (run ad-hoc, with CIs)
- Yield by entry-odds bucket (favourite-longshot check) — but report bootstrap CIs;
  thin buckets (n<50) are not conclusions.
- CLV vs yield reconciliation — if CLV is +sig but yield is −, suspect a sharp
  mapping bug (wrong handicap/total line direction) before believing the CLV.
- Cross-reference the `feedback-sample-sufficiency` and `eval_harness` memories.
