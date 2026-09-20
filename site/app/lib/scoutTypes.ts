/** The board's own shapes, on their own so the browser can hold them.
 *
 *  `scout.ts` builds a board out of a Gamma sweep, api-football and the CLOB;
 *  none of that belongs in a bundle. But the cross-venue merge runs in the
 *  browser — Kalshi's sweep is far too slow to sit inside the board's request
 *  — so the TYPES and the pure functions over them live here and `scout.ts`
 *  re-exports every one of them. There is still one definition of each.
 */

import type { BestPick, OutcomeKey, VenueBook } from './venues'

export type BookGrade = 'clean' | 'wide' | 'blown' | 'one-sided' | 'settled' | 'unknown'

export interface BookQuality {
  /** Which market was graded — named so the reading is checkable. */
  market: string
  bid: number | null
  ask: number | null
  spreadPp: number | null
  askDepthUsd: number | null
  /** `clob` is a live top-of-book round trip; `gamma` is the listing's own
   *  cached quote. Gamma's prices are known to lag the CLOB, so which one a
   *  grade came from is part of the reading, not an implementation detail. */
  source: 'clob' | 'gamma'
  grade: BookGrade
}

/** How a live reading was arrived at — see `scout.ts` for what each claim is
 *  standing on, because they are different claims and the card says which. */
export type LiveSource = 'pm' | 'feed' | 'board' | 'clock'

/** The phases Polymarket names on a football event. */
export type MatchPhase = '1H' | 'HT' | '2H' | 'ET' | 'PEN'

export type Side = 'home' | 'draw' | 'away'

export interface OddsMove {
  side: Side
  /** What to print: the team name, or "Draw". */
  label: string
  /** Probability now, and 24 hours ago. */
  now: number
  before: number
  /** Change in probability POINTS. Positive = shortened = odds dropped. */
  pp: number
  /** The last hour, when Gamma carries it. Null is "not known". */
  pp1h: number | null
  /** The CLOB token for the side that shortened, so its price path can be
   *  drawn. Null when Gamma published no token for that market. */
  tokenId: string | null
}

export interface ScoutFixture {
  /** Canonical event slug — what /game/<slug> takes. */
  slug: string
  home: string
  away: string
  competition: string | null
  /** Real kick-off: `startTime`, never `startDate`. The latter is when the
   *  board was listed, which on this feed is usually the same morning — using
   *  it as kick-off returns an empty board. */
  kickoff: string | null
  live: boolean
  /** What `live` is standing on. Never null when `live` is true. */
  liveSource: LiveSource | null
  /** From the feed only. Null when nothing authoritative knows the clock. */
  minute: number | null
  /** Polymarket's period, when it gave one. Half time has no minute, so this
   *  is the only thing that separates "at the break" from "clock unknown". */
  phase: MatchPhase | null
  score: { home: number; away: number } | null
  finished: boolean
  markets: number
  volumeUsd: number
  liquidityUsd: number
  /** Probabilities. Sides are resolved with the alias-aware scorer against the
   *  fixture title, never by market order. */
  oneX2: { home: number | null; draw: number | null; away: number | null }
  over25: number | null
  // ⓘ Nothing reads these since the "Measured" board filter was retired for the
  //   Insights link (2026-09-08). They are kept because they are true, cheap,
  //   and the honest answer to "which markets does this fixture even have" —
  //   but treat them as available rather than load-bearing.
  hasTotals: boolean
  hasFirstHalf: boolean
  book: BookQuality | null
  /** Every exchange that lists this fixture, Polymarket first. A fixture only
   *  one venue lists still carries one entry — the board says "not listed"
   *  rather than hiding the game. */
  venues: VenueBook[]
  /** Where to buy each outcome, net of each venue's taker fee. `venue` is null
   *  when only one exchange quotes it or the two are level. */
  best: Record<OutcomeKey, BestPick>
  /** Polymarket + Kalshi. What the board ranks on. */
  volumeCombinedUsd: number
  /** The outcome that shortened most in the last 24 hours, if Gamma knows.
   *  Null means the change was not published or nothing shortened — see the
   *  coverage note on `Mkt.chg24h`. */
  move: OddsMove | null
}


