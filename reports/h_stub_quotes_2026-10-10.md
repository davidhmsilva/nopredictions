# H-STUB-QUOTES — what the book pays a resting bid far below the price (2026-10-10)

The question comes from [Oliveira39](wallet_oliveira39_deep_2026-10-10.md): her
profit comes from resting BUY orders far below the price, filled when someone
sweeps a thin book. This test is not about her. It asks what the WHOLE book
paid every maker who was filled like that.

## Data

- `soccer_live` tape, 2026-09-13 → 09-27 (15 days, local Mac copy): one
  snapshot per minute of every PM soccer market's CLOB book plus PM's own
  live score.
- Universe: 6,094 in-play markets with ≥20 minutes of a clean book (spread
  ≤3pp, mid 0.05-0.95), across ~770 games. Moneyline, totals, spreads, team
  totals, HT result, BTTS, 1H totals.
- Every fill in those markets from `data-api /trades?takerOnly=false`:
  **1,859,870 fills**. 94 of the largest markets are capped at 3,000 fills.
- Kept: **401,874 maker BUY fills** with a clean book in the minute before.
  The taker row of each transaction is removed. `gap` = mid of the previous
  clean minute − fill price.
- Exits measured on the same tape: **sell into the BID at +5 min** (a real,
  conservative exit), the mid at +5 min, or hold to PM's resolution.
- Returns are cost-weighted. The 95% CI is bootstrapped by game.

## Result 1 — the further below the mid, the better the flip

| fill vs previous clean mid | fills | $ filled | flip at bid +5m | hold to the end |
|---|--:|--:|---|---|
| above the mid | 51,011 | $3.68M | −1.2% [−3.5, +1.0] | −3.3% [−8.4, +1.7] |
| at the mid (±2pp) | 264,566 | $21.98M | **−1.2% [−2.1, −0.3]** | −0.7% [−4.1, +3.0] |
| 2-5pp below | 41,802 | $2.60M | +2.5% [−1.4, +6.9] | +8.1% [−2.3, +19.0] |
| 5-10pp below | 14,253 | $403k | +3.9% [−4.8, +13.0] | −5.8% [−19.9, +7.0] |
| **10-20pp below** | 11,737 | $306k | **+5.3% [+0.4, +9.9]** | +6.0% [−9.0, +19.6] |
| **20-30pp below** | 7,508 | $194k | **+14.6% [+3.8, +24.1]** | +9.4% [−14.6, +30.1] |
| **30pp+ below** | 10,997 | $152k | **+34.9% [+15.9, +53.2]** | −11.6% [−39.5, +20.2] |

- An ordinary bid at the touch loses ~1% (the spread and adverse
  selection). That reproduces [[finding-maker-adverse-selection]] on a far
  larger sample.
- From 10pp below the mid, the 5-minute flip is positive with a CI clear of
  zero, and it rises **monotonically** with the distance.
- **Holding to the end has no edge at any distance.** The money is in the
  rebound, not in the outcome. This matches Oliveira39: her dip-and-flip leg
  is the robust one; her held legs are not.

## Result 2 — the score decides it

Fills ≥10pp below the mid:

| | fills | $ filled | flip at bid +5m | hold |
|---|--:|--:|---|---|
| **score unchanged** (someone dumped) | 23,590 | $521k | **+16.8% [+9.8, +23.2]** | +4.2% [−13.3, +19.6] |
| score changed (a goal, so the move was real) | 6,652 | $131k | −0.6% [−13.0, +12.2] | −3.9% [−31.2, +26.4] |

A goal moves the fair price, so a bid filled during a goal is not a bargain.
Cancelling the ladder when the score changes keeps the +16.8% and drops the
zero. The score is read from PM's own live block on the minute tape. A bot
needs it faster than that; see the next steps.

By market family (score unchanged, flip at bid +5m):

