/** The signed-in user's agents: list them, or save a new one from the Lab.
 *
 *  Signed out is a 401 here, unlike /api/me — there is nothing to show someone
 *  who has no agents, and the page turns the 401 into a sign-in prompt.
 */

import { NextResponse } from 'next/server'
import { currentUser } from '../../lib/supabaseAuth'
import { listAgents, saveLabAgent } from '../../lib/agents'

export const dynamic = 'force-dynamic'
export const maxDuration = 60

const SIGNED_OUT = { ok: false, error: 'Sign in to see your agents.' }

export async function GET() {
  const user = await currentUser()
  if (!user) return NextResponse.json(SIGNED_OUT, { status: 401 })
  try {
    return NextResponse.json({ ok: true, ...(await listAgents(user.id)) })
  } catch (err) {
    console.error('/api/agents GET failed', err)
    return NextResponse.json({ ok: false, error: 'Could not load your agents.' }, { status: 502 })
  }
}

export async function POST(request: Request) {
  const user = await currentUser()
  if (!user) return NextResponse.json(SIGNED_OUT, { status: 401 })

  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid request body.' }, { status: 400 })
  }
  const hypothesis = String(body.hypothesis ?? '')
  const interpretation = String(body.interpretation ?? '')
  if (!hypothesis.trim() || hypothesis.length > 500 || interpretation.length > 500) {
    return NextResponse.json({ ok: false, error: 'A theory of 1-500 characters is required.' }, { status: 400 })
  }

  try {
    const r = await saveLabAgent(user.id, { hypothesis, interpretation, spec: body.spec })
    return NextResponse.json(r, { status: r.ok ? 201 : r.status })
  } catch (err) {
    console.error('/api/agents POST failed', err)
    return NextResponse.json({ ok: false, error: 'Could not save the agent.' }, { status: 502 })
  }
}
