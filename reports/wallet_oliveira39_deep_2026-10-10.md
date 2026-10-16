# Oliveira39 — deep study (2026-10-10)

`0x54a2c4cfc4332d831acc3f5a860d6540982c1d43` · #2 of the [sharp hunt](sharp_hunt_2026-10-09.md).
Standard profile: `wallet_oliveira39_2026-10-10.md`. This file is what the
profile cannot see: every fill against the market's 1-minute price around it.

## In one paragraph

Oliveira39 is **not a stale-order sweeper and not a tipster**. It is a **stub-quote
maker**: it leaves resting BUY orders far below the price on live NBA and
football markets (MLS, the World Cup, the big European leagues). It gets filled
when someone dumps through a thin book, usually in the minute a goal or a run
crashes the price. It then either sells into the rebound about 5 minutes later
or holds the cheap ones to the end. 96% of its buys on fee-charging markets paid
**no fee**, so it is the maker on both sides. +$98k on $185k deployed in 9
months (+53%, CI [+33, +75]). The robust part is the dip-and-flip leg. **You
cannot copy it**, because the edge is the price of a fill that only happens if
your order is already resting. It is a strategy we can build ourselves.

## Data

- 7,559 activity rows, the whole life of the account (2026-01-17 → 10-07), complete.
- Reconciles with PM's own profit within 1.2% ($99,252 vs $98,114, gross basis).
- 6,604 FIFO lots × the CLOB `prices-history` at 1-minute fidelity, from 1h before
  to 3h after every buy (2,161 windows, 0 failures).
- ⚠️ The tape is (bid+ask)/2 with a missing side counted as 0/1. On a book
  emptied by a crash it reads ask/2 or (bid+1)/2. The minute **before** the
  fill is usually two-sided; the minute of the fill often is not.

## 1. It is the maker, not the taker

| | buys | paid a fee |
|---|--:|--:|
| markets with fees on | 2,762 | **120 (4%)** |
| markets with fees off | 2,694 | 0 (uninformative) |
| sells on fee markets | 577 | 73 (13%) |

On the markets where the fee shows who took liquidity, 96% of its buys and 87%
of its sells were resting orders. Its fill prices cluster on round levels
(0.01, 0.02 … 0.10, 0.20). That is a ladder of bids, not a hunter hitting asks.

## 2. Its orders fill during crashes, far below the price

Median market price (mid) around each fill, by fill-price band:

| fill band | lots | fill | mid −5m | mid −1m | mid at fill | +1m | +5m | +15m | +60m |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 0–0.05 | 1,757 | 0.03 | 0.325 | 0.28 | 0.125 | 0.096 | 0.03 | 0.015 | 0.001 |
| 0.05–0.15 | 2,229 | 0.09 | 0.37 | 0.315 | 0.235 | 0.22 | 0.205 | 0.205 | 0.051 |
| 0.15–0.40 | 1,803 | 0.20 | 0.455 | 0.445 | 0.315 | 0.335 | 0.335 | 0.365 | 0.205 |
| 0.40–0.70 | 681 | 0.53 | 0.635 | 0.645 | 0.635 | 0.665 | 0.65 | 0.64 | 0.875 |
| 0.70+ | 134 | 0.74 | 0.895 | 0.895 | 0.842 | 0.85 | 0.849 | 0.89 | 1.0 |

P&L by how far below the previous minute's mid the fill was:

| fill vs mid 1 min before | lots | cost | P&L | return | share of P&L |
|---|--:|--:|--:|--:|--:|
| bought **above** the mid | 182 | $4,184 | −$3,228 | **−77%** | −3% |
| at the mid (±0.05) | 1,104 | $39,293 | −$2,782 | **−7%** | −3% |
| 0.05–0.20 below | 1,711 | $53,708 | $19,164 | +36% | 20% |
| 0.20–0.50 below | 2,775 | $70,152 | $57,198 | +82% | 58% |
| ≥0.50 below | 799 | $17,564 | $23,614 | **+134%** | 24% |

🔑 **All of the money is in the gap between its fill and the price.** Fills
at the mid lose −7%, the ordinary adverse selection of a resting bid, as in
[[finding-maker-adverse-selection]]. The further below the mid the fill, the
better the trade.

Clearest single example: **Roma v Inter, 2026-09-19, 43'**. "Inter to win" traded
at 0.895 a minute earlier. Its bid at **0.71** was hit for $6,320, and it sold
**3 minutes later at 0.93**: +$1,958. Someone sold through an empty book 18pp
below the market, and the price came straight back.

## 3. Three legs, only one robust

| leg | lots | events | cost | P&L | return [95% CI, clustered by event] | top 5 events |
|---|--:|--:|--:|--:|---|--:|
| **B — dip and flip** (sold, median hold 4.8 min) | 1,948 | 203 | $91,917 | $50,223 | **+54.6% [+38.1, +78.3]** | 26% |
| C — held to the end, fill ≥ 0.15 | 1,571 | 496 | $68,488 | $34,127 | +49.8% [+11.6, +83.6] | 84% |
| A — lowball ladder, fill < 0.15, held | 3,085 | 907 | $24,593 | $13,869 | +56.4% [−12.3, +139.2] | 119% |

