import { NextResponse } from 'next/server'
import {
  buildFixtures,
  fetchSoccerEvents,
  rankFixtures,
  refreshBook,
} from '../../lib/scout'

/** How many boards get a live CLOB read on top of Gamma's cached quote.
 *
 *  Gamma already carries `bestBid`/`bestAsk`/`spread` for every market, so the
 *  base grade costs nothing and every fixture gets one. What Gamma does not
 *  give is freshness — its prices lag the CLOB, which matters most on a match
 *  in play. So the top of the card is re-read from the book itself and each
 *  card reports which source its grade came from. */
const REFRESH_TOP = 12

export const revalidate = 0
export const dynamic = 'force-dynamic'

export async function GET() {
  let events: Array<Record<string, unknown>>
  try {
    events = await fetchSoccerEvents()
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Polymarket unreachable' },
      { status: 502 }
    )
  }

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

  return NextResponse.json({
    ok: true,
    generatedAt: new Date().toISOString(),
    refreshed: head.length,
    fixtures: ranked,
  })
}
