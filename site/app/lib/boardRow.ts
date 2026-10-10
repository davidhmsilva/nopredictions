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
import { SPORT_META, type SportGame, type SportKey } from './sportsMeta'
import {
  combinedVolume,
  gradeOf,
  TRADEABLE,
  type BestPick,
  type OutcomeKey,
  type Quote,
  type VenueBook,
} from './venues'

/** Every board the site has: soccer, and the six US sports. */
export type BoardSport = 'soccer' | SportKey

export interface BoardRow {
  /** Stable across refreshes: the Polymarket slug on football, ESPN's event id
   *  on the US sports. Also the watchlist key. */
  key: string
  /** Which board it came from. The home page mixes every sport on one grid,
   *  so a row has to carry what its prices mean. */
  sport: BoardSport
  /** Our own page for it, where one exists. Null means the row's only links
   *  are the two exchanges. */
  href: string | null
  /** In the order the columns are written: football "home v away", a US sport
   *  "away @ home". */
  left: string
  right: string
  /** Everything a search should find this row by, lower-cased: every form of
   *  both names, the competition, and each venue's link (so a pasted
   *  Polymarket URL finds its game). */
  search: string
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
  /** A US game's main total on Polymarket — what fills the card's third line,
   *  where football has the draw. Null on football and where none is listed. */
  total: { line: number; over: Quote } | null
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
    sport: 'soccer',
    href: `/game/${f.slug}`,
    left: f.home,
    right: f.away,
    search: [f.home, f.away, f.competition, f.slug, ...(f.venues ?? []).map((v) => v.url)]
      .filter(Boolean)
      .join(' ')
      .toLowerCase(),
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
    total: null,
  }
}

export function rowFromSportGame(g: SportGame, sport: SportKey): BoardRow {
  const scored = g.state !== 'pre'
  return {
    key: g.id,
    sport,
    // The Game Center for a US game lives under its sport, keyed on ESPN's id.
    href: `/${sport}/${g.id}`,
    left: g.away.short,
    right: g.home.short,
    search: [
      g.away.name, g.away.short, g.away.abbr,
      g.home.name, g.home.short, g.home.abbr,
      SPORT_META[sport].label, sport,
      ...(g.venues ?? []).map((v) => v.url),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase(),
    competition: SPORT_META[sport].label,
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
    total: g.total ? { line: g.total.line, over: g.total.over } : null,
  }
}

/** The columns a row is priced in. Soccer is three-way plus the goals line; a
 *  US sport is the two moneylines. */
export function columnsFor(row: BoardRow): BoardColumn[] {
  return row.sport === 'soccer' ? SOCCER_COLUMNS : US_COLUMNS
}

/** What an outcome is called on a card: the team's own name rather than
 *  "Home" or "Away", which a reader has to translate back into a team. */
export function outcomeLabel(row: BoardRow, key: OutcomeKey): string {
  const homeIsLeft = row.sport === 'soccer'
  switch (key) {
    case 'home':
      return homeIsLeft ? row.left : row.right
    case 'away':
      return homeIsLeft ? row.right : row.left
    case 'draw':
      return 'Draw'
    case 'over25':
      return 'Over 2.5 goals'
  }
}

/** What a board cell shows for one outcome: a price, or a closed market.
 *
 *  🔑 Never a bare dash. A first-time visitor reads a column of "—" as a broken
 *     page (the user's call, 2026-10-10). A market with nothing fair to buy
 *     gets a lock instead, the way a sportsbook shows a suspended price, and
 *     the tooltip says why. */
export type ShownPrice = { kind: 'price'; ask: number } | { kind: 'closed'; why: string }

export const CLOSED_FT = 'Full time: this market has closed.'
export const CLOSED_NONE = 'Not offered right now.'
const CLOSED_NO_FAIR =
  'Suspended: no fair price to buy right now. The result is all but decided, or the book is too thin.'

export function shownPrice(row: BoardRow, key: OutcomeKey): ShownPrice {
  if (row.finished) return { kind: 'closed', why: CLOSED_FT }
  const pick = row.best[key]
  if (pick?.ask != null && pick.ask > 0.01 && pick.ask < 0.99) return { kind: 'price', ask: pick.ask }

  // A long price on a real book. `bestOf` stops at TRADEABLE because neither
  // venue "wins" a decided outcome. Tottenham trailing 1-0 at 80' was still a
  // two-sided 0.003/0.006 book: 167.00 is a real price, not a missing one. The
  // short end (0.98 and up) stays closed: 1.01 is not a bet anyone places.
  let ask: number | null = null
  let quoted = false
  for (const b of row.venues) {
    const q = b.quotes[key]
    if (!q) continue
    if (q.ask != null || q.bid != null) quoted = true
    if (q.ask == null || !(q.ask > 0) || q.ask >= TRADEABLE[1] || gradeOf([q]) === 'none') continue
    if (ask == null || q.ask < ask) ask = q.ask
  }
  if (ask != null) return { kind: 'price', ask }
  return { kind: 'closed', why: quoted ? CLOSED_NO_FAIR : CLOSED_NONE }
}

/** Whether a card for this row would show at least one price. A game whose
 *  every outcome is decided (or unquoted) has nothing to compare, and a card
 *  of dashes reads as a broken page. */
export function hasPrice(row: BoardRow): boolean {
  return columnsFor(row).some((c) => {
    const a = row.best[c.key]?.ask
    return a != null && a > 0.01 && a < 0.99
  })
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
