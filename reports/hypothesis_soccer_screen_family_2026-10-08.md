# Pre-registration: three independent soccer mechanisms

**Registered:** 2026-10-08, before evaluating these three rules on cached soccer outcomes.
**Status:** exploratory family. The same broad held-out archive was used in an earlier unrelated draw-rule screen, so these results are not a fresh blind confirmation set.

## Shared universe and price

Settled European soccer matches in the local opening-odds cache. Outcomes are priced at de-vigged opening bookmaker probabilities plus the stored 0.006 half-spread proxy, then the Lab's 5% taker-fee formula. Hold to settlement; one entry per fixture per rule. Time split is kick-off before 2022-07-01 for discovery and on/after that date for the held-out period. No closing information is used for entry selection.

## Hypothesis A — rest advantage

**Mechanism:** a team with substantially more recovery time may have a physical edge that is not fully priced, particularly across a broad multi-league sample.

**Rule:** buy the home or away 1X2 outcome whose recorded side-specific rest differential is at least +3 days. No other filter.

## Hypothesis B — fade recent form

**Mechanism:** short-term win/loss narratives may push opening prices too far toward the team with the better last-five points total.

**Rule:** when the side-specific last-five points differential is at most −6, buy that home or away side's 1X2 outcome. This is exactly the side with the weaker recent form. No other filter.

## Hypothesis C — fade recent high scoring

**Mechanism:** recent goal-rich matches may cause bettors to overestimate the next match's total; test whether that makes the Under 2.5 cheaper relative to outcomes.

**Rule:** when the sum of the home and away teams' rolling five-match average total goals is at least 3.2, buy Under 2.5. No other filter.

## Evaluation rule

- Compute the discovery one-sided test of positive net yield for each rule; apply Benjamini–Hochberg across the three rules and require q ≤ 0.10 before treating any as a candidate.
- The held-out period must contain at least 200 fixtures. A promising lead then requires positive held-out yield with game-level bootstrap 95% CI above zero, positive return in at least three full held-out seasons, and positive return after an extra 2 percentage points of entry cost.
- No threshold changes, extra league filters or alternative exit rules after seeing results. All other cuts are descriptive only.

## Caveat

Opening bookmaker prices plus a fixed half-spread are a PM-style proxy, not historical Polymarket or Kalshi fills. A candidate needs prospective validation with contemporaneous executable venue prices and depth.
