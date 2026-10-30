/** The name the signed-in user's published agents carry on the Marketplace:
 *  read it, set it, or clear it back to the pseudonym. Never the email. */

import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { publicNameOf, setPublicName } from '../../../lib/agents'

export const dynamic = 'force-dynamic'

export async function GET() {
  const user = await currentUser()
  if (!user) return NextResponse.json({ ok: false, error: 'Sign in first.' }, { status: 401 })
  try {
    return NextResponse.json({ ok: true, ...(await publicNameOf(user.id)) })
  } catch (err) {
    console.error('/api/me/name GET failed', err)
    return NextResponse.json({ ok: false, error: 'Could not read your public name.' }, { status: 502 })
  }
}

export async function PATCH(request: Request) {
  const user = await currentUser()
  if (!user) return NextResponse.json({ ok: false, error: 'Sign in first.' }, { status: 401 })
  let name: string | null = null
  try {
    const body = await request.json()
    name = typeof body?.public_name === 'string' ? body.public_name : null
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid request body.' }, { status: 400 })
  }
  try {
    const r = await setPublicName(user.id, name)
    return NextResponse.json(r, { status: r.ok ? 200 : r.status })
  } catch (err) {
    console.error('/api/me/name PATCH failed', err)
    return NextResponse.json({ ok: false, error: 'Could not save your public name.' }, { status: 502 })
  }
}
