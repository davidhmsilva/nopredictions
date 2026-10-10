# Wallet `0x54a2c4cf…2c1d43` = **Oliveira39**

Analysed 2026-10-10. 7,559 activity rows = the entire life of the account (2026-01-17 → 2026-10-07, 213 active days).

## The one-line version

⚠️ THIS RECONSTRUCTION DOES NOT RECONCILE with Polymarket's own profit figure for the wallet, so the numbers below are wrong by an unknown amount — most likely it exits through an activity type the feed does not publish, such as a merge. Read the shape, not the totals. Oliveira39 deployed $184,998 across 1,235 events in 262 days and finished $98,218 — a yield of +53.09% on money at risk. 98% of that capital went in after kick-off, the median position was held 23.0 min, and the median ticket was $1.78.

## Shape

| | |
|---|---|
| fills | 6,660 (5,456 BUY / 1,204 SELL), 31/day |
| markets / events / tokens | 2,034 / 1,235 / 2,161 |
| deployed | $184,998 |
| P&L | **$98,218** (+53.09%) |
| fees paid | $1,034 (0.56% of deployed) |
| median ticket | $1.78 (p90 $53.85, max $9,318) |
| BUY vwap / SELL vwap | 0.146 / 0.297 |
| median hold | **23.0 min** (39% ≤10 min) |
| peak open cost basis | $48,667 |
| cumulative cash floor | -$1,047 |
| losing days | 95 of 189 · worst -$6,739 · best $19,775 |

## By entry price

| entry price | lots | cost | P&L | return | share of P&L |
|---|---|---|---|---|---|
| 0.00–0.05 | 1,757 | $12,790 | $21,473 | +168% | 21.9% |
| 0.05–0.10 | 1,400 | $15,245 | $8,391 | +55% | 8.5% |
| 0.10–0.20 | 1,643 | $32,871 | $28,717 | +87% | 29.2% |
| 0.20–0.40 | 989 | $43,556 | $14,133 | +32% | 14.4% |
| 0.40–0.60 | 594 | $41,226 | $14,312 | +35% | 14.6% |
| 0.60–0.80 | 200 | $34,854 | $10,122 | +29% | 10.3% |
| 0.80–0.90 | 21 | $4,455 | $1,071 | +24% | 1.1% |

## By entry time (minutes after listed kick-off)

| window | lots | cost | P&L | return |
|---|---|---|---|---|
| pre-match (<0) | 58 | $3,080 | -$325.90 | -10.6% |
| in-match (0–110) | 4,940 | $143,118 | $86,139 | +60.2% |
| whistle (110–130) | 989 | $24,339 | $4,905 | +20.2% |
| settle (130+) | 617 | $14,461 | $7,500 | +51.9% |

## Month by month

| month | lots | events | deployed | P&L | yield | pre-match $ | mean entry | median hold |
|---|---|---|---|---|---|---|---|---|
| 2026-01 | 477 | 93 | $7,356 | $9,699 | +131.9% | 0% | 0.177 | 12.6 min |
| 2026-02 | 1,239 | 243 | $21,105 | $4,235 | +20.1% | 0% | 0.165 | 50.2 min |
| 2026-03 | 1,256 | 219 | $21,164 | $12,067 | +57.0% | 1% | 0.308 | 4.7 days |
| 2026-04 | 1,335 | 227 | $28,956 | $17,045 | +58.9% | 0% | 0.421 | 1.1 h |
| 2026-05 | 999 | 223 | $20,769 | $6,964 | +33.5% | 0% | 0.255 | 19.2 min |
| 2026-06 | 625 | 96 | $10,693 | $1,159 | +10.8% | 0% | 0.321 | 15.8 days |
| 2026-07 | 282 | 30 | $24,060 | $29,306 | +121.8% | 0% | 0.442 | 4.1 min |
| 2026-08 | 147 | 59 | $12,364 | $9,756 | +78.9% | 0% | 0.405 | 2.1 h |
| 2026-09 | 157 | 36 | $33,006 | $9,351 | +28.3% | 0% | 0.581 | 5.4 min |
| 2026-10 | 87 | 9 | $5,525 | -$1,362 | -24.7% | 52% | 0.244 | 5.2 min |

## What it does

**Archetype: Stale-order sweeper, In-play short-horizon trader, Post-whistle settlement buyer, Systematic / automated.**

— *Stale-order sweeper* (high confidence): 1,200 lots entered at ≤0.15 and exited at ≥3× carry 66% of all profit on 5.1% of the capital; median sweep ticket $0.69, median hold 6.5 days, across 392 events.

— *In-play short-horizon trader* (high confidence): 98.3% of capital goes in after kick-off, 1.7% before; median hold 23.0 min, 39% of round trips close inside 10 minutes.

