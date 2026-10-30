---
title: What an $85,000 Polymarket wallet actually did
summary: One trader made $85,423 in 105 days on a book that never exceeded $29,000. Nearly all of it came from one leg, and identifying that leg needs no model at all.
date: 2026-09-02
kind: research
source: Wallet 0xec5723df…560fa7, rebuilt from 16,157 fills
---

We rebuilt every fill of a single Polymarket account into FIFO round trips.
The headline: **+$85,423 in 105 days**, on a book that never went above
$29,000. All of it in-play. None of it pre-match. Median hold: **1.6 minutes**.

Then the part that made us build a strategy around it — **about half the profit
arrived in the twenty minutes after the final whistle.**

## The decomposition

The account made a lot of small, cheap buys after matches had ended but before
the market had resolved. Split those by whether the token bought was the one
that eventually paid:

| | trades | cost | P&L |
|---|---|---|---|
| bought the eventual **winner** | 323 | $3,565 | **+$27,931 (+783%)** |
| bought the eventual loser | 2,631 | $19,030 | −$690 (−4%) |

The entire return is the winner leg. The loser leg is, to a rounding error,
free — which is what buying at three cents does when you are wrong.

🔑 And identifying the winner leg needs **no model**. The match is over. The
score is public. What is being exploited is not a view about football; it is the
gap between the whistle and the market catching up.

## Speed turned out not to be the point

The obvious reading of a 1.6-minute median hold is that this is scalping, and
that whatever it is, a 30-second Python loop is far too slow to do it.

We tested that directly. Taking the same lots and either holding to resolution
or flipping them the way the account actually did:

- **hold: +158%**
- **flip: +124%**

Holding was *better*. This is buy-and-redeem, not scalping. The speed is a
preference, not the edge — which is exactly what puts it inside reach of a slow
loop.

## How often the opportunity exists

More often than we expected. On one real board we counted **40 markets already
decided at full time** and **24 already decided at minute 70**.

And full time is not even the most frequent case. Every first-half market
settles at the break, with the fixture still live and the board still trading —
and that happens in *every* match.

## Where we are being careful

The risk here is not a model risk and it is not a price risk. **It is our own
code.** A wrong settlement rule means buying something worth zero at six cents
while believing it is a 16× return.

So the primary measurement is not yield. It is `rule_correct`: our verdict
checked back against Polymarket's own resolution. The rules live apart from the
network and the database, with 38 tests, and everything fails closed — an
unparsed question, an ambiguous team, a missing half-time score and an
unrecognised status all return "we do not know" rather than a guess.

⚠️ Knockouts are excluded outright, and that is measured rather than
theoretical. The same account bought "Will Portugal vs. Croatia end in a draw?"
at 0.003 after the whistle, and **the market resolved NO**. Extra time and
penalties change what these questions pay, and that error inverts a position
rather than blunting it.

This is at the observation stage. No orders, real or paper, until the rule is
right at least 99 times in 100 across 200 opportunities.
