import { NextResponse } from 'next/server'
import { getBoard, pulseOf } from '../../lib/scoutCache'

/** The seven numbers on the tape, and nothing else.
 *
 *  The tape runs on every page, so it must not ship a 173-fixture payload to
 *  /lab just to print a count. It shares the board cache with /api/scout, so a
 *  page that renders both pays for one sweep. */
export const revalidate = 0
export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    return NextResponse.json({ ok: true, ...pulseOf(await getBoard()) })
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : 'Polymarket unreachable' },
      { status: 502 }
    )
  }
}
