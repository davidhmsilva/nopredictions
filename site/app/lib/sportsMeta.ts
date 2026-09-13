/** The US sports boards: which sports, where they live, and the shape the page
 *  receives. Client-safe on purpose — the server half (lib/sports.ts) talks to
 *  three APIs and a cache, and a page that imported it for a label would drag
 *  all of that into its bundle.
 */

export const SPORT_KEYS = ['nfl', 'cfb', 'mlb', 'nba', 'nhl', 'wnba'] as const
export type SportKey = (typeof SPORT_KEYS)[number]

export const SPORT_META: Record<SportKey, { label: string; path: string; days: number }> = {
  nfl: { label: 'NFL', path: '/nfl', days: 9 },
  cfb: { label: 'College football', path: '/cfb', days: 7 },
  mlb: { label: 'MLB', path: '/mlb', days: 3 },
  nba: { label: 'NBA', path: '/nba', days: 7 },
  nhl: { label: 'NHL', path: '/nhl', days: 7 },
  wnba: { label: 'WNBA', path: '/wnba', days: 7 },
}

export function isSportKey(s: string): s is SportKey {
  return (SPORT_KEYS as readonly string[]).includes(s)
}

export type Venue = 'kalshi' | 'polymarket'

/** How much of a market there is behind a venue's price, on the worse of its
 *  two sides. Same philosophy as the soccer grade and the pressure review's
 *  book gates: a wide or empty book is not a market, whatever it quotes.
 *
 *    clean  spread ≤ 3¢ and at least $250 offered at the best ask, both sides
 *    thin   spread ≤ 3¢ but under $250 at the best ask on a side
 *    wide   spread over 3¢, up to 10¢
 *    none   no two-sided quote, or a spread over 10¢ — Kalshi's placeholder
 *           books (0.02 / 0.81 on every outcome) land here by construction
 */
export type BookGrade = 'clean' | 'thin' | 'wide' | 'none'

export interface VenueQuote {
  bid: number | null
  /** What buying this side costs now, per $1 of payout, BEFORE fees. */
  ask: number | null
  spread: number | null
  /** Dollars offered at the best ask. */
  askDepthUsd: number | null
}

export interface VenueLine {
  venue: Venue
  url: string
  home: VenueQuote
  away: VenueQuote
  /** Kalshi counts volume in $1 contracts; Polymarket in dollars traded. */
  volume: number | null
  grade: BookGrade
}

export interface TeamRef {
  name: string
  short: string
  abbr: string
  logo: string | null
  score: number | null
}

export interface SportGame {
  /** ESPN's event id — the schedule both venues are placed on. */
  id: string
  start: string
  state: 'pre' | 'in' | 'post'
  /** ESPN's own short status: "Q4 0:06", "Top 7th", "Final". */
  detail: string
  home: TeamRef
  away: TeamRef
  kalshi: VenueLine | null
  polymarket: VenueLine | null
  /** The venue whose ask is lower on that side, among books graded above
   *  `none` — null when only one venue quotes it or the two are level. */
  best: { home: Venue | null; away: Venue | null }
}

export interface SportBoardData {
  sport: SportKey
  games: SportGame[]
  generatedAt: string
  /** How much of each venue could be placed on the schedule. A market that
   *  cannot be placed with certainty is left out, and these say how many. */
  counts: {
    espn: number
    kalshi: number
    kalshiPlaced: number
    polymarket: number
    polymarketPlaced: number
  }
}
