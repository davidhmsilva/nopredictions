import { NextResponse } from 'next/server'
import { getBoard } from '../../lib/scoutCache'

export const revalidate = 0
export const dynamic = 'force-dynamic'

/** Served from Vercel's CDN between publisher cycles, so a poll from an open
 *  tab is not a function invocation. Errors are never cached. */
const CDN_CACHE = { 'Cache-Control': 'public, s-maxage=30, stale-while-revalidate=60' }

export async function GET() {
  try {
    const board = await getBoard()
    return NextResponse.json({ ok: true, ...board }, { headers: CDN_CACHE })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Polymarket unreachable' },
      { status: 502, headers: { 'Cache-Control': 'no-store' } }
    )
  }
}
