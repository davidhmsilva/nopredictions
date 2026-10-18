# Pre-Match Drift — the paper trades now pay the spread (2026-10-16)

Strategy 40, `drift_positions`, `agent/drift_scanner.py` + `agent/drift_monitor.py`.
Paper only. `pm_live` is untouched; no money was or is placed.

## What was wrong

| leg | booked at | what it actually is | executable price |
|---|---|---|---|
| entry | `1 − Gamma YES bestAsk` | the **NO bid** | NO ask = 1 − YES bid |
| exit | `1 − CLOB /price?side=BUY` (= YES bid, checked against `/book`) | the **NO ask** | NO bid = 1 − YES ask |

Every position bought at the bid and sold at the ask. A +10% target on a draw
book quoted 0.65 / 0.78 fires on the spread alone, with no price move.

Also: the open-only unique index (db/021) let a token re-enter on every
10-minute scan once its last position closed (up to **129 positions on one
token**, 27.6 per token in October), and each log line was written twice
(a `FileHandler` on the same file the scheduler already appends stdout to).

## Before — the record as booked (all 1,811 closed positions)

| | |
|---|---|
| counted (non-void) | 1,776 · 1,764 won / 12 lost |
| P&L | +380.62u on 1,776u, **yield +21.4%** |
| `target` exits | 1,698, **1,633 of them within 20 minutes of entry** (median hold 10 min), mean NO "move" +14.1c |
| `kickoff` exits | 75, median hold 39h, mean move +1.9c |
| distinct tokens | 268 |

The 10-minute target exits are the spread, not drift: the monitor's first look
at the position read the other side of the same book.

## After — what the same trades are worth at executable prices

The opposite side of the book was never stored, so the old rows cannot be
re-priced one by one. What can be said:

- **Mid-to-mid, no costs** (the audit, Polymarket price history, 253 unique
  tokens): **+0.8% CI[−0.3, +2.0]**; Jun–Aug +0.1%; positions held > 4h −0.5%.
- **Round-trip spread at entry**: median **9.5% of the NO price** — measured
  twice, on the audit's open positions and again on the 18 positions today's
  dry run would open (1.3% to 40.6%; half the books are 9%+).
- So the corrected expectation is roughly **+0.8% − 9.5% ≈ −9% per round trip**
  at the median book. No edge is demonstrated; this is not a case for money.

## What changed

- **db/076** (applied 2026-10-16): `price_convention` (default
  `'yes_side_inverted'`, so every old row and anything the not-yet-updated
  server writes is flagged; only the new code writes `'executable'`),
  `no_token_id`, entry YES/NO bid & ask, `entry_no_ask_size`,
  `entry_no_depth_usd`, exit NO bid/ask/bid size, and a unique index of one
  executable position per token. Nothing deleted: all 1,831 existing rows are
  `yes_side_inverted`.
- **`agent/drift_book.py`**: one POST `/books` for both tokens; sorts levels
  (the API lists the best level last); walks the ask ladder to buy the stake and
  the bid ladder to sell the shares; NO book read directly, YES book mirrored as
  the fallback for old rows.
- **Scanner**: same signal (Gamma YES ask vs DC fair), but buys at the NO ask
  for the whole $1 (refuses if the ladder cannot fill it), records the book,
  and never buys a token the agent has held before.
- **Monitor**: target/stop/close are judged on, and exit at, the NO sale price.
- **Site** (`lib/agents.ts`, `AgentView`): tiles, spark, curve and the positions
  list count `executable` rows only; the page says how many earlier positions
  were left out and why.
- **Tests**: `agent/tests/test_drift_execution.py` (6), on the real 0.22/0.35 ·
  0.65/0.78 book of Middlesbrough v Cardiff draw: entry pays 0.78, exit
  receives 0.65, an unchanged book is a −16.7% stop, not a +20% target.

Dry runs on the live board (2026-10-16 22:40Z): the monitor marked all 20 open
positions at the NO bid (e.g. #1826 Southampton v Bolton draw: "paid" 0.65,
bid 0.65, ask 0.78 — the old code would have exited it at 0.78 as a target);
the scanner, with the known-token rule switched off for the demo, would buy
Middlesbrough v Cardiff draw at **0.78** (signal YES 0.35, only $4 at the top).

## Decisions left for the operator

1. **With executable prices, a −5% stop fires on the first check of any book
   wider than 5% of the price** — 11 of the 18 entries in today's dry run. The corrected
   agent will therefore log a run of immediate stops. A max-spread gate at entry
   (e.g. ≤ 2–3c) is the obvious fix, but it changes the strategy, so it was not
   made here.
2. The edge gate still reads Gamma's YES **ask**. On a 0.04 / 0.43 book
   (Brighton v Crystal Palace draw) it buys NO at 0.96 against a DC fair of 0.75
   and sets a target of 1.056. Gating on the executable price
   (`entry_yes_bid − dc_fair`) is the same kind of decision.
3. **Deploy**: the server (`claude/sharp-gates-ua7ro2` on Hetzner) still runs the
   old code; its new rows land as `yes_side_inverted` and stay out of the site.
   The site change needs a deploy too.

## Follow-up, 2026-10-19: the two open decisions, made

Both decisions above were taken as recommended (db/077, `drift_scanner.entry_gate` / `exit_levels`):

1. **Book gates at entry.** The NO book must be two-sided, ≤ 3c wide, and hold ≥ $25 at the ask, the same thresholds the in-play arms use. The stop is now 5% below the **NO bid at entry**, not below the price paid, so the spread paid on the way in is no longer read as a move against the position.
2. **Edge on the executable price.** `exec_edge_pp = (1 − NO paid − DC fair) × 100 ≥ 8`. Gamma's YES ask is only a pre-filter, since it can only overstate the edge. `edge_pp` keeps its signal meaning.

Dry run on the live board, 2026-10-19 00:20Z (known-token rule off):
- **Entered:** Blackpool v MK Dons (NO 0.60/0.62, +10.4pp executable) and Walsall v Oldham (0.59/0.61, +10.6pp).
- **Refused:** AFC Wimbledon v Mansfield (+9.0pp on Gamma, +7.0pp executable) and Liverpool v Villarreal draw (9c spread).

No executable rows existed before the gates, so no sample mixes the two rule sets.
