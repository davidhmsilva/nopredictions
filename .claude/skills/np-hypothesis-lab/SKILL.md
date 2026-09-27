---
name: np-hypothesis-lab
description: Generate, pre-register and rigorously test trading-edge hypotheses for NOPREDICTIONS. Use when looking for new edges, when an observation suggests a pattern (a market bias, a strategy tweak, a mispricing), or before acting on any "I think X is mispriced" idea. Enforces the scientific method — testable prediction, pre-registration, walk-forward validation, and self-critique — so we don't fool ourselves.
---

# Hypothesis Lab

The project thesis is "find mispricings, not predictions." That only works if every
edge claim survives the scientific method instead of hindsight and p-hacking. This
skill adapts hypothesis-generation + statistical-analysis + critical-thinking
methodology to trading, and bakes in the lessons we've actually paid for.

## The cardinal lesson
A strategy looked **+28% over its first 100 bets** while its CLV was ~0 — pure
variance, and it reverted. Encouraging early P&L is the single most dangerous
signal. Treat any positive result as variance until proven otherwise.

## Workflow

1. **Formulate a testable hypothesis** (not a vibe). State:
   - the **claim** (e.g. "PM over-prices home favourites in the 70'–85' window");
   - a **mechanism** (why would this inefficiency exist and persist?);
   - a **falsifiable prediction** with a number (e.g. "fading them yields >+3% CLV vs Pinnacle close, with N sized so the CI resolves ±3pp");
   - the **decision rule** that would act on it.
   Reject the obvious unless the data confirms it (CLAUDE.md rule #6).

2. **Pre-register BEFORE looking at outcomes** — write the hypothesis, the exact
   selection rule, the sample window, the metric, and the success threshold into
   `research_hypotheses` (or a dated note). No moving the goalposts after.

3. **Test out-of-sample / walk-forward** — never evaluate on discovery data:
   - Split by time; fit/choose on the past, evaluate on the held-out future.
   - For model changes (e.g. DC params, l2_reg), retrain on data up to T, score
     matches after T against actual outcomes AND closing lines, roll forward.
   - **CLV is a fast estimator, not the judge (rule 5).** Against a SHARP close (Pinnacle pre-match), positive yield + zero/negative CLV is *probably* variance at small n. Where there is no sharp close (in-play, post-whistle, PM's own thin close, structural edges like rebates or settlement rules) CLV does not apply and net yield with a CI is the only evidence. Entry and close on the same side of the book.

4. **Quantify honestly** (statistical rigor):
   - Report a **confidence interval**, not a point estimate. Bootstrap for skewed
     bet P&L.
   - **Size the sample to the claim (rule 4).** n ≈ (1.96·sd/effect)². At ~2.0 odds a ±2pp yield needs ~10,000 bets and 200 resolve only ±14pp; CLV needs tens. Below the n the claim needs, the verdict is "insufficient", whatever the sign.
   - Subgroup claims need CIs + a "how many outcome-flips reverse this?" check.
     Slicing N bets into K buckets and pointing at extremes is p-hacking.
   - Check **calibration**: if the model says 0.51 and reality is 0.29, the "edge"
     is model optimism, not market error.

5. **Self-critique (pre-mortem)** before believing it:
   - What's the most likely *boring* explanation (variance, vig, selection bias,
     a mapping bug inflating CLV)?
   - Is the edge measurable only where we can't trade, or tradeable only where we
     can't measure? (The sharp-coverage gap.)
   - Would it survive costs, slippage, and the executable (ask) price?

6. **Log the outcome — including failures.** A killed hypothesis is content (the
   "research graveyard") and prevents re-testing it. Record what, why, and the
   evidence (CI, CLV, n).

## Promotion bar
A hypothesis becomes a live strategy only when the metric that applies to it
(CLV against a sharp close, else net yield) has an out-of-sample CI clear of zero
at a sample sized for the effect (rule 4). Nothing currently clears it. Until then, paper only.

## Connects to
`np-edge-eval` (the metrics), `np-edge-scan` (sharp validation), `np-risk` (sizing
once proven), and the `feedback-sample-sufficiency` memory.
