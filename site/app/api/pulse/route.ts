import { NextResponse } from 'next/server'
import { getBoard, pulseOf } from '../../lib/scoutCache'

/** The seven numbers on the tape, and nothing else.
 *
 *  The tape runs on every page, so it must not ship a 173-fixture payload to
 *  /lab just to print a count. It shares the board cache with /api/scout, so a
 *  page that renders both pays for one sweep. */
export const revalidate = 0
export const dynamic = 'force-dynamic'

/** Served from Vercel's CDN between publisher cycles, so a poll from an open
 *  tab is not a function invocation. Errors are never cached. */
const CDN_CACHE = { 'Cache-Control': 'public, s-maxage=30, stale-while-revalidate=60' }

export async function GET() {
  try {
    return NextResponse.json({ ok: true, ...pulseOf(await getBoard()) }, { headers: CDN_CACHE })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Polymarket unreachable' },
      { status: 502, headers: { 'Cache-Control': 'no-store' } }
    )
  }
}
