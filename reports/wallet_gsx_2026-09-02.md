# Wallet `0xec5723df…560fa7` = **GSX-** — the in-play tail-buyer

Analysed 2026-09-02. 15,909 activity rows = the entire life of the account
(2026-05-20 → 2026-09-02, 98 active days). Football only.

## The one-line version

**100% in-play, zero pre-match. Median holding time 1.6 minutes. It buys cheap
tokens moments before they reprice — and sweeps stale resting asks at the final
whistle. +$85.4k on a book that never exceeded $29k.**

## Reconciliation (the analysis is trustworthy)

| | |
|---|---|
| leaderboard all-time profit | **+$87,475** |
| reconstructed from activity (FIFO, graveyard written off at 0) | **+$85,423** |
| + maker/taker rebates + rewards | +$2,461 |
| **gap** | **$409 (0.5%)** |

⚠️ The **windowed** leaderboard figures are broken for this wallet: 30d volume
($15,739) is *less* than 7d volume ($61,100), and lb 30d profit (+$260)
contradicts the reconstructed August (+$21,244). Only `window=all` reconciles.
Trust `activity`, not `lb-api` windows.

⚠️ lb "volume" all-time ($4.32M) is **shares, not dollars** — the wallet traded
4,263,971 shares for $1,066,026 of USDC. Its margin is 2.02% of "volume" but
**+15.7% of dollars deployed**. Reading the leaderboard margin here would put it
in the wrong archetype band entirely.

## Shape

| | |
|---|---|
| fills | 15,569 (9,373 BUY / 6,196 SELL), 159/day |
| markets / events / tokens | 1,183 / 830 / 1,391 |
| median ticket | $10.01 |
| BUY vwap / SELL vwap | 0.226 / 0.281 |
| **pre-match $** | **$10 of $1,066,026 (0.001%)** |
| in-play $ | 69.8% · post-whistle 30.2% |
| median hold | **1.6 min** (86% of lots ≤10 min) |
| exits | 93.7% of P&L by SELL, 23% by REDEEM, −16.7% expired worthless |
| peak open cost basis | **$29,014** (median $8,471) |
| cumulative cash floor | **−$577** — self-funding from the first week |
| losing days | 21 of 98 · worst −$1,335 · best +$5,907 |

Universe: World Cup 51% of $, then Brasileirão A+B, MLS, UCL, Sudamericana,
Libertadores, Colombia, Argentina, La Liga, Championship, Chile, Mexico, EPL,
Ligue 1, Bundesliga, Eredivisie, Egypt. **Our exact universe.**

## Where the money is

### By entry price band (graveyard included; robust to FIFO / LIFO / avg-cost)

| entry price | cost | P&L | return | share of total |
|---|---|---|---|---|
| **0.00–0.05** | $18,512 | **+$21,187** | **+114%** | 24.8% |
| 0.05–0.10 | $25,845 | +$12,569 | +49% | 14.7% |
| 0.10–0.20 | $35,404 | +$11,170 | +32% | 13.1% |
| 0.20–0.40 | $87,754 | +$17,527 | +20% | 20.5% |
| 0.40–0.60 | $99,198 | +$10,318 | +10% | 12.1% |
| 0.60–0.80 | $93,650 | +$7,290 | +8% | 8.5% |
| 0.80–0.90 | $51,814 | +$3,517 | +7% | 4.1% |
| 0.90–0.95 | $33,512 | +$800 | +2.4% | 0.9% |
| 0.95–1.01 | $97,188 | +$1,045 | **+1.1%** | 1.2% |

Return falls monotonically with entry price. **The near-certainty corner where
swisstony and `0x2c33…` live is where this wallet makes almost nothing.**

### By entry time (minutes after scheduled kick-off)

| window | cost | P&L | gross | net of worst-case (100% taker) fee |
|---|---|---|---|---|
| in-match <110 | $386,061 | +$37,617 | +9.7% | **+5.3%** |
| **whistle 110–130** | **$119,250** | **+$42,176** | **+35.4%** | **+32.9%** |
| settle 130+ | $33,313 | +$4,950 | +14.9% | +10.3% |
| **all** | $542,877 | **+$85,423** | **+15.7%** | **+11.7%** |

**Half of all profit is made in the 20 minutes after the final whistle.**

## The two businesses

### 1. Stale-order sweeping (55% of P&L on 0.9% of the capital)

807 lots with entry ≤0.15 that exited at ≥3× entry: **$4,682 of capital →
+$46,881 (+1,001%)**, median ticket $1.52, median hold 2.9 min, across 85 events.

Verified examples (winner confirmed from CLOB `tokens[].winner`):

