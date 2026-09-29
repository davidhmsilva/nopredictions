import { NextResponse } from 'next/server'
import { listTeams } from '../../lib/teampage'
import { fold, type TeamHit } from '../../lib/teamIndex'

/** Every listed club with the spellings the search box matches against.
 *  ~400 clubs, a few kB gzipped, fetched once per page load on the first
 *  focus of the box. The list itself is cached for 12h (lib/teampage), and
 *  the CDN holds this response for as long. */
export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const teams: TeamHit[] = (await listTeams()).map((t) => ({
      i: t.id,
      n: t.name,
      l: t.league,
      t: t.tier,
      k: Array.from(new Set([t.name, ...t.spellings].map(fold).filter(Boolean))),
    }))
    return NextResponse.json(
      { teams },
      { headers: { 'Cache-Control': 'public, s-maxage=43200, stale-while-revalidate=86400' } }
    )
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Unknown error', teams: [] },
      { status: 500 }
    )
  }
}
