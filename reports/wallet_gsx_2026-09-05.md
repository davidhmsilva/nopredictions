# Wallet `0xec5723df…560fa7` = **GSX-**

Analysed 2026-09-05. 16,159 activity rows = the entire life of the account (2026-05-20 → 2026-09-05, 104 active days).

## The one-line version

GSX- deployed $552,482 across 853 events in 108 days and finished $80,446 — a yield of +14.56% on money at risk. 100% of that capital went in after kick-off, the median position was held 1.5 min, and the median ticket was $10.00.

## Shape

| | |
|---|---|
| fills | 15,812 (9,518 BUY / 6,294 SELL), 152/day |
| markets / events / tokens | 1,215 / 853 / 1,426 |
| deployed | $552,482 |
| P&L | **$80,446** (+14.56%) |
| median ticket | $10.00 (p90 $167.46, max $2,677) |
| BUY vwap / SELL vwap | 0.225 / 0.281 |
| median hold | **1.5 min** (86% ≤10 min) |
| peak open cost basis | $25,323 |
| cumulative cash floor | -$577.08 |
| losing days | 25 of 102 · worst -$1,402 · best $5,687 |

## By entry price

| entry price | lots | cost | P&L | return | share of P&L |
|---|---|---|---|---|---|
| 0.00–0.05 | 3,616 | $18,851 | $20,677 | +110% | 25.7% |
| 0.05–0.10 | 2,270 | $26,304 | $12,006 | +46% | 14.9% |
| 0.10–0.20 | 1,765 | $33,870 | $8,803 | +26% | 10.9% |
| 0.20–0.40 | 2,577 | $91,345 | $18,745 | +21% | 23.3% |
| 0.40–0.60 | 1,956 | $99,482 | $8,178 | +8% | 10.2% |
| 0.60–0.80 | 1,289 | $97,894 | $6,910 | +7% | 8.6% |
| 0.80–0.90 | 614 | $52,795 | $3,257 | +6% | 4.0% |
| 0.90–0.95 | 316 | $33,575 | $685.96 | +2% | 0.9% |
| 0.95–1.01 | 782 | $98,365 | $1,185 | +1% | 1.5% |

## By entry time (minutes after listed kick-off)

| window | lots | cost | P&L | return |
|---|---|---|---|---|
| pre-match (<0) | 1 | $10.12 | -$0.25 | -2.5% |
| in-match (0–110) | 10,013 | $393,623 | $32,904 | +8.4% |
| whistle (110–130) | 4,277 | $121,038 | $41,886 | +34.6% |
| settle (130+) | 894 | $37,811 | $5,656 | +15.0% |

## Month by month

| month | lots | events | deployed | P&L | yield | pre-match $ | mean entry | median hold |
|---|---|---|---|---|---|---|---|---|
| 2026-05 | 1,698 | 103 | $65,579 | $6,686 | +10.2% | 0% | 0.607 | 1.8 min |
| 2026-06 | 5,609 | 223 | $234,083 | $29,267 | +12.5% | 0% | 0.606 | 1.1 min |
| 2026-07 | 4,376 | 200 | $147,455 | $23,697 | +16.1% | 0% | 0.511 | 1.5 min |
| 2026-08 | 3,171 | 293 | $98,140 | $19,829 | +20.2% | 0% | 0.649 | 2.4 min |
| 2026-09 | 331 | 34 | $7,225 | $968.34 | +13.4% | 0% | 0.606 | 2.0 min |

## What it does

**Archetype: Stale-order sweeper, In-play short-horizon trader, Post-whistle settlement buyer, Earns liquidity rebates, Systematic / automated.**

— *Stale-order sweeper* (high confidence): 786 lots entered at ≤0.15 and exited at ≥3× carry 58% of all profit on 0.8% of the capital; median sweep ticket $1.53, median hold 3.0 min, across 85 events.

— *In-play short-horizon trader* (high confidence): 100.0% of capital goes in after kick-off, 0.0% before; median hold 1.5 min, 86% of round trips close inside 10 minutes.

— *Post-whistle settlement buyer* (high confidence): 28.8% of capital is deployed after minute 110 — when the result is already public; that capital returns +34.6% (whistle) / +15.0% (post-settlement).

— *Earns liquidity rebates* (medium confidence): $2,454 of maker/taker rebates — 3% the size of trading P&L.

