import { NextResponse } from 'next/server'
import {
  buildGroups,
  fetchBook,
  fetchEvent,
  fetchHistory,
  fetchKalshi,
  fetchLive,
  fetchSiblings,
  takerFeePp,
  KALSHI_FEE_RATE,
  type GameData,
  type MarketGroup,
} from '../../lib/gamecenter'

// Top-of-book is fetched for the most-traded markets only. Every extra token is
// a CLOB round trip, and a fixture board runs to 85 markets — pulling books for
// all of them would make the page slower than it is useful.
const BOOKS_FOR_TOP = 12

function extractTeams(title: string): { home: string; away: string } | null {
  const clean = title.replace(/\s+-\s+.*$/, '').trim()
  const m = clean.match(/^(.+?)\s+vs\.?\s+(.+?)$/i)
  return m ? { home: m[1].trim(), away: m[2].trim() } : null
}

export async function GET(request: Request) {
  const url = new URL(request.url)
  const slugParam = url.searchParams.get('slug') ?? ''
  // Accept a pasted Polymarket URL as well as a bare slug — that is how people
  // actually arrive at a fixture.
  const slug =
    slugParam.match(/polymarket\.com\/(?:[a-z]{2}\/)?(?:event|sports\/[^/]+)\/([^/?#]+)/)?.[1] ??
    slugParam.trim()

  if (!slug) {
    return NextResponse.json({ error: 'slug is required' }, { status: 400 })
  }

  try {
    const main = await fetchEvent(slug)
    if (!main) {
      return NextResponse.json({ error: `No open Polymarket event for "${slug}"` }, { status: 404 })
    }

    const title = String(main.title ?? '')
    const teams = extractTeams(title)
    if (!teams) {
      return NextResponse.json(
        { error: `Could not read team names from "${title}"` },
        { status: 422 }
      )
    }

    const competition =
      typeof main.sport === 'object' && main.sport
        ? String((main.sport as Record<string, unknown>).name ?? '')
        : null

    const siblings = await fetchSiblings(slug, title)
    const events = siblings.length ? siblings : [main]
    const groups = buildGroups(events)

    // Books and the live state in parallel — they hit unrelated hosts.
    const [live, kalshi] = await Promise.all([
      fetchLive(teams.home, teams.away),
      fetchKalshi(teams.home, teams.away, competition),
    ])

    // Executable prices for the headline markets. A Gamma mid is not a price you
    // can trade; presenting it as one is how a paper strategy books +141% that
    // turns into +3.4% on the same decisions.
    const ranked = groups
      .map((g, i) => ({ g, i }))
      .sort((a, b) => (b.g.volume ?? 0) - (a.g.volume ?? 0))
      .slice(0, BOOKS_FOR_TOP)

    await Promise.all(
      ranked.map(async ({ g }) => {
        await Promise.all(
          g.outcomes.map(async (o) => {
            if (o.tokenId) o.book = await fetchBook(o.tokenId)
          })
        )
      })
    )

    const notes: string[] = []

    // Kalshi comparison, net of BOTH venues' taker fees. Presented as a price
    // comparison and never as an arb: across 57 fixtures quoted on both venues
    // the net arb count was zero, the gross ceiling being one tick against a
    // ~3pp fee bar.
    if (kalshi) {
      // Polymarket splits 1X2 into three binary markets whose OUTCOMES are just
      // "Yes"/"No" — the team lives in the question ("Will Arsenal FC win on
      // ...?"). Matching Kalshi's side names against the outcome names finds
      // nothing; the question is what has to be read.
      let best: number | null = null
      for (const g of groups.filter((x) => x.group === 'Match result')) {
        const yes = g.outcomes.find((o) => /^yes$/i.test(o.name))
        const pmAsk = yes?.book?.ask ?? yes?.price
        if (pmAsk == null) continue

        const isDraw = /draw|tie/i.test(g.question)
        const k = kalshi.sides.find((s) => {
          if (isDraw) return /tie|draw/i.test(s.name)
          const key = s.name.toLowerCase().split(' ')[0]
          return key.length > 2 && g.question.toLowerCase().includes(key)
        })
        if (!k?.ask) continue

        const gross = 100 * Math.abs(pmAsk - k.ask)
        const net = gross - takerFeePp(pmAsk) - takerFeePp(k.ask, KALSHI_FEE_RATE)
        if (best == null || net > best) best = net
      }
      kalshi.bestNetPp = best
      if (best != null && best < 0) {
        notes.push(
          `Best cross-venue difference is ${best.toFixed(2)}pp AFTER both taker fees — ` +
            `i.e. negative. Consistent with the 57-fixture study that found zero net arbs.`
        )
      }
    }

    if (!live) {
      notes.push(
        process.env.FOOTBALL_API_KEY
          ? 'No live feed matched this fixture — clock and score unverified.'
          : 'FOOTBALL_API_KEY not set on this deployment, so no live clock or stats.'
      )
    } else if (!live.stats) {
      notes.push('api-football has no in-game statistics coverage for this competition.')
    }

    // The sparkline follows the busiest outcome of the busiest market.
    const headline: MarketGroup | undefined = ranked[0]?.g
    const headlineOutcome = headline?.outcomes.find((o) => o.tokenId)
    const history = headlineOutcome?.tokenId
      ? {
          tokenId: headlineOutcome.tokenId,
          label: `${headline!.question} — ${headlineOutcome.name}`,
          points: await fetchHistory(headlineOutcome.tokenId),
        }
      : null

    const payload: GameData = {
      slug,
      title,
      home: teams.home,
      away: teams.away,
      competition,
      kickoff: main.startTime ? String(main.startTime) : null,
      pmUrl: `https://polymarket.com/event/${slug}`,
      live,
      groups,
      history,
      kalshi,
      notes,
    }

    return NextResponse.json(payload)
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Unknown error' },
      { status: 500 }
    )
  }
}
