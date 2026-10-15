import { NextResponse } from 'next/server'
import { featuredById, pickedSpec, type Picked } from '../../../lib/labQuick'
import { runSpecCached, specKey } from '../../../lib/labRun'
import type { Spec } from '../../../lib/backtest'

// The free half of the Lab: a popular test or the bet picker. No account, no
// quota — neither calls a model — and the spec is always built HERE from a
// fixed list, never taken from the request, so everything that can run is
// cacheable. See lib/labQuick.

export const maxDuration = 30

const hits = new Map<string, number[]>()
function rateLimited(ip: string): boolean {
  const now = Date.now()
  const arr = (hits.get(ip) ?? []).filter((t) => now - t < 60_000)
  arr.push(now)
  hits.set(ip, arr)
  return arr.length > 30
}

export async function POST(request: Request) {
  const ip = request.headers.get('x-forwarded-for')?.split(',')[0]?.trim() || 'unknown'
  if (rateLimited(ip)) {
    return NextResponse.json({ ok: false, error: 'Too many tests in a minute — slow down.' }, { status: 429 })
  }

  let body: { example?: unknown; pick?: Partial<Picked> }
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid request body.' }, { status: 400 })
  }

  let built: { spec: Spec; title: string } | null = null
  if (typeof body.example === 'string') {
    const f = featuredById(body.example)
    if (f) built = { spec: f.spec, title: f.title }
  } else if (body.pick) {
    built = pickedSpec({
      side: String(body.pick.side ?? '') as Picked['side'],
      league: String(body.pick.league ?? ''),
      odds: String(body.pick.odds ?? ''),
    })
  }
  if (!built) {
    return NextResponse.json({ ok: false, error: 'Unknown test.' }, { status: 400 })
  }

  try {
    const r = await runSpecCached(specKey(built.spec))
    return NextResponse.json({
      ok: true,
      supported: true,
      title: built.title,
      spec: built.spec,
      verdict: r.verdict,
      stats: r.stats,
      seasons: r.seasons,
      monthly: r.monthly,
      recent: r.recent,
      caveats: r.datasetCaveats,
    })
  } catch (err) {
    console.error('lab quick', err)
    return NextResponse.json({ ok: false, error: 'The test engine did not answer. Try again.' }, { status: 502 })
  }
}