```
+$3,672   3,709 sh "No" @0.010 -> redeem 1.00   Will Lille OSC win on 2026-08-28?   winner=No
  +$992   1,067 sh @0.040 -> sold 0.97 in 1.2m  Spread: Argentina (-1.5)            winner=Argentina
  +$897     988 sh @0.003 -> sold 0.91 in 0.7m  Will PRT vs HRV end in a draw?
  +$890   1,000 sh @0.050 -> sold 0.94 in 1.5m  Will IR Iran win on 2026-06-26?     winner=No
```

Someone left a resting ask at 0.003–0.05 on a token that was already worth ~1.
The wallet takes it and dumps it back into the repriced book minutes later.
**This is the mirror image of [[finding-pm-leads-af-aggregate]]** — we measured
PM leading the api-football aggregate by 25–79s on a penalty; this wallet is the
money on the fast side of that window.

🔑 **The fee is why it works at 1 cent.** `fee = shares × 0.05 × p × (1−p)` peaks
at 1.25pp at p=0.50 and is **0.05pp at p=0.01**. The sweep business is
essentially fee-immune; our own mid-band taker path pays the maximum.

### 2. Buying the tail of the in-play repricing (the other 45%)

In-match lots, by how far the price moved between entry and exit:

| move | n | cost | P&L |
|---|---|---|---|
| loss < −0.05 | 1,138 | $30,461 | −$12,148 |
| flat −0.05..+0.02 | **5,412** | **$183,384** | **−$7,276** |
| +0.02..+0.10 | 1,715 | $113,997 | +$13,288 |
| +0.10..+0.30 | 1,220 | $50,163 | +$25,444 |
| **> +0.30** | **398** | **$8,056** | **+$18,309** |

**It is not scalping a spread — it is buying lottery tickets on a ~90-second
horizon.** The modal position (5,412 lots, $183k, 55% of in-match capital) exits
flat and bleeds the spread. The whole return is the 16% of lots that catch a
goal or a repricing.

## Does it survive a CI? (the BreakTheBank test)

Bootstrap over 830 events, clustered by event, 10–20k resamples:

| slice | events | yield | 95% CI |
|---|---|---|---|
| **all** | 830 | **+15.74%** | **[+11.73%, +20.37%]** |
| **excluding every sweep** | 821 | **+7.16%** | **[+4.89%, +9.61%]** |
| in-match only (<110') | 621 | +9.74% | [+6.63%, +13.14%] |
| post-whistle only (≥110') | 416 | +30.89% | [+19.26%, +46.48%] |

p(return ≤ 0) < 0.0001. Robustness:

- Drop the **top 200 individual lots** → still +$31,841 / **+6.3%**.
- Top 1 / 5 / 10 events = 5% / 21% / 33% of P&L (BreakTheBank: top 5 = **98%**).
- 44% of events profitable; monthly yield **+11.5 / +13.6 / +17.0 / +21.9%**
  (May→Aug) — no decay, if anything improving.

This is the opposite verdict to [[wallet-breakthebank]]: that wallet's +$6.7M
had a CI of [−5.7%, +25.5%]; this wallet's +$85k does not.

## What is NOT established

1. **Selection bias on the wallet itself.** We are looking at it because someone
   pointed at it. The statistics above say the *edge in its own record* is real;
   they cannot tell us how many identically-shaped wallets blew up unseen.
2. **Maker vs taker is unresolved.** Rebates split $1,978 maker / $457 taker,
   but sweeping a resting ask is by definition a take. The reconciliation matches
   the leaderboard to $409 **without** deducting the $21,773 of worst-case taker
   fee, so either the wallet pays almost none of it or lb profit is gross. The
   fee-adjusted column above is the pessimistic bound.
3. **Whether it has a faster data feed** than the PM book, or is simply awake and
   automated on 30 competitions.
4. **Capacity.** Median sweep ticket **$1.52**, p90 $11, max $250. The
   opportunity is bounded by what other people leave resting — it does not scale.

## What it means for us

- **A wallet in our exact universe made 15.7% of turnover doing zero pre-match.**
  That is consistent with, not contrary to, [[finding-spread-floor]] and
  [[finding-pm-mid-is-pinnacle]]: pre-match PM is fair at the mid and untradeable
  by price movement, so the only money is in-play — which is what we already
  believe and have never actually executed on.
- **The measurable, buildable half is the whistle window.** +35.4% gross /
  +32.9% net on $119k, on a signal (the match is over, the result is public) that
  needs no model at all — only a book reader and a fill. We already have every
  piece: `pm_ticks`, `polymarket_client`, `live_executor`.
- **Our pressure agents are aiming at the wrong part of the distribution.** They
  price a fair value and look for a few pp of edge in the 0.4–0.7 band. This
  wallet earns +1% there and +114% below 0.05.
- The in-match arm's modal trade **loses** the spread. Any version of this we
  build must be sized so the tail pays for the bleed — 16% of lots carrying 100%
  of the return.
