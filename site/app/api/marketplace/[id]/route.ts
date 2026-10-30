/** One published agent: its rule, its backtest, its record and its bets. A
 *  private, archived or missing agent all answer the same 404. */

import { NextResponse } from 'next/server'
import { getPublished } from '../../../lib/agents'

export const dynamic = 'force-dynamic'

export async function GET(_request: Request, { params }: { params: { id: string } }) {
  const id = Number(params.id)
  if (!Number.isInteger(id) || id <= 0) {
    return NextResponse.json({ ok: false, error: 'No such agent.' }, { status: 404 })
  }
  try {
    const detail = await getPublished(id)
    if (!detail) return NextResponse.json({ ok: false, error: 'No such agent.' }, { status: 404 })
    return NextResponse.json(
      { ok: true, ...detail },
      { headers: { 'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300' } },
    )
  } catch (err) {
    console.error('/api/marketplace/[id] GET failed', err)
    return NextResponse.json({ ok: false, error: 'Could not load the agent.' }, { status: 502 })
  }
}
