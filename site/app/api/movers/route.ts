/** The dropping-odds board.
 *
 *  Shares the board cache with /api/scout and /api/pulse, so a visit here pays
 *  for no sweep of its own — and the movement itself is already in those bytes
 *  (Gamma's `oneDayPriceChange`), so there is no second source to reach for.
 */

import { NextResponse } from 'next/server'
import { getBoard } from '../../lib/scoutCache'
import { moverBoard, moversMeta } from '../../lib/movers'

export const revalidate = 0
export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const board = await getBoard()
    const { funded, thin } = moverBoard(board.fixtures)
    return NextResponse.json({
      ok: true,
      funded,
      thin,
      meta: moversMeta(board.fixtures, funded.length + thin.length),
      generatedAt: board.generatedAt,
    })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Polymarket unreachable' },
      { status: 502 }
    )
  }
}
