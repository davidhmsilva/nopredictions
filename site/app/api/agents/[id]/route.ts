/** One agent: read its record, switch it on or off, publish it, rename it, or
 *  archive it. The owner check lives in every query in lib/agents, not here. */

import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { archiveAgent, getAgent, updateAgent, type RunStatus } from '../../../lib/agents'

export const dynamic = 'force-dynamic'

const STATUSES: RunStatus[] = ['running', 'paused']

function idOf(raw: string): number | null {
  const n = Number(raw)
  return Number.isInteger(n) && n > 0 ? n : null
}

export async function GET(_request: Request, { params }: { params: { id: string } }) {
  const user = await currentUser()
  if (!user) return NextResponse.json({ ok: false, error: 'Sign in first.' }, { status: 401 })
  const id = idOf(params.id)
  if (!id) return NextResponse.json({ ok: false, error: 'No such agent.' }, { status: 404 })
  try {
    // Someone else's agent and a missing one get the same 404: an id that
    // answers differently would tell a stranger which ids exist.
    const detail = await getAgent(user.id, id)
    if (!detail) return NextResponse.json({ ok: false, error: 'No such agent.' }, { status: 404 })
    return NextResponse.json({ ok: true, ...detail })
  } catch (err) {
    console.error('/api/agents/[id] GET failed', err)
    return NextResponse.json({ ok: false, error: 'Could not load the agent.' }, { status: 502 })
  }
}

export async function PATCH(request: Request, { params }: { params: { id: string } }) {
  const user = await currentUser()
  if (!user) return NextResponse.json({ ok: false, error: 'Sign in first.' }, { status: 401 })
  const id = idOf(params.id)
  if (!id) return NextResponse.json({ ok: false, error: 'No such agent.' }, { status: 404 })

  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid request body.' }, { status: 400 })
  }
  const patch: { run_status?: RunStatus; is_public?: boolean; name?: string } = {}
  if (body.run_status !== undefined) {
    if (!STATUSES.includes(body.run_status as RunStatus)) {
      return NextResponse.json({ ok: false, error: 'run_status is running or paused.' }, { status: 400 })
    }
    patch.run_status = body.run_status as RunStatus
  }
  if (body.is_public !== undefined) patch.is_public = body.is_public === true
  if (typeof body.name === 'string') patch.name = body.name

  try {
    const r = await updateAgent(user.id, id, patch)
    return NextResponse.json(r, { status: r.ok ? 200 : r.status })
  } catch (err) {
    console.error('/api/agents PATCH failed', err)
    return NextResponse.json({ ok: false, error: 'Could not update the agent.' }, { status: 502 })
  }
}

export async function DELETE(_request: Request, { params }: { params: { id: string } }) {
  const user = await currentUser()
  if (!user) return NextResponse.json({ ok: false, error: 'Sign in first.' }, { status: 401 })
  const id = idOf(params.id)
  if (!id) return NextResponse.json({ ok: false, error: 'No such agent.' }, { status: 404 })
  try {
    const r = await archiveAgent(user.id, id)
    return NextResponse.json(r, { status: r.ok ? 200 : r.status })
  } catch (err) {
    console.error('/api/agents DELETE failed', err)
    return NextResponse.json({ ok: false, error: 'Could not archive the agent.' }, { status: 502 })
  }
}
