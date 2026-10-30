---
title: The spread is the tell, not the depth
summary: A book quoting an ask of 0.99 behind $30,117 of depth is not a deep book. It is not a book at all — and every depth filter we had passed it.
date: 2026-08-31
kind: research
source: 100-entry review of the Live Pressure Overs arm, db/037
---

We ran a live in-play strategy to exactly 100 paper entries and then stopped to
look at it. The yield was **−2.07% gross**, about −4.6% after Polymarket's taker
fee, with a confidence interval of **[−22.4%, +18.2%]**.

That interval is the first thing worth saying out loud. It is ±20 points wide
against an effect we would be happy to find at 2–4 points. A hundred bets cannot
answer the question the strategy was built to ask. So the settlement arm was set
aside, and everything below comes from the calibration arm instead — one row per
fixture, minute and score, which has two orders of magnitude more power because
it does not have to wait for a bet to resolve to learn something.

## What the calibration arm found

We compared the realised frequency of the event against the price you could
actually have paid — `real − ask`, clustered by fixture so one busy match cannot
carry the result. Split by how wide the book was at that moment:

| spread at the quote | real − ask |
|---|---|
| 0–3pp | **+3.64pp** |
| 3–6pp | +0.92pp |
| 6–10pp | −4.26pp |
| 20pp+ | **−38.38pp** |

6,449 observations across 494 fixtures.

The bottom row is not a market. It is 430 observations on 221 fixtures, quoting
an ask near **0.90** on an outcome that resolved at **0.529**. It is a single sell order parked a long
way from any bid, with nothing on the other side to argue with it.

## The quote that changed what we filter on

CA Mineiro against EC Vitória, 29 August, fifteen minutes in. The first-half
over 0.5 book quoted:

- **bid 0.55**
- **ask 0.99**
- on **$30,117** of depth

Two minutes later it traded at 0.56.

Every depth filter we had passed that quote, because by depth it was one of the
healthiest books on the board. A liquidity floor asks *how much is there*. It
never asks *whether the two sides agree on anything*, and on a thin in-play
market that second question is the whole of it.

## Depth still matters — but only on one of the three

To be fair to depth: on the full-match over arm it did come out monotone, and
the floor was raised from $50 to $1,000 on the strength of it — −4.72pp under
$200, −0.49pp to $1,000, +3.63pp to $5,000, +4.61pp above. On the two
first-half arms the same cut comes out non-monotone and the floor was left
where it was.

So the honest summary is narrower than "depth does not matter". It is: **the
spread separates on every market we measured, and depth separates on one of
them.** A rule that only had the second one was going to keep buying quotes like
the Mineiro book.

## What this buys, and what it does not

With the spread gate on, the ask is fair to slightly cheap at every minute from
70 to 89 — between +2.1pp and +4.7pp. Without it, the same market looks
*expensive*, by around 5 to 6 points late on.

That apparent expense was never a property of the market. It was an artefact of
including books that were not books.

⚠️ And it is worth being precise about what that means, because it is tempting
to read it as an edge. Every one of those positives still has a confidence
interval crossing zero. Deep, tight books also belong to the larger
competitions, and that confound is not controlled for here. **The gate removes a
measured bleed. It does not find an edge.** Those are different claims and only
the first one is supported.
