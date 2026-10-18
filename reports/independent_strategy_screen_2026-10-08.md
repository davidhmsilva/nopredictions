# Independent strategy screen — 2026-10-08

This screen uses rules recorded before the outcome run for each family. The first three-rule soccer family was registered before its outcomes were read; the seven-rule follow-up family was registered after seeing that first family's results, so the follow-up is sequential and exploratory rather than blind confirmation. The rules use simple mechanisms and fixed thresholds; no source files or database rows were changed.

## Results

Yield is net return per dollar staked under the registered fee/spread proxy. Confidence intervals are 20,000-draw percentile bootstrap intervals over individual games.

| Pre-registered strategy | Discovery sample | Held-out sample | +2pp cost stress | Reading |
|---|---:|---:|---:|---|
| NFL primetime moneyline underdogs | n=500; −11.5% [−23.6%, +0.8%] | n=324; −3.9% [−19.4%, +12.2%] | −9.6% [−24.2%, +5.2%] | No positive held-out edge; highly unstable by season |
| Soccer draws in balanced, low-total matches | n=1,340; +1.5% [−6.1%, +9.1%] | n=1,292; −5.5% [−13.0%, +2.2%] | −11.0% [−18.0%, −3.8%] | Discovery gain did not carry forward; fails the cost stress |

### NFL seasonal held-out returns

| Season | Games | Net yield |
|---:|---:|---:|
| 2021 | 53 | +2.3% |
| 2022 | 62 | −18.3% |
| 2023 | 67 | +18.4% |
| 2024 | 66 | −50.9% |
| 2025 | 68 | +27.9% |
| 2026 to Sep. 10 | 8 | −2.9% |

The descriptive control of all NFL moneyline underdogs in the held-out period had n=1,426 and −6.8% yield [−14.1%, +0.5%]. This is not a separately selected strategy.

### Soccer seasonal held-out returns

2022 (from July): n=195, +11.7%; 2023: n=447, −5.4%; 2024: n=311, −6.8%; 2025: n=330, −13.8%; 2026 to Jan. 14: n=9, −34.8%. The rule's positive 2022 result did not persist.

## Conclusion

Neither tested rule is promising under its pre-registered criteria. This says these two methods did not find an edge in these samples; it does not say the underlying markets are efficient. Do not promote either to paper/live trading from this screen.

## Follow-up soccer mechanisms

The initial three-rule soccer family and seven follow-up rules were pooled into one ten-rule discovery family for Benjamini–Hochberg correction. Every discovery q-value was 1.00. Held-out intervals below use 10,000 game-level bootstrap draws. “Stress” adds 2 percentage points to the entry price before the same 5% fee formula.

| Rule | Discovery n / yield | Held-out n / yield (95% bootstrap CI) | +2pp cost yield | Result |
|---|---:|---:|---:|---|
| Rest advantage ≥3 days | 7,457 / −2.7% | 2,659 / −11.6% [−16.8%, −6.3%] | −16.7% | Fails |
| Fade last-five form (≤−6 points) | 17,219 / −6.2% | 6,205 / −12.3% [−16.9%, −7.6%] | −19.9% | Fails |
| Under 2.5 after high recent scoring (sum ≥3.2) | 21,584 / −3.2% | 25,445 / −4.8% [−6.0%, −3.6%] | −8.4% | Fails |
| Follow last-five form (≥+6 points) | 17,219 / −5.2% | 6,205 / −3.2% [−5.7%, −0.7%] | −7.0% | Fails |
| Fade rest advantage (≤−3 days) | 7,457 / −6.1% | 2,659 / −1.5% [−6.6%, +3.8%] | −6.9% | Fails |
| Strong favorite (probability ≥0.55) | 19,530 / −0.5% | 7,203 / +0.9% [−0.8%, +2.6%] | −2.0% | Inconclusive before stress; fails criteria |
| Longshot (probability ≤0.20) | 21,438 / −11.6% | 7,259 / −23.2% [−28.1%, −18.2%] | −32.5% | Fails |
| Over 2.5 after high recent scoring (sum ≥6.0) | 6,235 / −4.7% | 8,499 / −4.1% [−6.0%, −2.2%] | −7.4% | Fails |
| Under 2.5 after low recent scoring (sum ≤4.0) | 3,501 / −2.8% | 3,329 / −6.5% [−9.5%, −3.6%] | −9.7% | Fails |
| Stronger historical team status by ≥2 levels | 24,014 / −4.8% | 8,533 / −2.6% [−5.0%, −0.1%] | −6.9% | Fails |

The strongest held-out point estimate was the favorite filter at +0.9%, but its interval includes zero, its discovery yield was negative, the pooled q-value was 1.00, and the added-cost result was negative. It is not a candidate. The rest-difference feature also has 303 selected records above a 30-day differential; that feature needs data-quality review before any new rest-based test.

These soccer prices are de-vigged bookmaker opening probabilities plus a fixed Polymarket spread and fee proxy. The historical in-play cache does have real Polymarket bid/ask observations, but it covers only 1,543 fixtures from 2026-08-14 through 2026-09-27. That is useful for prospective, event-level testing, but not enough to support a season-level confirmation claim.

## Data boundaries and next test

- The NFL cache contains sportsbook consensus closing prices, not historical Kalshi/Polymarket fills. Its half-spread and PM taker-fee adjustments are proxies.
- The soccer cache uses de-vigged Pinnacle closing probabilities plus the registered PM half-spread and taker-fee proxy, not historical PM executable quotes.
- The separately registered cross-venue Kalshi/Polymarket lag test could not be run: a read-only Supabase connection failed host-name resolution in this environment, so the market-to-fixture/outcome mapping was unavailable. Do not infer a result from that test.
- The Stage M PM history is midpoint-like and may be synthetic when a book is one-sided or empty; Kalshi files include bid/ask but not ask depth. A stronger cross-venue test needs the read-only `venue_market_history` mapping and executable-side book snapshots/depth.

## Registration records

- [NFL primetime underdogs](hypothesis_nfl_primetime_dogs_2026-10-08.md)
- [Balanced low-total soccer draws](hypothesis_soccer_balanced_low_total_draw_2026-10-08.md)
- [Cross-venue football price disagreement — not run](hypothesis_cross_venue_lag_2026-10-08.md)
- [Initial soccer mechanism family](hypothesis_soccer_screen_family_2026-10-08.md)
- [Follow-up soccer mechanism family](hypothesis_soccer_followup_family_2026-10-08.md)
