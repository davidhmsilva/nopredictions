# Pre-registration: cross-venue football price disagreement

**Registered:** 2026-10-08, before joining the historical price paths to settlement outcomes.

## Claim

When Polymarket's minute price for a football outcome is at least 5 percentage points above the cost of buying the same outcome at Kalshi's contemporaneous ask, the Kalshi contract is underpriced often enough to produce positive net return when bought and held to settlement.

## Mechanism

The same event trades in separate venues with different users, liquidity and update paths. A temporary disagreement may leave the slower or thinner venue quoting a price below the information reflected at the other venue. The direction is not assumed in advance; this test specifically checks whether Kalshi lags Polymarket.

## Selection rule

1. Match only the same settled football fixture, market family and outcome across both venues.
2. Compare observations at the same kick-off-relative minute; require both venues to have a recorded quote in that minute. For Kalshi YES, entry is its recorded YES ask. For Kalshi NO, entry is `1 - YES bid`.
3. Treat the Polymarket history price as a reference midpoint, not an executable quote. Enter only when its implied fair value minus Kalshi's executable ask and estimated Kalshi taker fee is at least 0.05.
4. Take at most one entry per fixture-market-outcome: the first eligible observation. Hold to settlement. No exits or parameter tuning.
5. Exclude invalid prices, duplicate minutes, and contracts whose outcome mapping is ambiguous. Report market families separately only as descriptive, not as independent discoveries.

## Split and success bar

Sort fixtures by kick-off. Use the first 70% for discovery and the final 30% as the untouched time-ordered test set. The test set must have at least 200 independent fixture-market-outcome entries to support a positive conclusion. Primary metric is mean return per one-contract Kalshi purchase, net of the stated taker fee, with fixture-clustered 95% confidence interval. A promising result requires positive held-out net return with a CI above zero, no dependence on a single competition or season, and robustness to an additional 2 percentage points of execution cost.

## Known limitations before testing

- Historical Polymarket `prices-history` points are synthetic midpoints; one-sided and empty books can produce misleading values. They are a reference signal only.
- Kalshi files record minute-close bid/ask, but not ask depth or exact fill size; the simulation assumes one contract at the recorded ask.
- The stored universe is football. This cannot establish an edge for NFL, NBA, MLB, NHL, WNBA or college football.
- A positive result is a research lead, not a live strategy. Forward observation with contemporaneous two-sided books is still required.

## Decision

If data linkage, market equivalence or quote quality cannot be established, do not substitute a looser comparison; report that the historical dataset cannot test the claim and specify what recorder is needed.
