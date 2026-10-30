/** What the signed-in viewer already has to do with the Marketplace: which
 *  published agents are theirs, and which they have copied. Signed out is a
 *  200 with nothing in it — being signed out is a normal way to browse. */

import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { viewerRelations } from '../../../lib/agents'

export const dynamic = 'force-dynamic'

export async function GET() {
  const user = await currentUser()
  if (!user) return NextResponse.json({ ok: true, mine: [], copies: {} })
  try {
    return NextResponse.json({ ok: true, ...(await viewerRelations(user.id)) })
  } catch (err) {
    console.error('/api/marketplace/me failed', err)
    return NextResponse.json({ ok: false, error: 'Could not read your agents.' }, { status: 502 })
  }
}
