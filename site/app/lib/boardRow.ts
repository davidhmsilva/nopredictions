/** One row of a board, whatever sport it came from.
 *
 *  🔑 Football and the US sports were two different pages with two different
 *     tables, and the US one had the thing that matters most — both exchanges
 *     side by side with the cheaper one marked. This is the shape that lets
 *     ONE component render both, so a fix to the board is a fix to every
 *     board and the two cannot drift apart again.
 *
 *  The adapters are dumb on purpose. Everything interesting — which venue is
 *  cheaper, what a book is worth, how volumes add — already happened in
 *  `venues.ts`, once.
 */

import type { LiveSource, MatchPhase, ScoutFixture } from './scoutTypes'
import type { SportGame } from './sportsMeta'
import { combinedVolume, type BestPick, type OutcomeKey, type VenueBook } from './venues'

export interface BoardRow {
  /** Stable across refreshes: the Polymarket slug on football, ESPN's event id
   *  on the US sports. Also the watchlist key. */
  key: string
  /** Our own page for it, where one exists. Null means the row's only links
   *  are the two exchanges. */
  href: string | null
  /** In the order the columns are written: football "home v away", a US sport
   *  "away @ home". */
  left: string
  right: string
  leftLogo: string | null
  rightLogo: string | null
  competition: string | null
  kickoff: string | null
  live: boolean
  finished: boolean
  minute: number | null
  phase: MatchPhase | null
  liveSource: LiveSource | null
  /** ESPN's own short status on the US boards — "Q4 0:06", "Top 7th". */
  detail: string | null
  /** Written left-then-right, matching `left` and `right`. */
  score: { left: number; right: number } | null
  /** How many markets the venues list between them. Null where we do not
   *  count them — the US boards read the moneyline only. */
  markets: number | null
  best: Partial<Record<OutcomeKey, BestPick>>
  venues: VenueBook[]
  /** Both exchanges. ⚠️ Polymarket counts dollars traded and Kalshi $1
   *  contracts; close enough to RANK on, and the footer says so. */
  volume: number
}

export interface BoardColumn {
  key: OutcomeKey
  label: string
  title: string
  /** Drawn after a rule, because it is a different market from the ones to
   *  its left rather than another leg of the same one. */
  divider?: boolean
}

export const SOCCER_COLUMNS: BoardColumn[] = [
  { key: 'home', label: 'Home', title: 'Home win — the cheaper of the two exchanges, after fees' },
  { key: 'draw', label: 'Draw', title: 'The draw — the cheaper of the two exchanges, after fees' },
  { key: 'away', label: 'Away', title: 'Away win — the cheaper of the two exchanges, after fees' },
  {
    key: 'over25',
    label: 'O2.5',
    title: 'Over 2.5 goals — the cheaper of the two exchanges, after fees',
    divider: true,
  },
]

/** Away first, the way a US schedule prints a game. */
export const US_COLUMNS: BoardColumn[] = [
  { key: 'away', label: 'Away', title: 'The away side — the cheaper of the two exchanges, after fees' },
  { key: 'home', label: 'Home', title: 'The home side — the cheaper of the two exchanges, after fees' },
]

export function rowFromScout(f: ScoutFixture): BoardRow {
  return {
    key: f.slug,
    href: `/game/${f.slug}`,
    left: f.home,
    right: f.away,
    leftLogo: null,
    rightLogo: null,
    competition: f.competition,
    kickoff: f.kickoff,
    live: f.live,
    finished: f.finished,
    minute: f.minute,
    phase: f.phase,
    liveSource: f.liveSource,
    detail: null,
    score: f.score ? { left: f.score.home, right: f.score.away } : null,
    markets: f.markets,
    // Defensive: a board served from a cache written before both venues
    // existed has neither field, and a crash on the whole page is a worse
    // answer than one row with no Kalshi column.
    best: f.best ?? {},
    venues: f.venues ?? [],
    volume: f.volumeCombinedUsd ?? f.volumeUsd ?? 0,
  }
}

export function rowFromSportGame(g: SportGame, competition: string): BoardRow {
  const scored = g.state !== 'pre'
  return {
    key: g.id,
    // The US sports have no Game Center of their own yet, so the row's links
    // are the exchanges. Stated here rather than faked with a dead href.
    href: null,
    left: g.away.short,
    right: g.home.short,
    leftLogo: g.away.logo,
    rightLogo: g.home.logo,
    competition,
    kickoff: g.start,
    live: g.state === 'in',
    finished: g.state === 'post',
    minute: null,
    phase: null,
    liveSource: g.state === 'in' ? 'feed' : null,
    detail: g.detail || null,
    score: scored && g.away.score != null && g.home.score != null
      ? { left: g.away.score, right: g.home.score }
      : null,
    markets: null,
    best: g.best ?? {},
    venues: g.venues ?? [],
    volume: g.volumeCombined ?? 0,
  }
}

/** Per-venue volume off the row's books, for the footer and the tooltip that
 *  keep the two units apart. */
export function volumeByVenue(row: BoardRow): { polymarket: number | null; kalshi: number | null } {
  const of = (v: 'polymarket' | 'kalshi') => row.venues.find((b) => b.venue === v)?.volume ?? null
  return { polymarket: of('polymarket'), kalshi: of('kalshi') }
}

export function rankRows(rows: BoardRow[], liveBoost = 1.1): BoardRow[] {
  return rows.slice().sort((a, b) => {
    if (a.finished !== b.finished) return a.finished ? 1 : -1
    return b.volume * (b.live ? liveBoost : 1) - a.volume * (a.live ? liveBoost : 1)
  })
}

export { combinedVolume }
