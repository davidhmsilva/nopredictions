/** Copy a published agent into the signed-in user's account — `deploy: true`
 *  also switches the copy on. Asking twice returns the copy they already have
 *  (`existing: true`) rather than making a second. */

import { NextResponse } from 'next/server'
import { currentUser } from '../../../../lib/supabaseAuth'
import { copyAgent } from '../../../../lib/agents'

export const dynamic = 'force-dynamic'

export async function POST(request: Request, { params }: { params: { id: string } }) {
  const user = await currentUser()
  if (!user) return NextResponse.json({ ok: false, error: 'Sign in to copy an agent.' }, { status: 401 })

  const id = Number(params.id)
  if (!Number.isInteger(id) || id <= 0) {
    return NextResponse.json({ ok: false, error: 'That agent is not on the Marketplace.' }, { status: 404 })
  }

  let body: Record<string, unknown> = {}
  try {
    body = await request.json()
  } catch {
    // An empty body is a copy that is not deployed.
  }

  try {
    const r = await copyAgent(user.id, id, { deploy: body.deploy === true })
    return NextResponse.json(r, { status: r.ok ? (r.existing ? 200 : 201) : r.status })
  } catch (err) {
    console.error('/api/marketplace/[id]/copy failed', err)
    return NextResponse.json({ ok: false, error: 'Could not copy the agent.' }, { status: 502 })
  }
}
