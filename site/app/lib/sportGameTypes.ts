/** One US game's page — the shape the browser receives. Client-safe: the
 *  server half (lib/sportGame.ts) talks to ESPN, Gamma and the CLOB. */

import type { SportGame, SportKey } from './sportsMeta'

export interface GameTeam {
  name: string
  short: string
  abbr: string
  logo: string | null
  /** "10-6", then the home or road record where ESPN has one. */
  record: string | null
  splitRecord: string | null
  score: number | null
  /** Points per period, where the game has started. */
  periods: number[]
}

export interface Leader {
  category: string
  player: string
  position: string | null
  value: string
}

export interface Injury {
  player: string
  position: string | null
  status: string
  detail: string | null
}

export interface RecentGame {
  result: string
  score: string
  /** "@" or "vs". */
  atVs: string
  opponent: string
  date: string | null
}

export interface StatLine {
  label: string
  away: string
  home: string
}

/** One Polymarket market beyond the moneyline: a spread, a total, a quarter,
 *  a prop. Priced at the ask, from the order book where it answered. */
export interface PmMarketRow {
  title: string
  outcomes: { label: string; ask: number | null }[]
  volume: number | null
}

export interface PmMarketGroup {
  name: string
  markets: PmMarketRow[]
}

export interface StandingsGroup {
  title: string
  cols: string[]
  rows: { team: string; side: 'home' | 'away' | null; cells: string[] }[]
}

export interface ScoringPlay {
  /** "Q2", "Top 3rd", "P1" — the sport's own name for the moment. */
  period: string
  clock: string | null
  team: string | null
  text: string
  away: number | null
  home: number | null
}

/** A price over time: seconds since epoch, probability. */
export interface PricePoint {
  t: number
  p: number
}

export interface SportGamePage {
  sport: SportKey
  id: string
  start: string
  state: 'pre' | 'in' | 'post'
  detail: string
  home: GameTeam
  away: GameTeam
  venue: string | null
  broadcasts: string[]
  /** Both exchanges' moneylines and the better one, straight off the board.
   *  Null once the game has left the board (finished, or outside its window). */
  board: SportGame | null
  /** The sportsbook line ESPN carries (DraftKings, usually). */
  sportsbook: {
    provider: string
    details: string | null
    overUnder: number | null
    homeMoneyline: number | null
    awayMoneyline: number | null
  } | null
  /** ESPN's own matchup model, in percent. Theirs, not ours. */
  predictor: { home: number; away: number } | null
  leaders: { home: Leader[]; away: Leader[] }
  injuries: { home: Injury[]; away: Injury[] }
  recent: { home: RecentGame[]; away: RecentGame[] }
  /** Season averages before the game, the game's own numbers once it starts. */
  stats: StatLine[]
  /** Every other Polymarket market on this game. */
  markets: PmMarketGroup[]
  /** Record against the spread, where ESPN has one ("3-1-0"). */
  ats: { home: string | null; away: string | null }
  /** "WSH wins series 2-1" — baseball, basketball and hockey. */
  series: string | null
  /** The division or conference each team plays in. */
  standings: StandingsGroup[]
  /** Once the game has started: every score, in order. */
  scoring: ScoringPlay[]
  /** ESPN's home win chance through the game, in percent. Empty before it. */
  winProb: number[]
  /** Polymarket's moneyline over the last three days, both sides. */
  history: { home: PricePoint[]; away: PricePoint[] }
  pmUrl: string | null
  kalshiUrl: string | null
  generatedAt: string
}
