/** One sweep of the board, shared.
 *
 *  Building the board costs a paged Gamma sweep plus twelve CLOB round trips —
 *  about six seconds. The tape at the top of every page needs the same
 *  aggregates the table does, so without a cache a visit to /lab or /agent
 *  would pay for a full football sweep to render seven numbers.
 *
 *  This is a module-level cache, so it is per serverless instance and warms
 *  independently on each. That is fine: the worst case is the cost we already
 *  pay today, and nothing here is a write.
 */

import {
  buildFixtures,
  fetchSoccerEvents,
  rankFixtures,
  refreshBook,
  type ScoutFixture,
} from './scout'

/** How many boards get a live CLOB read on top of Gamma's cached quote.
 *
 *  Gamma already carries `bestBid`/`bestAsk`/`spread` for every market, so the
 *  base grade costs nothing and every fixture gets one. What Gamma does not
 *  give is freshness — its prices lag the CLOB, which matters most on a match
 *  in play. So the top of the card is re-read from the book itself and each
 *  card reports which source its grade came from. */
const REFRESH_TOP = 12

/** Long enough that a page load and its tape share one sweep; short enough that
 *  an in-play board is not stale by the time you look at it. */
const TTL_MS = 45_000

export interface Board {
  fixtures: ScoutFixture[]
  generatedAt: string
  refreshed: number
}

let cached: Board | null = null
let cachedAt = 0
/** Concurrent callers await the same sweep rather than each starting one. */
let inFlight: Promise<Board> | null = null

async function sweep(): Promise<Board> {
  const events = await fetchSoccerEvents()
  const { fixtures, marketsBySlug } = buildFixtures(events)
  const ranked = rankFixtures(fixtures)

  // Live boards first — a stale quote costs most where the price is moving.
  const head = ranked.filter((f) => !f.finished).slice(0, REFRESH_TOP)
  const refreshed = await Promise.allSettled(
    head.map((f) => {
      const markets = marketsBySlug.get(f.slug)
      return markets ? refreshBook(markets) : Promise.resolve(null)
    })
  )
  refreshed.forEach((r, i) => {
    // A failed round trip leaves Gamma's grade in place. A probe that could not
    // be made is not evidence of a bad book.
    if (r.status === 'fulfilled' && r.value) head[i].book = r.value
  })

  return { fixtures: ranked, generatedAt: new Date().toISOString(), refreshed: head.length }
}

export async function getBoard(): Promise<Board> {
  if (cached && Date.now() - cachedAt < TTL_MS) return cached
  if (inFlight) return inFlight

  inFlight = sweep()
    .then((board) => {
      cached = board
      cachedAt = Date.now()
      return board
    })
    .finally(() => {
      inFlight = null
    })

  try {
    return await inFlight
  } catch (e) {
    // A sweep that fails does not throw away a board we already have. Stale
    // numbers with a timestamp beat an empty page.
    if (cached) return cached
    throw e
  }
}

// ── the aggregates the tape shows ────────────────────────────────────────────

export interface Pulse {
  boards: number
  markets: number
  competitions: number
  live: number
  clean: number
  volumeUsd: number
  liquidityUsd: number
  generatedAt: string
}

export function pulseOf(board: Board): Pulse {
  const fs = board.fixtures
  return {
    boards: fs.length,
    markets: fs.reduce((s, f) => s + f.markets, 0),
    competitions: new Set(fs.map((f) => f.competition).filter(Boolean)).size,
    live: fs.filter((f) => f.live).length,
    clean: fs.filter((f) => f.book?.grade === 'clean').length,
    volumeUsd: fs.reduce((s, f) => s + f.volumeUsd, 0),
    liquidityUsd: fs.reduce((s, f) => s + f.liquidityUsd, 0),
    generatedAt: board.generatedAt,
  }
}
