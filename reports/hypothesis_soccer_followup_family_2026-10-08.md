# Pre-registration: follow-up soccer mechanisms

**Registered:** 2026-10-08 19:17 UTC, before evaluating outcomes for rules D–J below.
**Status:** sequential exploratory research. The outcomes for the earlier three-rule family were already seen. Therefore this is not a fresh blind confirmation, even though the following rules and thresholds are fixed before their results are inspected.

## Shared evaluation

Use the local soccer opening-price cache, one bet per fixture, with the fixed 0.006 price half-spread and 5% prediction-market taker-fee formula. Split at 2022-07-01 UTC. Where explicitly stated, use the status cache's pre-match features and its de-vigged closing price plus the same half-spread and fee. Hold to settlement. No rule or threshold changes after evaluation.

Recompute the original rules A–C together with D–J and apply Benjamini–Hochberg to all ten discovery p-values. A lead is only promising if discovery q ≤ 0.10, held-out n ≥ 200, held-out game-level bootstrap 95% CI above zero, positive yield in at least three full held-out seasons (2023–2025), and positive yield after adding 2 percentage points to entry price. This pooled correction does not remove the sequential-selection limitation noted above.

## Rules

- **D — form momentum:** buy the home or away 1X2 side with side-specific last-five points differential ≥ +6.
- **E — fade rest advantage:** buy the home or away 1X2 side with side-specific rest differential ≤ −3 days.
- **F — strong favorite:** buy the home or away 1X2 favorite when its de-vigged opening probability is ≥ 0.55.
- **G — longshot:** buy the home or away 1X2 side when its de-vigged opening probability is ≤ 0.20.
- **H — high-scoring continuation:** buy Over 2.5 when the sum of both teams’ rolling five-match average total goals is ≥ 6.0.
- **I — low-scoring continuation:** buy Under 2.5 when that same sum is ≤ 4.0.
- **J — historical team-status gap:** in the status cache, buy a home or away 1X2 side whose pre-season status is at least two ordinal levels stronger than its opponent (`status_gap ≥ 2`, where lower status number means stronger). Enter at the cache’s de-vigged closing probability plus the fixed half-spread and taker fee. This is a close-price benchmark, not an opening-price execution claim.

## Caveat

All prices are bookmaker-derived proxies. They are not historical executable Polymarket or Kalshi fills, and the status rule uses a close-price proxy. Any candidate would still need prospective validation against contemporaneous executable prices and depth.
