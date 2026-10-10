# Is Polymarket's in-play football price calibrated? (H-PM-INPLAY-CAL, 2026-10-04)

**Verdict: yes, within its costs. No taker edge.** Hypothesis #44 in
`research_hypotheses`, pre-registered (db/068) before any price was compared
with an outcome, amended once before unblinding (recorded), verdict recorded.

- Pre-registered predictions: P1 calibrated (**supported**), P2 late
  longshot bias (**rejected**), P3 late overs cheap (**rejected — the
  opposite at real quotes**).
- The one thing this test found that matters for everything else: **the
  history price is not a price when the book is one-sided** (below).

## Data

| | |
|---|---|
| prices | Stage M: 55,458 settled Polymarket football markets, 1-minute history, 2024-08 → 2026-09 |
| outcomes | `venue_market_history.payout0` (0/1, no pushes) |
| match clock and score | ESPN timelines (Stage L) — 6,261 fixtures anchored; 16,259 of 17,458 goals (93.1%) matched to the minute every market of the fixture jumped. Kick-off runs a median 2' late; the second half sits 20' later than the first (p10-p90 15-29') |
| real quotes | the live tape (`agent/data/soccer_live`), bid/ask every minute, 2026-09-13 → 09-27 |
| split | DISCOVERY before 2026-03-01 (3,609 fixtures) · TEST from 2026-03-01 (2,808) |

Statistic: per cell (family × phase × price bucket), the mean over fixtures of
each fixture's mean (outcome − price), CI by fixture bootstrap. Phases: PRE
(listed kick-off −30 to −1), 1H, 2H-A (46-69'), 2H-B (70-95'). Excluded: prices
outside 0.02-0.98, markets the score had already decided, and the goal minute
plus the two after it.

## 🔑 What the history price is

Matched against the tape's real book on 420k minutes:

| book at that minute | share | what `prices-history` reports |
|---|--:|---|
| two-sided | 53% | the mid — median \|p − mid\| 0.00pp, inside [bid, ask] 95% |
| ask only | 28% | **ask / 2** (90%) — an ask of 0.83 reads 0.415 |
| bid only | 11% | **(bid + 1) / 2** (96%) — a bid of 0.90 reads 0.95 |
| empty | 7% | **0.5000** (98%) |

It is `(best bid + best ask) / 2` with a missing bid counted as 0 and a missing
ask as 1. It is the last trade only 5% of the time, even on wide books (1.5%) —
so the Stage M note "last trade when the spread is wide" was wrong, and is
corrected. One-sided books dominate in-play late: by 70' only 20% of totals
books, 12% of spreads and 33% of BTTS are two-sided.

⚠️ **These artefacts fabricate exactly the shape a longshot-bias study looks
for**: they understate cheap tokens and overstate expensive ones. Any study of
history mids — Stage M, and Stage J's `mid_*` fields on thin books — must filter
them. The amendment did it two ways: drop 0.5000, and test only cells where the
tape shows >= 95% of displayed prices came from a two-sided book.

## Result as registered

On the trusted cells, the registered decision rule flagged **16 cells**:
moneyline 0.45-0.85 resolving 3-6pp below price in the first half and 46-69',
moneyline 0.05-0.15 2.2pp above in the first half, BTTS 0.15-0.55 3-4pp above.
It matches the artefact's signature, so it was checked before being believed.

## The check — coherent books (post-hoc, labelled as such)

A fixture's three moneyline books (home, draw, away) must sum to about 1 when
all three are real. 83% of 549,425 in-play fixture-minutes do (0.98-1.04).

| moneyline cells, TEST | coherent minutes (83%) | incoherent minutes (17%) |
|---|---|---|
| 1H 0.45-0.55 | −0.79 [−3.44, +1.87] | **−8.20** [−11.09, −5.34] |
| 1H 0.55-0.65 | −3.25 [−5.97, −0.62] | **−10.32** [−13.42, −7.08] |
| 2H-A 0.45-0.55 | +0.26 [−2.37, +3.01] | **−9.23** [−12.37, −6.01] |
| 2H-B 0.35-0.45 | +1.39 [−3.59, +6.56] | **−12.31** [−15.75, −8.60] |
| 2H-B 0.45-0.55 | +0.80 [−3.14, +4.84] | **−14.40** [−17.49, −11.27] |

On coherent books, **0 of 39 moneyline cells survive BH-FDR (q ≤ 0.10) and 0
pass the rule**. Seven CIs exclude zero, which is near what 39 tests produce by
chance, and their discovery-period values are 2-4× smaller.

## At real quotes (the tape, 261 fixtures, two-sided books only)

| family | phase | outcome − mid | take at the ask | take the other side |
|---|---|--:|--:|--:|
| moneyline | PRE | −0.01 | −1.55 | −1.51 |
| moneyline | 1H | −0.01 | −2.93 | −2.85 |
| moneyline | 2H-A | −0.47 | −3.37 | −2.38 |
| moneyline | 2H-B | −0.49 | −4.26 | −3.23 |
| totals | 2H-B | −5.51 | −13.85 | −2.63 |
| spreads | 2H-B | −7.50 | −16.92 | −1.68 |
| btts | 1H | +4.61 | −1.08 | −10.37 |

pp, means over fixtures; CIs in `agent/data/pm_inplay_cal/report.md`. Pre-match
moneyline at −1.55pp at the ask is the known cost of crossing the spread,
reproduced. In-play, every way of taking loses.

The two coherent cells that still looked off: backing first-half longshots
0.05-0.15 at the ask +0.3pp [−4.4, +5.5] (n=149); fading 46-69' favourites
0.65-0.85 at the bid +1.4 [−6.0, +9.4] (n=127). Nothing.

## The predictions

| | history (trusted cells, TEST) | at real quotes | verdict |
|---|---|---|---|
| **P1** calibrated within costs | moneyline within ±2pp once books are coherent | every taker trade negative | **supported** |
| **P2** late longshots overpriced | longshots +1.22 (wrong sign), favourites −0.41 | fade longshots −2.14, back favourites −4.07 | **rejected** |
| **P3** late overs cheap | not measurable (books two-sided 36-76%) | Over at 70'+ resolves −7.48 vs mid; buying at the ask −17.93 | **rejected — the opposite** |

P3's reversal is the late book's asymmetry, not a mispriced over: the ask sits
far above fair, so the mid does too. It is the 100-game review's "20pp+ bucket
is not a market" again, now on every family.

## What it means

1. **Polymarket in-play is priced right where it is a market**, as it is
   pre-match. Not one test here gives a taker a positive expectation.
2. **The cost is the trade.** In-play, crossing the spread costs 2.9-4.3pp on
   moneyline and 9-17pp on totals and spreads late. Any in-play edge has to be
   larger than that, or it has to be a fill that isn't taking (rewards, or
   settlement — not this test).
3. **Decided markets** (an Over past its line, BTTS after both scored) were
   still displayed between 0.02 and 0.98 on 24,534 minute-rows over 2,450
   fixtures, at 0.691 on average. That is mostly the one-sided artefact itself.
   The real-quote version of that question is H-SETTLED-SWEEP's, with its
   phantom-goal risk.

## Reproduce

```bash
cd agent && source ../ingest/.venv/bin/activate
python pm_inplay_calibration.py --build     # outcome-blind: clock, observations, costs, what the price is (~2 min)
python pm_inplay_calibration.py --report    # the registered test + the tape at real quotes (~2 min)
python pm_inplay_calibration.py --coherence # the post-hoc validity check, kept apart from the registered test
```