| family | fills | $ filled | flip |
|---|--:|--:|---|
| moneyline | 15,148 | $418k | +12.2% [+3.5, +19.7] |
| totals | 6,788 | $76k | **+38.8% [+18.9, +65.0]** |
| spreads | 587 | $10k | +5.9% [−10.9, +10.3] |
| 1H totals · HT result · BTTS · team totals | ~1,070 | ~$17k | large but on 12-61 games |

## Result 3 — size of the pie, and who takes it

- Unchanged-score dumps ≥10pp below the mid: **$34.7k filled per day,
  ~$5.1k/day of flip P&L**, summed over every maker in the book.
- They happen in **458 of 771 games** (median 17 fills per game that has one),
  at every stage of a match (0-30' $102k, 30-60' $203k, 60-80' $111k,
  80'+ $91k over 15 days).
- Fill size: median $3, p90 $36, max $9,318. A few big sweeps carry the dollars.
- **The top 10 wallets take 44%** of it. The largest, `0xdb42c28c…`
  (SharkVision, which was in the Roma–Inter ladder), took $60k in 15 days.
  The rest is spread thin.

## What this says, and what it does not

**Says:** a stub-quote ladder on PM in-play football has a measurable edge
in the book itself, not only in one lucky wallet. Rules that come out of it:
1. Put bids **≥20pp below** the mid. That is where the edge is clearly larger
   than the noise.
2. **Sell on the rebound** within ~5 min. Do not hold.
3. **Cancel on a goal.**
4. **Totals and moneyline** first.

**Does not say:**
- **What OUR fill rate would be.** These fills went to orders that were
  already resting. A new ladder sits behind them in the queue at the same
  price, and the top 10 already hold 44%.
- **Capital lockup.** A resting bid ties up USDC whether it fills or not.
  Return on the capital a ladder needs was not measured.
- **Fees.** The +5 min exit sells into the bid as a taker. The fee is ≤1.25pp
  at 0.50 and smaller away from it, small against +15-35%, but not deducted.
- **Depth at the exit.** The bid at +5 min may not absorb the size.
- **Coverage.** 15 days, football only, a Mac that sleeps (gaps in the tape),
  and the 94 biggest markets capped at 3,000 fills each.

## Next

1. **Replay a ladder on this same data**, which needs nothing new. Virtual
   bids at mid −20/−30pp on clean totals + moneyline, re-placed every
   minute. A bid counts as filled only when a real trade prints **strictly
   below** it, which puts us at the back of the queue. Cancel on a score
   change, sell at the bid at +5 min. Output: fills per day, return on the
   capital locked, worst day.
2. More days. The Hetzner server has been recording since 09-27; this local
   copy stops there.
3. Register as `H-STUB-QUOTES` in `research_hypotheses` before the replay
   is run.
4. Live needs the CLOB websocket (score changes and fills in seconds, not
   minutes). [[finding-market-making]] already names 60-second polling as
   the blocker.

Scripts and intermediate files: session scratchpad `stub/` (tape.py,
trades.py, analyse.py). Not committed.


---

## ⚠️ The ladder replay (same day): the first study overstated it

The tables above measure **other makers' fills at their own prices**. Their
"score unchanged" label also read the score **2-4 minutes after** the fill,
which a bot cannot know at fill time. The replay below asks the question we
actually face: what a ladder WE place would have earned.

**Setup.** Every clean minute (spread ≤3pp) on both tokens of every totals
and moneyline market: one $50 bid at mid − d, re-placed each minute at the
new mid. **Filled only when a real trade prints strictly BELOW our price**
in that minute, which means the sweep passed through us. A taker exit pays
0.05·p·(1−p).

**First pass** (cancel only when PM's live score changes by the next
minute, taker exit at bid +5m with a depth check, else +15m, else hold):

| depth | fills | P&L | per fill | peak capital | green days |
|---|--:|--:|--:|--:|--:|
| 10pp | 2,464 | −$28,780 | −23.4% | $18.1k | 0/15 |
| 20pp | 1,164 | −$11,971 | −20.6% | $15.4k | 1/15 |
| 30pp | 539 | −$6,129 | −22.7% | $12.7k | 1/15 |

The optimistic fill rule (a print AT our price also fills) changes this by
1-5pp, and the conclusion does not move.

**Why.** **37% of the ladder's fills have a goal within the next 4 minutes.**
PM's live score lags the book, so a minute-level cancel is too late, and
those fills lose −40% to −51%. A real goal walks the price through the ladder
and it never comes back.

**Better exits, and the value of knowing about the goal in time** (30pp,
per fill, CI clustered by game):

| | exit sell at bid +5m (taker) | exit resting ask at mid +5m (maker) | exit resting ask at the ask +1m (maker) |
|---|---|---|---|
| everything (what minute data gives) | −1.4% [−10.0, +7.6] | +5.0% [−3.7, +14.4] | +4.3% [−3.6, +13.1] |
| **no goal within 4 min** (upper bound for an instant goal feed) | +14.4% [+2.2, +26.3] | **+21.2% [+8.4, +33.5]** | +13.7% [+3.3, +25.4] |

At 20pp, even the no-goal subset is −2.4% to +3.4%. Holding to the end is
negative everywhere.

### Verdict

- **A naive ladder (minute data, PM's own score) has no edge**: −23% per fill
  with a taker exit, and +5% with a CI crossing zero even with a maker exit.
- **The edge exists only with three things at once:**
  1. bids **≥30pp** below the mid;
  2. **knowing about a goal before the book does** (seconds, not PM's minute
     score) and pulling the ladder;
  3. **exiting as a maker** (a resting ask), not selling to the bid.

  With all three: +14% to +21%, CI clear of zero. The no-goal row is an upper
  bound: it assumes every goal is caught.
- That is what Oliveira39 does: maker on both sides (87% of her sells paid no
  fee) and deep levels. Her edge is the execution stack.
- **Size:** at 30pp, ~17 good fills/day across every PM soccer game at $50
  → ~$130-180/day gross at the upper bound, on ~$12.7k of peak capital. Larger
  orders would only fill if the sweeps are larger, which was not measured.
- **What this needs before anything else:** a goal signal faster than PM's
  live block. Candidates: the CLOB websocket itself (a sudden one-sided move
  on all of a game's markets at once is a goal), or a paid low-latency score
  feed. Next test: whether a "whole game reprices at once" rule, readable
  from the book, catches the goals in time. That can be measured on this same
  tape.

---

## Goal detection from the book: it does not separate (same day)

**Question.** Can the book itself warn of a goal before our bid is hit? The
idea: a goal reprices every market of the game, while a dump hits one.

**Setup.** For every 30pp and 20pp ladder fill above, count the OTHER markets
of the same game (moneyline, totals, spreads, BTTS, team totals, HT) that
traded ≥ X pp away from their own previous mid in the S seconds **before** our
fill. Only before counts, because that is the only window in which a cancel
could happen. Goal label: PM's live score changed within 4 min.

**Timing**, 30pp, 123 goal fills:
- 61 had ≥2 other markets reprice **before** our fill. The lead was p25 2s,
  **median 5s**, p75 49s.
- 47 repriced in the same second or after our fill. Nothing could have
  cancelled those.
- 15 never repriced at all.

**Separation**, 18 rules (any market or moneyline only; move ≥ 5, 10 or 15pp;
S = 2-60s; k = 1-3 markets):
- In every rule, the share of goals caught ≈ the share of good fills thrown
  away. For example, 45% vs 35%, 24% vs 24%, 2% vs 3%.
- The best rule: per-fill **+12.1% CI [−1.5, +26.5]**, against a base of
  **+5.0% CI [−4.1, +14.7]**.
- No CI clears zero, and that is the best of 18 tries.
- At 20pp every rule stays negative.

**Why.** 109 of the 260 no-goal fills (42%) also come with other markets
repricing first. A whale sweeps several markets of a game at once. Penalties,
red cards and VAR also move everything without changing the score. From the
book, a dump and a goal look alike.

### Verdict

- **Established:**
  - a naive ladder at 10-20pp below the mid loses;
  - a detector built from the PM book cannot tell goals from dumps in time.
- **Not established (too little data):** the 30pp ladder with a maker exit,
  +5.0% CI [−4.1, +14.7] on 383 fills. At a per-fill sd of ~1.2, resolving
  +5% needs ~2,200 fills, about 90 days at this rate. That is "not enough
  data yet", not "no edge".
- **What would change the answer:** a goal signal from **outside** the book,
  in seconds. That means a paid low-latency score feed, or an exchange that
  suspends on goals. Its value is bounded by the oracle row above: +21% CI
  [+8, +34] at 30pp, on ~$130-180/day at $50 orders.

### Next

1. More data, at no cost: pull the Hetzner `soccer_live` tape (09-27 → now,
   ~2 weeks) and its fills, then re-run the 30pp replay. That roughly doubles n.
2. Only if 30pp holds up with more data: price a low-latency goal feed
   against ~$130-180/day of upside.

---

## Out-of-sample: 12 more days from Hetzner (09-28 → 10-09)

The 30pp idea was formed on 09-13 → 09-27. The server's `soccer_live` tape
adds 12 days that were never looked at: 4,406 more clean markets and
1.67M more fills. The overlapping days are byte-identical to the Mac copy.
Same replay: a $50 bid at mid − d, filled only on prints strictly below it.

| 30pp | in-sample (15 d) | **out-of-sample (12 d)** | all 27 days |
|---|---|---|---|
| everything, maker exit at mid +5m | +5.0% [−3.7, +14.4] | **+6.9% [−6.2, +21.4]** | **+5.9% [−2.0, +14.2]**, n=699 / 450 games |
| everything, taker exit at bid +5m | −1.4% | +0.6% | −0.5% [−8.1, +7.7] |
| oracle no-goal, maker exit | +21.2% [+8.4, +33.5] | **+7.4% [−6.0, +22.5]** | +14.7% [+5.5, +24.8] |

| 20pp | in-sample | out-of-sample | all |
|---|---|---|---|
| everything, maker exit | −4.8% | −5.2% | **−5.0% [−8.1, −1.6]** |
| everything, taker exit | −10.6% | −10.9% | **−10.7% [−13.8, −7.5]** |

### Verdict (final for now)

1. **20pp loses, replicated out of sample.** Closed.
2. **30pp is positive in both periods and still crosses zero**: +5.9%
   [−2.0, +14.2] on 699 fills. The maker exit assumes a resting ask at the
   mid fills within 5 min, which is optimistic. The taker exit is −0.5%.
   Not established.
3. **The value of a goal feed did not replicate.** The oracle no-goal subset
   fell from +21.2% to **+7.4%** out of sample, no better than the
   unfiltered +6.9%. The in-sample ceiling that justified pricing a feed was
   mostly noise in that period. **A paid goal feed is not justified.**
4. **The size is small whatever the sign.** ~26 fills/day across all PM soccer
   at $50 × ~6% ≈ **$75/day** at the point estimate, against an execution
   stack (websocket, re-quoting every market every few seconds, maker exits)
   that would cost far more to build and run.

**Status: parked.** H-STUB-QUOTES is not refuted at 30pp, but its plausible
size does not pay for the infrastructure. What remains useful:
- **For the Wallet product**: wallets like Oliveira39 should be labelled
  *maker, not copyable*. The mechanism goes in the explainer: careless whales
  with market orders pay deep resting bids, and the book refills within
  seconds.
- **Content**: the Roma–Inter tape (one market order walking the No ladder
  from 0.93 to 0.54 in one second, $20k for shares worth ~$6.5k) is a clean
  story about who pays on Polymarket.
