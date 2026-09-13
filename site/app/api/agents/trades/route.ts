/** The paper trades of the signed-in user's agents, with the pressure arms'
 *  per-entry match state. Replaces the anon-key reads the public record page
 *  made, which db/050 removes. */

import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { agentTrades } from '../../../lib/agents'

export const dynamic = 'force-dynamic'

export async function GET() {
  const user = await currentUser()
  if (!user) return NextResponse.json({ ok: false, error: 'Sign in first.' }, { status: 401 })
  try {
    return NextResponse.json({ ok: true, ...(await agentTrades(user.id)) })
  } catch (err) {
    console.error('/api/agents/trades failed', err)
    return NextResponse.json({ ok: false, error: 'Could not load the trades.' }, { status: 502 })
  }
}
