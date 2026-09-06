import { NextResponse } from 'next/server'
import { getBoard } from '../../lib/scoutCache'

export const revalidate = 0
export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const board = await getBoard()
    return NextResponse.json({ ok: true, ...board })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Polymarket unreachable' },
      { status: 502 }
    )
  }
}
