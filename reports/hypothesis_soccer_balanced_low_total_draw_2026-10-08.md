# Pre-registration: balanced low-total football draws

**Registered:** 2026-10-08, before evaluating this rule on the cached soccer outcomes.

## Claim and mechanism

In matches where the closing market prices the two teams nearly evenly and the total-goals market expects a relatively low score, the draw may be underpriced. One possible mechanism is a preference for picking a winning team over selecting a draw, while low expected scoring and evenly matched sides raise the draw's base probability.

## Exact rule

- Universe: settled European soccer 1X2 and O/U 2.5 matches in the local closing-odds cache.
- Select a fixture if the absolute difference between de-vigged home and away closing probabilities is at most 0.05, and the de-vigged Under 2.5 closing probability is at least 0.55.
- Buy the draw at the de-vigged closing draw probability plus the registered 0.006 PM half-spread proxy; include the Lab's 5% taker-fee formula. One entry per fixture; hold to settlement.
- No league, season, price or team filters; no parameter tuning.
- Split by the fixture's kick-off date: discovery before 2022-07-01; untouched test from 2022-07-01 onward.

## Success threshold

Promising only if the held-out period contains at least 200 fixtures, net ROI per dollar risked has a fixture-level bootstrap 95% CI entirely above zero, at least three held-out seasons are individually positive, and the result remains positive after an extra 2 percentage points of entry cost.

## Caveat

This tests a historical price proxy based on de-vigged bookmaker closing prices plus a measured half-spread, not actual fills on Kalshi or Polymarket. It is a research lead only; any promising result needs prospective validation on real venue books.
