/** Two exchanges, one price.
 *
 *  🔑 The whole point of carrying both venues is this file: when a market is
 *     quoted on Kalshi AND on Polymarket, we say which one is cheaper and by
 *     how much. Everything else — the boards, the Game Center, the agents —
 *     reads its answer from here, so "best odds" cannot come to mean two
 *     different things in two places.
 *
 *  ⚠️ The comparison is NET OF EACH VENUE'S TAKER FEE, and that is not a
 *     detail. Polymarket charges 0.05·p·(1−p) per share and Kalshi
 *     0.07·p·(1−p) per contract — 40% more. At an even-money price that is
 *     1.25pp against 1.75pp, so a half-cent gross edge on Kalshi is a LOSS
 *     once the fee lands. Comparing the two asks as they are printed would
 *     hand the win to Kalshi in exactly the cases where it is wrong.
 *
 *     Both numbers are kept: `ask` is what the venue prints and what the board
 *     shows, `net` is what it actually costs you, and the pick is made on
 *     `net`. The page says so rather than leaving the highlight to imply it.
 *
 *  ⚠️ A book is only allowed to win if it is a real book. A lone sell order
 *     parked at 0.99 behind an empty bid side is the cheapest quote on the
 *     card by arithmetic and is not a price — the project measured that class
 *     at −38pp (the 100-game review's 20pp+ spread bucket). `gradeOf` is the
 *     gate, and `bestOf` refuses anything graded `none`.
 *
 *  Client-safe: no fetch, no node imports. The server halves import it too.
 */

export type Venue = 'polymarket' | 'kalshi'

export const VENUES: Venue[] = ['polymarket', 'kalshi']

export const VENUE_NAME: Record<Venue, string> = {
  polymarket: 'Polymarket',
  kalshi: 'Kalshi',
}

/** For a column head, where "Polymarket" does not fit. */
export const VENUE_SHORT: Record<Venue, string> = { polymarket: 'PM', kalshi: 'Kalshi' }

/** Taker fee rate per venue: fee = rate × p × (1 − p) per $1 of payout.
 *  Polymarket's is measured across 16.5k fills on two wallets (pm_fees);
 *  Kalshi publishes `ceil(0.07 × C × p × (1−p))`, and its maker leg is 25% of
 *  that. Both are TAKER numbers: this site prices what it costs to cross the
 *  spread, because that is what a reader clicking through will do. */
export const FEE_RATE: Record<Venue, number> = { polymarket: 0.05, kalshi: 0.07 }

export function takerFee(price: number, venue: Venue): number {
  if (!Number.isFinite(price) || price <= 0 || price >= 1) return 0
  return FEE_RATE[venue] * price * (1 - price)
}

/** What a $1 payout actually costs at this venue, fee included. */
export function netCost(ask: number, venue: Venue): number {
  return ask + takerFee(ask, venue)
}

// ── a quote ──────────────────────────────────────────────────────────────────

export interface Quote {
  bid: number | null
  /** What buying this side costs now, per $1 of payout, BEFORE the fee. */
  ask: number | null
  /** ask − bid, in probability. Null when the book is not two-sided. */
  spread: number | null
  /** Dollars offered at the best ask, where the venue told us. Null is "not
   *  known", never "nothing there" — Gamma's listing quote carries no depth. */
  askDepthUsd: number | null
}

export const EMPTY_QUOTE: Quote = { bid: null, ask: null, spread: null, askDepthUsd: null }

export function quoteOf(bid: number | null, ask: number | null, askDepthUsd: number | null): Quote {
  const b = bid != null && bid > 0 && bid < 1 ? bid : null
  const a = ask != null && ask > 0 && ask < 1 ? ask : null
  return {
    bid: b,
    ask: a,
    spread: b != null && a != null ? Math.round((a - b) * 1000) / 1000 : null,
    askDepthUsd: a != null ? askDepthUsd : null,
  }
}

// ── how much of a market is behind the price ─────────────────────────────────

/**   clean  two-sided, ≤ 3¢ wide, and at least $250 at the best ask
 *    thin   two-sided and ≤ 3¢ wide, but under $250 where depth is known
 *    wide   over 3¢ and up to 10¢
 *    none   one-sided, or over 10¢ — Kalshi's placeholder books (0.02/0.81 on
 *           every outcome) and Polymarket's unfunded ladders both land here
 *
 *  🔑 The tell is the SPREAD, not the depth. CA Mineiro v EC Vitória quoted
 *     bid 0.55 / ask 0.99 behind $30,117 and traded at 0.56 two minutes later:
 *     every depth floor passes that quote. Depth only ever downgrades clean to
 *     thin; it can never rescue a wide one. */
export type BookGrade = 'clean' | 'thin' | 'wide' | 'none'

export const GRADE_LABEL: Record<BookGrade, string> = {
  clean: 'clean book',
  thin: 'thin book',
  wide: 'wide book',
  none: 'no real book',
}

export const CLEAN_SPREAD = 0.03
export const WIDE_SPREAD = 0.1
export const CLEAN_DEPTH_USD = 250

/** The grade of a ladder, read off its WORST side. A market is only as good as
 *  the leg you happen to want. Pass every outcome of the market. */
export function gradeOf(quotes: (Quote | null | undefined)[]): BookGrade {
  const qs = quotes.filter((q): q is Quote => q != null)
  if (qs.length === 0 || qs.some((q) => q.spread == null)) return 'none'
  const worst = Math.max(...qs.map((q) => q.spread as number))
  if (worst > WIDE_SPREAD + 1e-9) return 'none'
  if (worst > CLEAN_SPREAD + 1e-9) return 'wide'
  return qs.some((q) => q.askDepthUsd != null && q.askDepthUsd < CLEAN_DEPTH_USD) ? 'thin' : 'clean'
}

