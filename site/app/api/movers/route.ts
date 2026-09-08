/** The dropping-odds board.
 *
 *  Shares the board cache with /api/scout and /api/pulse, so a visit here pays
 *  for no sweep of its own — and the movement itself is already in those bytes
 *  (Gamma's `oneDayPriceChange`), so there is no second source to reach for.
 */

import { NextResponse } from 'next/server'
import { getBoard } from '../../lib/scoutCache'
import { moverBoard, moversMeta, withSparklines } from '../../lib/movers'

export const revalidate = 0
export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const board = await getBoard()
    const { funded, thin } = moverBoard(board.fixtures)
    // Both boards in one call, so the two lists share the cache entry rather
    // than racing each other for the same tokens.
    const withPaths = await withSparklines([...funded, ...thin])
    return NextResponse.json({
      ok: true,
      funded: withPaths.slice(0, funded.length),
      thin: withPaths.slice(funded.length),
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
