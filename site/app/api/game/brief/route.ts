import { NextResponse } from 'next/server'
import { briefFacts, cachedBrief } from '../../../lib/matchbrief'
import { boardAtMids, buildMatchContext, loadFixture, slugOf } from '../../../lib/matchcontext'

export const maxDuration = 60

// A brief is written for a fixture someone might still act on. Anything further
// out, or long finished, is refused before a token is spent — a crawler walking
// old slugs must not be able to buy a brief per URL.
const MAX_AHEAD_H = 72
const MAX_AFTER_H = 6

export async function GET(request: Request) {
  const slug = slugOf(new URL(request.url).searchParams.get('slug') ?? '')
  if (!slug) return NextResponse.json({ error: 'slug is required' }, { status: 400 })

  try {
    const fx = await loadFixture(slug)
    if (!fx) return NextResponse.json({ error: `No Polymarket fixture for "${slug}"` }, { status: 404 })

    if (fx.kickoff) {
      const h = (new Date(fx.kickoff).getTime() - Date.now()) / 3.6e6
      if (h > MAX_AHEAD_H || h < -MAX_AFTER_H) {
        return NextResponse.json({ brief: null, reason: 'The brief is written from three days before kick-off.' })
      }
    }

    const [ctx, board] = await Promise.all([buildMatchContext(fx), boardAtMids(fx)])
    if (!ctx.teams?.home && !ctx.teams?.away && !board.pricedLike.totals) {
      return NextResponse.json({
        brief: null,
        reason: 'Neither team is in our database and the board has no total to price from — there is nothing measured to write about.',
      })
    }

    // Started is the board's word OR ESPN's: either one seeing a live match is
    // enough to withhold prices from the brief.
    const started = board.started || ctx.espn?.state === 'in' || ctx.espn?.state === 'post'
    const facts = briefFacts(fx, ctx, board.headlines, board.pricedLike, started)
    const brief = await cachedBrief(slug, !!ctx.espn?.lineupsConfirmed, started, facts)
    return NextResponse.json({ brief })
  } catch (err) {
    console.error('brief failed', err)
    return NextResponse.json({ brief: null, reason: 'The brief could not be written just now.' })
  }
}
