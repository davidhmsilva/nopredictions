import { NextResponse } from 'next/server'
import { getSportGame } from '../../../../lib/sportGame'
import { isSportKey } from '../../../../lib/sportsMeta'

// ESPN's summary, the sport's board (usually cached), one Gamma event and
// one book read.
export const maxDuration = 30
export const dynamic = 'force-dynamic'

export async function GET(_req: Request, { params }: { params: { sport: string; id: string } }) {
  // ESPN event ids are digits; anything else is not a game we can look up.
  if (!isSportKey(params.sport) || !/^\d{4,14}$/.test(params.id)) {
    return NextResponse.json({ ok: false, error: 'No such game.' }, { status: 404 })
  }
  try {
    const game = await getSportGame(params.sport, params.id)
    return NextResponse.json({ ok: true, ...game })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Could not load the game.' },
      { status: 502 }
    )
  }
}
