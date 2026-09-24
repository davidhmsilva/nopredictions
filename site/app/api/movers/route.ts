/** The dropping-odds board, every sport.
 *
 *  Shares the board caches with /api/scout and /api/sports, so a visit here
 *  pays for no sweep of its own — and the movement itself is already in those bytes
 *  (Gamma's `oneDayPriceChange`), so there is no second source to reach for.
 */

import { NextResponse } from 'next/server'
import { getBoard } from '../../lib/scoutCache'
import { getSportBoard } from '../../lib/sports'
import { SPORT_KEYS } from '../../lib/sportsMeta'
import {
  moverBoard,
  moversMeta,
  soccerCandidates,
  sportCandidates,
  withSparklines,
  type MoverCandidate,
} from '../../lib/movers'

export const revalidate = 0
export const dynamic = 'force-dynamic'
// Seven boards when every cache is cold.
export const maxDuration = 30

export async function GET() {
  try {
    // Every board the site has. A sport that fails to load costs that sport's
    // movers, not the page.
    const [board, ...sports] = await Promise.all([
      getBoard(),
      ...SPORT_KEYS.map((k) => getSportBoard(k).catch(() => null)),
    ])
    const candidates: MoverCandidate[] = [
      ...soccerCandidates(board.fixtures),
      ...sports.flatMap((b, i) => (b ? sportCandidates(SPORT_KEYS[i], b) : [])),
    ]
    const { funded, thin } = moverBoard(candidates)
    // Both boards in one call, so the two lists share the cache entry rather
    // than racing each other for the same tokens.
    const withPaths = await withSparklines([...funded, ...thin])
    return NextResponse.json({
      ok: true,
      funded: withPaths.slice(0, funded.length),
      thin: withPaths.slice(funded.length),
      meta: moversMeta(candidates, funded.length + thin.length),
      generatedAt: board.generatedAt,
    })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Polymarket unreachable' },
      { status: 502 }
    )
  }
}
