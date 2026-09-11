import { NextResponse } from 'next/server'
import { buildMatchContext, loadFixture, slugOf } from '../../../lib/matchcontext'

export const maxDuration = 30

export async function GET(request: Request) {
  const slug = slugOf(new URL(request.url).searchParams.get('slug') ?? '')
  if (!slug) return NextResponse.json({ error: 'slug is required' }, { status: 400 })

  try {
    const fx = await loadFixture(slug)
    if (!fx) return NextResponse.json({ error: `No Polymarket fixture for "${slug}"` }, { status: 404 })
    const ctx = await buildMatchContext(fx)
    return NextResponse.json(ctx)
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Unknown error' },
      { status: 500 }
    )
  }
}
