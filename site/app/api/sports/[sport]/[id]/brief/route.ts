import { NextResponse } from 'next/server'
import { getSportGame } from '../../../../../lib/sportGame'
import { isSportKey } from '../../../../../lib/sportsMeta'
import { usBrief } from '../../../../../lib/usbrief'

export const maxDuration = 60
export const dynamic = 'force-dynamic'

// The same window as the soccer brief: written for a game someone might still
// act on, refused before a token is spent for anything further out or long over.
const MAX_AHEAD_H = 72
const MAX_AFTER_H = 6

export async function GET(_req: Request, { params }: { params: { sport: string; id: string } }) {
  if (!isSportKey(params.sport) || !/^\d{4,14}$/.test(params.id)) {
    return NextResponse.json({ brief: null, reason: 'No such game.' }, { status: 404 })
  }
  try {
    const g = await getSportGame(params.sport, params.id)
    const h = (new Date(g.start).getTime() - Date.now()) / 3.6e6
    if (h > MAX_AHEAD_H) {
      return NextResponse.json({ brief: null, reason: 'The brief is written from three days before the start.' })
    }
    if (h < -MAX_AFTER_H || g.state === 'post') {
      return NextResponse.json({ brief: null, reason: null })
    }
    const brief = await usBrief(g)
    return NextResponse.json({ brief })
  } catch (err) {
    console.error('us brief failed', err)
    return NextResponse.json({ brief: null, reason: 'The brief could not be written just now.' })
  }
}
