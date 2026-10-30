---
title: The pressure signal did not work
summary: We built three strategies on the idea that watching a match tells you something the price does not. Measured properly, the filter selects nothing — and the best rows are the ones it throws away.
date: 2026-09-05
kind: research
source: db/037 and db/040, 494 fixtures on the full-match arm, 338 on the first half
---

The premise was reasonable. A live match produces shots, corners, possession and
expected goals within seconds. Polymarket's price is set by people, some of whom
are not watching. If a team is visibly battering the other and the score is
still 0-0, maybe the over is cheap.

We built three strategies on that premise and ran them on paper for weeks. This
is what the data says.

## The gate selects nothing

The filter is a pressure index on a 0–100 scale. The test is simple: sort every
observation by how much pressure we read, and see whether the ones we called
"high pressure" did better than the ones we ignored.

Realised frequency minus our fair value, by pressure quintile, at minutes 75–88:

| quintile | 1 (quietest) | 2 | 3 | 4 | 5 (loudest) |
|---|---|---|---|---|---|
| real − fair | +4.82pp | +10.82pp | **+13.84pp** | +9.61pp | +7.29pp |

It peaks in the **middle**. A signal that worked would rise from left to right.
This one goes up and then comes back down, which is what noise plus a table bias
looks like.

Restricting to clean books makes it worse, not better: entries above the
threshold gave **+2.35pp** against **+5.28pp** for everything below it. **The
gate is throwing away the better rows.**

## The first-half arm agrees, tested separately

Different market, different table, out-of-sample five-fold cross-validation,
paired difference in log-loss, bootstrap interval — against a control that knows
only the minute and the pre-match total.

- pressure as a mean: **+0.00380, CI [+0.00278, +0.00498]** — worse than the
  control, and confidently so
- pressure at its maximum: +0.00265, CI [−0.00218, +0.00750] — indistinguishable
  from zero

Terciles are flat within ±9pp. Whatever the live reading contains, the minute
and the pre-match total already had it.

## How much is actually in the box score

We measured the ceiling directly. Over and above what is free — the minute, the
score, the pre-match total — the entire apparatus of shots, corners, possession
and xG adds **+0.0008 pseudo-R²**.

For scale: the Polymarket price itself carries roughly **sixteen times** that.

There is a version of this result that sounds like a bug in our feed, so it is
worth saying that we chased that too. A separate test asked whether the
*movement* of the ask carried anything beyond its level. It came back
**−0.00042, CI [−0.00104, +0.00019]** — a zero.

## What was left standing

Not the signal. What survived the review was something we were not looking for:
**book quality**, which turned out to separate cleanly where pressure did not.
That is [its own piece](/insights/the-spread-is-the-tell).

## Why the thresholds are still in the code

A fair question, given the above. The answer is that removing them is a decision
about what these strategies *are*, not a bug fix — three arms defined by a
signal, with the signal removed, are a different experiment wearing the same
name. So they stay, and they stay recorded, and every row carries the control
arm beside the treatment so the comparison remains answerable from the data.

None of the three is near its verdict gate, which is 200 settled entries with an
interval clear of zero after fees. At the current rate that is months away. We
would rather publish this now than publish a number later.
