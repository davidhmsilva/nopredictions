/** One sweep of the board, shared — in two layers.
 *
 *  Building the board costs a paged Gamma sweep plus twelve CLOB round trips.
 *  Measured on production, 2026-09-08: **8.3s cold, 0.4s warm**. The tape at
 *  the top of every page needs the same aggregates the table does, so without
 *  a cache a visit to /lab or /agent would pay for a full football sweep to
 *  render seven numbers.
 *
 *  ⚠️ A module-level cache is per serverless INSTANCE. On a site with the
 *  traffic this one has, most visitors land on an instance that has never
 *  swept — so the module cache was a 0.4s number describing an 8.3s
 *  experience. The fix is not a bigger TTL; it is a cache the instances share.
 *
 *  L1 — this module. Serves repeat hits on a warm instance with no round trip
 *       at all, and coalesces concurrent callers onto one sweep.
 *  L2 — Next's Data Cache (`unstable_cache`), which on Vercel is shared across
 *       every instance and region. A cold instance reads a board someone
 *       else's request already paid for.
 *
 *  Nothing here is a write, so a stale read costs a slightly old timestamp and
 *  never anything else. Every board carries `generatedAt` and the page shows it.
 */

import { unstable_cache } from 'next/cache'
import {
  buildFixtures,
  fetchSoccerEvents,
  rankFixtures,
  refreshBook,
  refreshVenueQuotes,
  type ScoutFixture,
} from './scout'
import { fetchEspnLive } from './espn'

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
  /** How many fixtures carry a LIVE book for every price the board compares,
   *  rather than Gamma's listing quote. */
  priced: number
}

let cached: Board | null = null
let cachedAt = 0
/** Concurrent callers await the same sweep rather than each starting one. */
let inFlight: Promise<Board> | null = null

/** The sweep, behind the cache the instances share.
 *
 *  `revalidate` matches the L1 TTL so the two layers do not disagree about how
 *  old a board may be. The key is fixed because there is exactly one board —
 *  it takes no arguments, and giving it one would fragment the cache that is
 *  the entire point of this layer. */
// ⚠️ The key carries a version because the Data Cache outlives a deploy. When
//    a fixture's shape changes — as it did when both venues landed on every
//    row — a stale entry is served straight into the new renderer, and the
//    page crashes on a field that did not exist yesterday. Bump it.
const sweepShared = unstable_cache(sweep, ['scout-board-v2'], {
  revalidate: TTL_MS / 1000,
  tags: ['scout-board'],
})

async function sweep(): Promise<Board> {
  // Both in parallel: they hit unrelated hosts, and ESPN is free so its cost is
  // latency alone. A failed ESPN sweep leaves the board exactly as it was
  // before the feed existed rather than taking it down.
  const [events, espn] = await Promise.all([
    fetchSoccerEvents(),
    fetchEspnLive().catch(() => []),
  ])
  const { fixtures, marketsBySlug, legsBySlug } = buildFixtures(events, espn)

  // 🔑 Every price the board COMPARES is read from the book, not from Gamma's
  //    listing quote. Half a cent decides which exchange is called cheaper,
  //    and Gamma's ask differs from the CLOB by over 1pp on 15.3% of football
  //    markets (lib/clob carries the measurement). It costs about two requests
  //    for the whole matchday, because `POST /books` takes 400 tokens at once.
  const priced = await refreshVenueQuotes(fixtures, legsBySlug).catch(() => 0)

  // Ranked after pricing: a live book can move a fixture's grade, not its
  // volume, but the two run in the same pass and the order must be final.
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

  return {
    fixtures: ranked,
    generatedAt: new Date().toISOString(),
    refreshed: head.length,
    priced,
  }
}

export async function getBoard(): Promise<Board> {
  if (cached && Date.now() - cachedAt < TTL_MS) return cached
  if (inFlight) return inFlight

  inFlight = sweepShared()
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