// ── the pick ─────────────────────────────────────────────────────────────────

/** Outside this band the market is decided and the odds stop describing a bet
 *  anyone would place, so neither venue "wins" it. */
export const TRADEABLE: [number, number] = [0.02, 0.98]

/** Half a cent gross. Below that the two asks are the same price on a 1¢ tick
 *  and calling one of them better is noise dressed as a finding. */
export const MIN_GAP = 0.005

export interface VenueQuote {
  venue: Venue
  quote: Quote
  /** The grade of the LADDER this quote belongs to, not of the leg. */
  grade: BookGrade
}

export interface BestPick {
  /** Null when only one venue quotes it, when the two are level, or when the
   *  only cheaper book is not a real book. */
  venue: Venue | null
  /** The winning venue's printed ask. */
  ask: number | null
  /** What it costs after that venue's taker fee. */
  net: number | null
  /** How much the loser's net cost is above the winner's, in probability
   *  points. This is the number the saving is worth — never the gross gap. */
  savingPp: number | null
  /** How many venues quoted a real, tradeable price for this outcome. The
   *  highlight means nothing at 1: there was nothing to be better than. */
  quoted: number
}

export const NO_PICK: BestPick = { venue: null, ask: null, net: null, savingPp: null, quoted: 0 }

function tradeable(v: VenueQuote | null | undefined): number | null {
  if (!v || v.grade === 'none') return null
  const a = v.quote.ask
  return a != null && a > TRADEABLE[0] && a < TRADEABLE[1] ? a : null
}

/** Which venue to buy this outcome at, net of each one's taker fee.
 *
 *  Refuses rather than guesses: a venue with no real book cannot win, and two
 *  prices inside half a cent of each other are one price. */
export function bestOf(quotes: (VenueQuote | null | undefined)[]): BestPick {
  const real = quotes
    .map((v) => {
      const ask = tradeable(v)
      return ask == null || !v ? null : { venue: v.venue, ask, net: netCost(ask, v.venue) }
    })
    .filter((x): x is { venue: Venue; ask: number; net: number } => x != null)

  if (real.length === 0) return NO_PICK
  const sorted = real.slice().sort((a, b) => a.net - b.net)
  const win = sorted[0]
  if (real.length === 1) {
    return { venue: null, ask: win.ask, net: win.net, savingPp: null, quoted: 1 }
  }
  const runnerUp = sorted[1]
  // Tie-break on the printed price too: a sub-tick net difference that exists
  // only because the fee curves cross is not a better price to a reader.
  if (Math.abs(win.ask - runnerUp.ask) < MIN_GAP && Math.abs(win.net - runnerUp.net) < MIN_GAP) {
    return { venue: null, ask: win.ask, net: win.net, savingPp: null, quoted: real.length }
  }
  return {
    venue: win.venue,
    ask: win.ask,
    net: win.net,
    savingPp: Math.round((runnerUp.net - win.net) * 1000) / 10,
    quoted: real.length,
  }
}

/** Volume across every venue that reported one.
 *
 *  ⚠️ The two units are not identical and the page says so: Polymarket counts
 *     dollars traded, Kalshi counts $1 contracts. They are close enough to add
 *     for a RANKING — which is all this is used for — and nowhere near close
 *     enough to quote as a dollar total without the caveat. */
export function combinedVolume(v: Partial<Record<Venue, number | null>>): number {
  return VENUES.reduce((s, k) => s + Math.max(0, v[k] ?? 0), 0)
}

// ── one exchange's side of a fixture ─────────────────────────────────────────

/** The outcomes this board prices, on both exchanges. Kalshi's soccer game
 *  series carry exactly the 1X2 and its TOTAL series the goals ladder, so
 *  these four are where a cross-venue comparison exists on football. A US
 *  sport uses `home` and `away` and leaves the other two empty — the same
 *  vocabulary, not a second one. */
export type OutcomeKey = 'home' | 'draw' | 'away' | 'over25'

export const OUTCOMES: OutcomeKey[] = ['home', 'draw', 'away', 'over25']

/** One exchange's side of a fixture. */
export interface VenueBook {
  venue: Venue
  /** Where to go and trade it. */
  url: string
  /** Polymarket counts dollars traded; Kalshi counts $1 contracts. Added only
   *  for a RANKING, never quoted as one total — see `combinedVolume`. */
  volume: number | null
  /** Read off the worst leg of the 1X2, which is the ladder both venues quote. */
  grade: BookGrade
  /** The ask/bid this venue is showing for each outcome. Missing means the
   *  venue does not quote that outcome, never that it is worthless. */
  quotes: Partial<Record<OutcomeKey, Quote>>
}

/** Where to buy each outcome, across every venue that lists the fixture. */
export function bestFor(books: VenueBook[]): Record<OutcomeKey, BestPick> {
  const out = { home: NO_PICK, draw: NO_PICK, away: NO_PICK, over25: NO_PICK }
  for (const key of OUTCOMES) {
    const quotes: VenueQuote[] = []
    for (const b of books) {
      const q = b.quotes[key]
      if (q) quotes.push({ venue: b.venue, quote: q, grade: b.grade })
    }
    out[key] = bestOf(quotes)
  }
  return out
}

