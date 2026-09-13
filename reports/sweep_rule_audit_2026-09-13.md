# Settled-market sweep — first `rule_correct` audit (2026-09-13)

H-SETTLED-SWEEP (id 31), `settled_market_observations`, **obs_version 1**,
2026-09-04 → 2026-09-13 ~12:00Z. Observation only — no orders.
Triggered by two wrong entries on Seoul E-Land v Suwon, 2026-09-12.

## 1. Where the rules are wrong

| phase | settled rows | fixtures | rule wrong | `would_enter` settled | wrong entries |
|---|--:|--:|--:|--:|--:|
| `post_whistle` | 235,389 | 692 | **0** | 2 | 0 |
| `halftime` | 6,319 | 169 | **0** | 0 | — |
| `in_match` | 22,871 | ~207 | **21** (7 fixtures) | 19 | **3** |

`rule_correct` on `in_match`:

- over **all** rows: 22,850 / 22,871 = **99.91%**
- over **`would_enter`** rows: 16 / 19 = **84.2%**

The error rate is ~170× higher on the rows that would be bought than on the
population. Pooled across every phase the figure is 99.99%, which hides it
completely.

## 2. One cause: the feed showed a goal that did not stand

Every one of the 21 wrong verdicts traces to the score input. There are
**zero** errors from question parsing, side resolution or rule logic.

| fixture | date | feed showed | what stood | phantom lasted | wrong verdicts |
|---|---|---|---|---|--:|
| Seoul E-Land v Suwon (K League 2) | 09-12 | 0-2 at 65' | 0-1 | 5 polls, 65'-68' | 6 (2 entries) |
| Pyramids v El Gouna (Egypt) | 09-09 | 2-0 at 89' | 1-0 | 2 polls | 2 (1 entry) |
| Criciúma v Juventude (Brazil B) | 09-09 | 1-0 at 8' | HT 0-0, FT 0-2 | 2 polls | 1 |
| CRB v América-MG (Brazil B) | 09-05 | 4-0 at 75' | 3-1 | 1 poll | 1 |
| Austin v San Jose (MLS) | 09-06 | 1-1 at 11' | HT 0-1, FT 1-1 | 11'-13' | 3 |
| Bucheon v Daejeon (K League 1) | 09-05 | 0-3 at 40' | HT 0-2, FT 0-5 | 1 poll | 1 |
| FC Kharkiv v Obolon (Ukraine) | 09-05 | 5-1 at 83'-90' | PM paid as 5-0 (BTTS No, O5.5 Under, 2H O0.5 Under); feed never reached FT | — | 6 |

- Austin and Bucheon are invisible to a check against the **full-time** score:
  a later, legitimate goal restores the total. Only the **half-time** score
  exposes them.
- Kharkiv is unverified. It was either an abandoned match or a disallowed goal;
  our feed stopped at `2H 90'` and never showed FT.

## 3. A third of the in-match entries were bought on a phantom

6 of the 19 settled `in_match` entries were taken on a poll whose score exceeds
the final score on one side: CRB, Criciúma, Pyramids, and Seoul ×3.

- **3 lost** (Pyramids, Seoul ×2).
- **3 are booked `rule_correct = true` and were not decided markets.**
  Criciúma "0-0 → No" was bought at a real 0-0 in the 8th minute, and the
  match happened to finish 0-2. Seoul "1-1 → No" was bought at a real 0-1.
  CRB "3-0 → No" was bought at a phantom 4-0.

So the real defect rate on entries is **6/19 (32%)**, not 3/19. `rule_correct`
compares our verdict with PM's resolution; it cannot see a verdict that was
right by luck.

(Wuhan v Qingdao also reads `1H 45'` at 2-2 against a recorded HT of 2-1.
All three of its entries hold against the full-time score. Ambiguous, not
counted.)

## 4. What it would have cost

Up to $25 per entry at the observed VWAP, gross, redeemed at 1:

| phase | entries | markets / fixtures | cost | returned | P&L |
|---|--:|---|--:|--:|--:|
| `in_match` | 19 | 18 / 11 | $423.00 | $424.51 | **+$1.51 (+0.4%)**, ≈ −$2.5 after ~$4.04 fee |
| `in_match` without the 3 wrong | 16 | | $348.00 | $424.51 | +$76.51 |
| `post_whistle` | 2 | 2 / 2 | $47.01 | $52.23 | +$5.22 (+11.1%) |

Three rule errors cancelled the whole window's profit.

## 5. Price does not separate them

- Wrong entries showed 18-32pp of "edge"; correct ones showed 5-55pp.
  No `MAX_EDGE` or ask threshold removes the three without removing most of
  the rest.
- 13 of 22 `in_match` entries fire in the first minute the market reads as
  decided, which is exactly when a VAR check is still pending.

## 6. Lessons

1. **Measure `rule_correct` on `would_enter` rows.** The verdict gate
   (`rule_correct >= 0.99`) is only meaningful on the population that would be
   bought.
2. **Rule errors select themselves into entries.** When our score is wrong, the
   market is priced on the true score, and that reads as edge. It is the same
   shape as every large claimed edge in the real-money phase.
3. **The error is one-sided.** Every `in_match` rule fires on "a goal
   happened" (Over passed, BTTS completed, exact score dead). A phantom goal
   makes a rule fire wrongly; a missed goal only makes it stay silent. It
   fabricates entries and never removes them, the same shape as db/038's tape
   maximum. A wrong entry inverts the position: 0.67 paid for something worth 0.
4. **The rules are sound; the live score is not.** `post_whistle` + `halftime`
   are 0 wrong on 241,708 settled rows, because a whistle score has already
   been through VAR.
5. **The book is still the binding constraint.** ~99% of decided markets have
   no ask. There were 24 `would_enter` rows in 9 days (22 `in_match`,
   2 `post_whistle`), so n≥200 is ~75 days away, and `in_match` rows cannot
   count toward it until fixed.

## 7. Proposed fix — NOT implemented

This needs `obs_version 2`. Never pool v1 `in_match` rows with it.

- **Goal must stand ≥ N minutes** before an `in_match` rule may fire. The
  longest measured phantom was ~4 min (5 polls), so N ≥ 5. The cost is most
  current entries, which fire at minute 0 of the decided state.
- **and/or PM's own ladder must confirm the score.** This is the late-goals
  `book_confirmed` pattern: at 0-2, "O/U 1.5 Over" should be bid near 1.
  On Seoul it was asked at 0.67, so the market was saying the goal had not
  stood.
- First-half verdicts should also be checked against the half-time score.