- **B is the strategy.** Median: bid filled at 0.16 (6.25) when the mid
  a minute earlier was 0.365 (2.74); sold at 0.23 (4.35) about 5 minutes later.
  It was profitable in every complete month, but the yield is falling:
  110-114% in Jan-Feb, 72-95% in Mar-Apr, then 21-24% in Aug-Sep.
- **A is a lottery ticket that happens to be priced in its favour.** Fills
  at 0.021 won 3.6% of shares (implied 2.1%). Fills at 0.082 won 12.1%
  (implied 8.2%). The direction is right every time, but the leg's CI crosses
  zero and five events carry more than all of its profit.
- **C** is real in level but concentrated: two World Cup bets
  (Brazil–Norway $7.6k, Norway–England $6.0k) are a quarter of it.

Calibration of everything held to resolution (share-weighted):

| fill band | avg price | hit rate |
|---|--:|--:|
| 0–0.05 | 0.021 | 0.036 |
| 0.05–0.15 | 0.082 | 0.121 |
| 0.15–0.40 | 0.245 | 0.382 |
| 0.40–0.70 | 0.537 | 0.825 |
| 0.70+ | 0.753 | 0.995 |

## 4. Copying does not work

The same dollar stake on every one of its lots, held to resolution, entered at:

| entry | return | 95% CI |
|---|--:|---|
| its own fill | +78.3% | [+43.5, +117.2] |
| the mid in the fill minute | +17.4% | [−13.0, +51.9] |
| copy at +1 min (mid) | +18.1% | [−12.9, +53.5] |
| copy at +5 min (mid) | +18.3% | [−13.9, +54.9] |
| copy at +15 min | +12.8% | [−18.6, +46.3] |
| the mid 1 min **before** | −3.3% | [−28.4, +25.7] |

And that is at the **mid**. A copier pays the ask, on a book a crash has just
emptied. Unlike Grand-Vista, where a +5 min copy kept +14%, this wallet's edge
is the fill itself. By the time its trade is public, the order that created
the edge has been used up.

## 5. Where and when

- Market type: totals +67% (38% of P&L), spreads +55% (34%), moneyline +38% (27%).
- League: **MLS +121%** ($26.9k, 96 events), **NBA +75%** ($19.4k, 179),
  World Cup +104% ($18.9k, **11 events**), Bundesliga 2 (dfb) +70% (4 events).
  College basketball loses −71% (−$5.5k, one BYU–Baylor game −$6.7k).
- Minute: all through the match. 15-45' +62%, 60-90' +57%, **90-110' +69%**.
  Pre-match −11%.
- UTC hour: 18:00-03:00, European evenings plus US nights.
- Losing events are mostly the ladder getting filled all the way down on a
  move that was real: BYU–Baylor (122 lots), Algeria–Austria, 76ers–Lakers.

## 6. What changed recently

- Sept 2026: mean entry price rose 0.40 → 0.58; leg B fell to +21%.
- Oct 2026 (in progress): 52% of capital pre-match, −24.7% on $5.5k. Not its
  game, and too early to read.
- The yield of leg B halved from H1 to Q3. That could be more stub-quoters
  competing for the same dumps, or fewer dumps. It cannot be told apart from here.

## 7. What it means for us

1. **Do not offer it as a copy target.** On the Wallet page, the honest label
   is "maker — not copyable". That label is itself a product feature: no
   copy-trading tool says it.
2. **It is a buildable strategy, and one we have never tested.**
   [[finding-maker-adverse-selection]] rejected resting bids **at the touch**.
   This is the opposite end: stub bids 15-50pp below the mid, which only fill
   when somebody dumps. Proposed as **H-STUB-QUOTES**. Measure on the
   `soccer_live` recorder tape (every PM soccer market's book, every minute):
   how often does a market print 20pp+ below the previous minute's mid, and
   where is the mid 5 minutes later? No new data is needed for a first answer.
   Going live needs the websocket ([[finding-market-making]] already names
   60-second polling as the blocker). Ladder orders must be cancelled when a
   **real** event moves the fair price, or they become legs A/C: the −$6.7k
   BYU game.
3. **Capacity is small.** Median ticket $1.78, leg B $5. It is bounded by how
   much other people dump, not by our bankroll.

## Not established

- Why the sellers dump: liquidations, stop-outs, bots selling on a feed
  glitch, or humans panicking after a goal. The tape shows the price crashing
  and recovering, not who sold.
- Whether the crashes coincide with goals. That needs a join to the ESPN
  timelines (Stage L), which was not done here.
- Leg A and leg C are not statistically established on their own (see §3).

Scratch data (raw activity, price windows, per-lot table): session scratchpad
`oli/`. Not committed.