— *Post-whistle settlement buyer* (medium confidence): 21.0% of capital is deployed after minute 110 — when the result is already public; that capital returns +20.2% (whistle) / +51.9% (post-settlement).

— *Systematic / automated* (high confidence): 6,660 fills across 1,235 events, 31 fills per active day; median ticket $1.78 — size is uniform, which is what a script looks like.

It trades 2,034 markets over 1,235 fixtures — 6,660 fills, 31 per active day over 213 days with activity. It buys at an average of 0.146 and sells at 0.297.

Where it plays: NBA (14% of capital, +75%), MLS (12% of capital, +121%), FIFA World Cup (10% of capital, +104%), UEFA Nations League (8% of capital, +13%).

Market types: moneyline 37%, spreads 32%, totals 30%, both_teams_to_score 0%.

## Where the money comes from

**The money is made in play**: $143,118 deployed there returned $86,139 (+60.2%), which is 88% of all profit on 77% of the capital.

Return falls with the entry price: +168% in the 0.00–0.05 band against +24% in 0.80–0.90. That is a wallet paid for taking prices nobody else wanted, not for being right more often.

In-play, the modal trade goes nowhere: the flat -0.05..+0.02 bucket is 548 lots and $26,177 of capital for -$1,885. The > +0.30 bucket is 1,306 lots and $79,053. The tail pays for the bleed — any copy of this has to be sized for that, because most positions lose the spread.

Exits: 29% sold back into the book, 22% redeemed at settlement, 49% simply left to resolve (-$40,630 of that expired worthless).

## How it evolved

Across 9 complete months the book grew from $7,356 to $33,006 of monthly turnover, and the yield is **flat** — +54.8% over the first 4 month(s) against +56.0% over the last 5. Yield roughly constant across the life of the wallet.

2026-10 is still in progress ($5,525 deployed, -24.7%) and is excluded from the trend.

**Regime change in 2026-09**: the average entry price moved 0.40 → 0.58. It started buying a different kind of position; the earlier record does not describe the current one.

Best month 2026-01 ($9,699, +131.9%), worst 2026-06 ($1,159, +10.8%).

Day to day it wins 94 of 189 sessions — best $19,775, worst -$6,739, longest losing streak 7 days. Peak open book $48,667; the cumulative cash floor is -$1,047, so it never needed much more than its first stake — it funded itself out of its own winnings.

## Is the edge real?

Bootstrapped over 1,235 events (resampled by event, not by lot, because lots inside one fixture are the same bet taken repeatedly): yield +53.09%, 95% CI [+32.65%, +74.95%], p(≤0) = 0.0000. The interval clears zero — the edge in this wallet's own record is real.

Concentration: top event 8% of profit, top 5 34%, top 10 52%; 50% of events profitable. Drop the 200 best individual lots and the rest of the book LOSES $8,140 (-4.40%) — the entire result is those 200 trades. The result does not depend on a handful of bets.

Reconciliation: Polymarket says $98,114 all-time, counted before fees; on the same basis (P&L plus $1,034 of fees paid) we reconstruct $99,252 — a gap of -$1,137 (1.2%). **That gap is large enough to matter — read the numbers as approximate.**

## What is not established

⚠️ -$40,621 of the P&L is unsold positions marked at settlement or at last trade — a mark, not money.

⚠️ Selection bias on the wallet itself. You are reading it because someone pointed at it. The statistics describe the edge in its own record; they cannot tell you how many identically-shaped wallets blew up unseen.

⚠️ `lb-api` 'volume' is SHARES, not dollars, and its windowed figures do not reconcile. Only `window=all` is used here.

⚠️ Capacity: the median ticket is $1.78 and the largest is $9,318. This strategy is bounded by what other people leave resting on the book — it does not scale by adding money.

## Sweep examples (entry ≤0.15, exit ≥3×)

```
    $5,481      6410 sh @0.145 -> 1.00 in  129.4m  TSV Fortuna 95 Düsseldorf vs. SC Freiburg: O/U 5.5
    $4,201      4287 sh @0.020 -> 1.00 in  306.9m  Spread: Pistons (-7.5)
    $2,969      2999 sh @0.010 -> 1.00 in 67452.3m  FC Metz vs. AJ Auxerre: O/U 3.5
    $2,641      2840 sh @0.070 -> 1.00 in 20975.4m  Knicks vs. Raptors: O/U 225.5
    $1,558      1750 sh @0.110 -> 1.00 in 110570.5m  76ers vs. Pacers: O/U 235.5
    $1,374      1494 sh @0.080 -> 1.00 in  165.8m  FK Bodø/Glimt vs. Sandefjord Fotball: O/U 4.5
```

