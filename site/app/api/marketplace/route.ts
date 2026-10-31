/** The Marketplace: every agent its owner chose to publish, best P&L first.
 *
 *  Public — no session is read, so the answer is the same for everybody and
 *  carries nothing about the viewer. What the viewer already owns or copied
 *  comes from /api/marketplace/me, a separate request only the signed-in make.
 */

import { NextResponse } from 'next/server'
import { publishedAgents } from '../../lib/marketplaceCache'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    return NextResponse.json(
      { ok: true, agents: await publishedAgents() },
      // A minute is plenty: a record moves when a game settles, not by the second.
      { headers: { 'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300' } },
    )
  } catch (err) {
    console.error('/api/marketplace GET failed', err)
    return NextResponse.json({ ok: false, error: 'Could not load the Marketplace.' }, { status: 502 })
  }
}
