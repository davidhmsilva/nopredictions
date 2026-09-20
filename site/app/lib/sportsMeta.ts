/** The US sports boards: which sports, where they live, and the shape the page
 *  receives. Client-safe on purpose — the server half (lib/sports.ts) talks to
 *  three APIs and a cache, and a page that imported it for a label would drag
 *  all of that into its bundle.
 */

/** Venues, quotes, book grades and the best-price pick are one vocabulary
 *  across the whole site — football and the US sports read the same file, so
 *  "cheapest venue" cannot come to mean two different things on two pages. */
import type { BestPick, BookGrade, Quote, Venue, VenueBook } from './venues'

export type { BestPick, BookGrade, Quote, Venue, VenueBook }

export const SPORT_KEYS = ['nfl', 'cfb', 'mlb', 'nba', 'nhl', 'wnba'] as const
export type SportKey = (typeof SPORT_KEYS)[number]

/** `label` is the name in prose and in the page title; `tab` is what the sport
 *  bar has room for. They differ only where the full name does not fit a
 *  phone's tab strip. */
export const SPORT_META: Record<SportKey, { label: string; tab?: string; path: string; days: number }> = {
  nfl: { label: 'NFL', path: '/nfl', days: 9 },
  cfb: { label: 'College football', tab: 'NCAA', path: '/cfb', days: 7 },
  mlb: { label: 'MLB', path: '/mlb', days: 3 },
  nba: { label: 'NBA', path: '/nba', days: 7 },
  nhl: { label: 'NHL', path: '/nhl', days: 7 },
  wnba: { label: 'WNBA', path: '/wnba', days: 7 },
}

export function isSportKey(s: string): s is SportKey {
  return (SPORT_KEYS as readonly string[]).includes(s)
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
  /** Every exchange that lists the game, Polymarket first — the same shape the
   *  football board carries, so one component renders both. A game only one
   *  venue lists still appears; the row says the other is not listed. */
  venues: VenueBook[]
  /** Where to buy each side, net of each venue's taker fee. */
  best: Record<'home' | 'away', BestPick>
  /** Polymarket dollars + Kalshi contracts. What the board ranks on. */
  volumeCombined: number
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
