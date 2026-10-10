# UpTheBlues — report (2026-10-10)

`0x2a69660046d7acc4ab204d7cc5ba78b0776cd2f7`

## In one paragraph

A **very large, fully automated taker** that only ever buys and holds to
settlement. It mostly buys the "nothing happens" side: No on team wins,
Unders, No on exact scores, BTTS No. Football (EPL, Turkey, Bundesliga,
Championship) and ATP/WTA tennis. It has turned over ~$314M of sports volume
for **+$480k all time**, and +$1.73M in the last 30 days, so it was about
−$1.25M before September. Every test of skill comes back flat or negative:
it pays ~2.3% over the mid, its pre-match CLV is **negative**, the price does
not move its way after it buys, and four separate 48-hour windows give yields
whose CIs all include zero. **It is not demonstrably sharp.** The hot month is
consistent with variance on a book this size. Not a copy target.

## Data

- Leaderboard (sports): all time +$466k / $314M volume (rank 250); month
  +$1.73M (rank 3); week +$151k; day −$29k.
- Open positions now: $1.82M, almost all on today's matches. There are
  **18,332 resolved, unredeemed losing positions worth −$9.85M of cash P&L**
  (the `/positions` feed caps at 20,000, so this is a floor).
- About 17,000 activity rows a day. The analyser's 250k-row cap covers
  ~2 weeks at most, so **four complete 48-hour windows** were rebuilt
  instead (FIFO, fee-inclusive cost, bootstrap by event).
- Price context: CLOB `prices-history` at 1-minute fidelity for every token
  bought in the last 48h (9,268 windows, 0 failures).
  ⚠️ That tape is (bid+ask)/2, unreliable on one-sided books.

## Shape (last 48h: 2026-10-08 → 10-10)

| | |
|---|---|
| fills | 31,688, **all BUY, zero SELL** |
| markets / events | 7,088 / 1,552 |
| deployed | $4,509,699 |
| P&L (net of fees) | +$108,824 (+2.41%) **CI [−4.14%, +9.29%]** |
| fees paid | $69,226 (1.54%), **taker on 100% of fills** |
| taker rebates received | $19,472 |
| median ticket | $20.88 (max $16,766) |
| peak open book | $1.83M |
| pre-match / in-play | 35% / 58% of capital |
| markets bought on both sides | 1,514 of 7,088 |

Where the money goes: No on moneyline $1.10M · Under $0.89M · Yes on
moneyline $0.37M · No on exact score $0.22M · Over $0.16M. Tennis players
(Zheng, Hurkacz, van de Zandschulp…) about $0.3M.

Concentration: top event = 68% of profit, top 5 = 199%. **Without its best
200 lots the book loses −$334k (−7.4%).**

## Four windows, same machine

| window | rows | deployed | P&L | yield [95% CI] |
|---|--:|--:|--:|---|
| Jul 11-13 | 1,606 | $0.24M | +$75k | +31.6% [−6.0, +81.0] |
| Aug 15-17 | 36,895 | $3.58M | **−$220k** | −6.2% [−14.0, +0.7] |
| Sep 12-14 | 49,988 | $6.43M | +$173k | +2.7% [−3.8, +9.4] |
| Oct 08-10 | 34,991 | $4.51M | +$109k | +2.4% [−4.1, +9.3] |

Zero sells in every window. Swings of ±$100-200k per two days on a few
million deployed are what a +1.73M month and a −1.25M history look like
from the same process.

## Does it buy well? (last 48h, cost-weighted, CI clustered by event)

The price of the token compared with what it paid, fee included:

| | all fills | pre-match | in-play |
|---|---|---|---|
| mid 1 min **before** the fill | −3.80% [−4.44, −3.14] | −2.81% | **−4.33%** |
| mid at the fill | **−2.30%** [−2.55, −2.06] | −2.40% | −2.25% |
| mid +1 min | −1.95% | −1.90% | −1.98% |
| mid +5 min | −1.13% [−2.08, −0.20] | −1.77% | −0.75% |
| mid +30 min | −1.27% [−4.70, +2.00] | −2.40% | −0.16% |
| **mid at kick-off (CLV, buys <3h before)** | | **−0.76% [−1.38, −0.04]** | |
| held to resolution | +4.00% [−3.68, +11.19] | −3.27% [−15.2, +8.4] | +6.09% [−2.68, +14.69] |

- **It pays ~2.3% over the mid**: half the spread plus the taker fee.
- **In-play it buys AFTER the move.** The price a minute before its fill was
  4.3% below what it paid, and after the fill the price only drifts back
  toward its entry. That is a reaction to an event the market has already
  priced, not a lead on it. Compare [[wallet-oliveira39]], whose fills sit
  far BELOW the previous mid.
- **Pre-match CLV is negative**: it buys above the price at kick-off, the
  same signature as [[wallet-king1605]].
- The held-to-resolution numbers are positive in-play but cross zero, and
  they rest on 515 events where the top few bets dominate.

## What it is

A high-turnover, high-capital taker bot. It systematically takes the
low-variance "No/Under" side across thousands of markets, earns taker
rebates (~$10k/day at the current pace), and lives on the variance of a very
large book. Possible readings, none established:
- a model with a small edge on the No/Under side that is swamped by spread
  and fee;
- a rebate farm that needs the trading P&L only to be near zero. Rebates are
  18% of the 48h trading P&L, but the fees paid are 3.5× the rebates, so
  rebates alone do not make it profitable;
- plain variance: four windows, four CIs containing zero, and a history that
  was −$1.25M before this month.

## For us

1. **Not a copy target.** Copying a taker who buys after the move, at 2.3%
   over the mid, with negative CLV, inherits all of the cost and none of an
   edge, if there is one.
2. **For the Wallet product**, this is the case for ranking on CI and CLV
   rather than P&L. It is #3 on Polymarket's monthly sports leaderboard and
   tests flat. That is the screenshot.
3. Not established: the whole history. Only 4×48h of ~10 months were
   rebuilt, because 17k rows/day exceeds what the feed can be walked for.
   The all-time figure is Polymarket's own, gross of fees and without
   rebates.
