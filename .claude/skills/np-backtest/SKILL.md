---
name: np-backtest
description: Run the no-lookahead walk-forward backtest of the DC model against historical Pinnacle opening/closing odds to measure real out-of-sample yield and CLV. Use to test whether the model (or a model change) has edge over the sharp market before trusting it, or to re-check the edge after a retrain or a parameter change. This is the project's gold-standard edge test.
---

# Walk-Forward Backtest

The honest answer to "does the model have edge?" — measured the way the project's
own rules demand (rule #2 walk-forward, rule #5 CLV-is-king), over ~56k bets.

## What it does
For each period it retrains DC **only on matches before that period** (trailing
window, no lookahead), then for every match bets each outcome whose DC edge over
the de-vigged Pinnacle **opening** price ≥ threshold. It settles at the opening
odds vs the real result (**yield**) and compares opening vs Pinnacle **closing**
odds (**CLV**). Bootstrap CIs on both.

## Run it
```bash
cd agent && ../ingest/.venv/bin/python backtest_walkforward.py            # full 2018+
cd agent && ../ingest/.venv/bin/python backtest_walkforward.py --start 2025-01-01   # quick
# knobs: --threshold 0.05  --retrain-months 3  --train-window-years 4  --l2-reg 1.0
```
Full run is ~10-15 min (16 retrains). Use a recent --start for a smoke test.

## The established baseline (2026-06-03)
DC vs Pinnacle, 55,841 bets: **YIELD −6.87%** [−8.2,−5.5], **CLV −0.33%** [−0.41,−0.24].
Both significantly negative. **DC does not beat Pinnacle on 1X2** — its market
disagreements are model error, not edge, and the bigger the disagreement the worse
the CLV. Any new variant must beat THIS baseline to be worth anything.

## How to read a result (honesty rules)
- **CLV is the verdict, not yield.** Positive yield with flat/negative CLV = luck.
  Edge is real only if CLV is significantly positive (CI excludes 0).
- A negative-CLV result means the model is anti-informative vs the sharp — don't
  bet that signal; if anything it argues for fading it (test that explicitly).
- This benchmark (Pinnacle 1X2 opening) is the hardest in the world. A model can
  fail here yet still beat softer venues (Polymarket) — but the burden of proof is
  on you, and you can't backtest PM (no historical PM prices). Use the observer for
  forward PM-vs-sharp CLV instead.

## Productive variants to test (each must show +CLV to matter)
- By league tier — does DC add value where Pinnacle is less sharp (lower leagues)?
- Fade the model — if DC is anti-predictive, does betting against its edges give +CLV
  after vig?
- Non-1X2 markets, or only sharp-validated picks (model ∩ sharp).
