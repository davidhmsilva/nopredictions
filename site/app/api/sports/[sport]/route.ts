import { NextResponse } from 'next/server'
import { getSportBoard } from '../../../lib/sports'
import { isSportKey } from '../../../lib/sportsMeta'

// Three hosts per board (ESPN, Kalshi, Polymarket's Gamma + CLOB). A cold
// college-football sweep pages through a couple of hundred Kalshi events.
export const maxDuration = 30
export const dynamic = 'force-dynamic'

export async function GET(_req: Request, { params }: { params: { sport: string } }) {
  if (!isSportKey(params.sport)) {
    return NextResponse.json({ ok: false, error: 'Unknown sport.' }, { status: 404 })
  }
  try {
    const board = await getSportBoard(params.sport)
    return NextResponse.json({ ok: true, ...board })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Could not build the board.' },
      { status: 502 }
    )
  }
}
