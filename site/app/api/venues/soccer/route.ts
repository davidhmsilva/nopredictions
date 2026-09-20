/** Kalshi's football board, on its own clock.
 *
 *  🔑 Separate from `/api/scout` on purpose. Polymarket's whole board is one
 *     paged Gamma sweep and answers in well under a second warm; Kalshi's is
 *     139 separate series requests it rate-limits to about four a second, so a
 *     cold sweep is ~34 seconds. Folding that into the board's own request
 *     would make everybody wait for a column only some fixtures have.
 *
 *     So the page asks for both at once and merges in the browser. This route
 *     is allowed to be slow; the board is not.
 */

import { NextResponse } from 'next/server'
import { getKalshiSoccer } from '../../../lib/kalshiSoccer'

// A cold sweep is 139 throttled requests plus the goals ladders for whichever
// competitions are playing. It happens about four times an hour across the
// whole deployment — everything else reads the shared cache.
export const maxDuration = 60
export const revalidate = 0
export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const idx = await getKalshiSoccer()
    return NextResponse.json({ ok: true, ...idx })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Kalshi unreachable' },
      { status: 502 }
    )
  }
}