— *Systematic / automated* (high confidence): 15,812 fills across 853 events, 152 fills per active day; median ticket $10.00 — size is uniform, which is what a script looks like.

It trades 1,215 markets over 853 fixtures — 15,812 fills, 152 per active day over 104 days with activity. It buys at an average of 0.225 and sells at 0.281.

Where it plays: FIFA World Cup (48% of capital, +15%), Brasileirão (8% of capital, +15%), Internationals (8% of capital, +8%), MLS (5% of capital, +15%).

Market types: moneyline 68%, totals 16%, spreads 8%, soccer_team_to_advance 3%.

## Where the money comes from

**The money is made in the 20 min after the whistle**: $121,038 deployed there returned $41,886 (+34.6%), which is 52% of all profit on 22% of the capital.

Return falls with the entry price: +110% in the 0.00–0.05 band against +1% in 0.95–1.01. That is a wallet paid for taking prices nobody else wanted, not for being right more often.

In-play, the modal trade goes nowhere: the flat -0.05..+0.02 bucket is 4,848 lots and $195,470 of capital for -$3,940. The > +0.30 bucket is 377 lots and $17,453. The tail pays for the bleed — any copy of this has to be sized for that, because most positions lose the spread.

Exits: 80% sold back into the book, 6% redeemed at settlement, 14% simply left to resolve (-$13,944 of that expired worthless).

## How it evolved

Across 4 complete months the book grew from $65,579 to $98,140 of monthly turnover, and the yield is **improving** — +12.0% over the first 2 month(s) against +17.7% over the last 2. Yield rising while turnover holds or grows is the signature of a repeatable process rather than a lucky run.

2026-09 is still in progress ($7,225 deployed, +13.4%) and is excluded from the trend.

Best month 2026-08 ($19,829, +20.2%), worst 2026-05 ($6,686, +10.2%).

Day to day it wins 77 of 102 sessions — best $5,687, worst -$1,402, longest losing streak 5 days. Peak open book $25,323; the cumulative cash floor is -$577.08, so it never needed much more than its first stake — it funded itself out of its own winnings.

## Is the edge real?

Bootstrapped over 853 events (resampled by event, not by lot, because lots inside one fixture are the same bet taken repeatedly): yield +14.56%, 95% CI [+10.51%, +19.19%], p(≤0) = 0.0000. The interval clears zero — the edge in this wallet's own record is real.

Concentration: top event 6% of profit, top 5 24%, top 10 38%; 43% of events profitable. Drop the 200 best individual lots and it still makes $26,947 (+4.88%). The result does not depend on a handful of bets.

Reconciliation: Polymarket's own all-time profit for this wallet is $88,421; our reconstruction brackets it at $82,927 … $102,179 (it falls inside — the reconstruction is trustworthy).

## What is not established

⚠️ 120 lots sold shares that never appear as a purchase ($19,253 of proceeds) — neg-risk conversions, which Polymarket's activity feed does not publish. They are booked FLAT, so they add no profit; the true figure is inside the bracket above, not at either end.

⚠️ -$13,366 of the P&L is unsold positions marked at settlement or at last trade — a mark, not money.

⚠️ Selection bias on the wallet itself. You are reading it because someone pointed at it. The statistics describe the edge in its own record; they cannot tell you how many identically-shaped wallets blew up unseen.

⚠️ `lb-api` 'volume' is SHARES, not dollars, and its windowed figures do not reconcile. Only `window=all` is used here.

⚠️ Capacity: the median ticket is $10.00 and the largest is $2,677. This strategy is bounded by what other people leave resting on the book — it does not scale by adding money.

## Sweep examples (entry ≤0.15, exit ≥3×)

```
    $3,672      3709 sh @0.010 -> 1.00 in   24.5m  Will Lille OSC win on 2026-08-28?
   $997.94      5000 sh @0.010 -> 0.21 in    3.0m  Argentina vs. Egypt: O/U 1.5
   $988.04      1063 sh @0.040 -> 0.97 in    1.2m  Spread: Argentina (-1.5)
   $958.81      1000 sh @0.041 -> 1.00 in  529.7m  Will Kahrabaa Ismailia FC win on 2026-05-29?
   $906.96      1000 sh @0.003 -> 0.91 in    0.6m  Will Portugal vs. Croatia end in a draw?
   $888.06      1000 sh @0.051 -> 0.94 in    1.5m  Will IR Iran win on 2026-06-26?
```

