import { NextResponse } from 'next/server'
import {
  buildGroups,
  buildHeadlines,
  buildMovers,
  buildPressure,
  buildWatch,
  fetchBook,
  fetchEvent,
  fetchHistory,
  fetchLive,
  fetchSiblings,
  inferBoardState,
  looksLive,
  matchTotalLine,
  pmOver25,
  type GameData,
  type MarketGroup,
  type PricePoint,
} from '../../lib/gamecenter'
import { buildLooks, buildPulse } from '../../lib/looks'
import { findKalshiFixture } from '../../lib/kalshiSoccer'
import { matchKalshiMarkets } from '../../lib/kalshiGame'
import { buildPricedLike } from '../../lib/pricedLike'

// Top-of-book is fetched for the most-traded markets only. Every extra token is
// a CLOB round trip, and a fixture board runs to 85 markets — pulling books for
// all of them would make the page slower than it is useful.
const BOOKS_FOR_TOP = 12
// Price history is one more round trip per token, and only the busiest markets
// have enough flow for 24h of it to mean anything.
const HISTORY_FOR_TOP = 6
// The pulse re-fetches at 1-minute fidelity, so it stays on the few markets a
// bettor would actually watch move.
const PULSE_FOR_TOP = 8

/** Polymarket's Over 2.5 as it stood BEFORE kick-off.
 *
 *  The fair-value table buckets on the pre-match total, so once a match is live
 *  the current quote is the wrong number — it has already absorbed the goals.
 *
 *  Polymarket's listed start time is not used to find the boundary, in either
 *  direction. It ran ~30 min EARLY on the smaller leagues that invalidated 73k
 *  of our own observations, and on Sevilla v Rayo (2026-08-15) it sat eight
 *  hours LATE while the first half was already played — so "points before the
 *  listed time" can be pure in-play trading. What is safe is the shape of the
 *  window: a football match lasts two hours, so once the board says the fixture
 *  is live, the oldest point in a 24h series is certainly pre-match. Before
 *  kick-off, the newest point is the live pre-match price. */
function preKickoffPrice(points: PricePoint[], started: boolean): number | null {
  if (!points.length) return null
  return started ? points[0].p : points[points.length - 1].p
}

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

    const kickoff = main.startTime ? String(main.startTime) : null

    const siblings = await fetchSiblings(slug, title)
    const events = siblings.length ? siblings : [main]
    const groups = buildGroups(events)

    // Books and the live state in parallel — they hit unrelated hosts.
    const [live, kalshiFixture] = await Promise.all([
      fetchLive(teams.home, teams.away),
      // Time-budgeted: a cold Kalshi index is a 34-second sweep and this page
      // must not wait for it. In practice the board keeps it warm.
      findKalshiFixture({ home: teams.home, away: teams.away, kickoff }).catch(() => null),
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

    // Kalshi's prices for the SAME markets, matched one at a time. Presented
    // as a price comparison and never as an arb: across 57 fixtures quoted on
    // both venues the net arb count was zero, the gross ceiling being one tick
    // against a ~3pp fee bar.
    //
    // ⚠️ The books are fetched BEFORE this runs, because the comparison is on
    //    what you can actually pay. A Gamma mid is not a price.
    const kalshi = kalshiFixture
      ? matchKalshiMarkets(groups, teams.home, teams.away, kalshiFixture)
      : null
    if (kalshi && kalshi.matched === 0) {
      notes.push(
        `Kalshi lists this fixture (${kalshi.eventTicker}) but none of its markets ` +
          `line up with one of Polymarket's, so no price is shown against it.`
      )
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

    // 24h of price history for the busiest markets, plus the 2.5 total whatever
    // its volume — that one is not decoration, it is what buckets the fixture.
    const traded = groups
      .map((g) => {
        const o = g.outcomes.find((x) => /^(yes|over)$/i.test(x.name) && x.tokenId)
          ?? g.outcomes.find((x) => x.tokenId)
        return o?.tokenId ? { g, o } : null
      })
      .filter((x): x is { g: MarketGroup; o: (typeof groups)[0]['outcomes'][0] } => x !== null)

    const total25 = traded.find((t) => matchTotalLine(t.g.question) === 2.5)
    const wanted = [...traded.slice(0, HISTORY_FOR_TOP)]
    if (total25 && !wanted.includes(total25)) wanted.push(total25)
    // Every full-match total, whatever its volume: the pressure reading follows
    // the rung above the current score, and which rung that is changes with
    // every goal. A ladder is at most six markets.
    for (const t of traded) {
      if (matchTotalLine(t.g.question) != null && !wanted.includes(t)) wanted.push(t)
    }

    const histories = await Promise.all(
      wanted.map(async (t) => ({
        question: t.g.question,
        outcome: t.o.name,
        tokenId: t.o.tokenId!,
        points: await fetchHistory(t.o.tokenId!),
      }))
    )

    const movers = buildMovers(histories)

    // A second, finer pass over the busiest markets. The 5-minute buckets that
    // describe a fixture are too coarse to describe the game you are watching:
    // a goal and the repricing that follows it land inside one bucket.
    const pulseSource = await Promise.all(
      wanted.slice(0, PULSE_FOR_TOP).map(async (t) => ({
        question: t.g.question,
        outcome: t.o.name,
        points: await fetchHistory(t.o.tokenId!, 1, 1),
      }))
    )
    const pulse = buildPulse(pulseSource)

    const total25History = histories.find((h) => h.tokenId === total25?.o.tokenId)

    // What the board itself says about whether this fixture has kicked off —
    // asked before the live feed rather than after it, because the feed is
    // absent far more often than it is present.
    const board = inferBoardState(groups, teams.home, teams.away)
    if (board.phase === 'unknown' && total25History && looksLive(total25History.points)) {
      // A goalless first half quotes the same rungs as a fixture that has not
      // started; only the fact that every bucket keeps moving separates them.
      board.phase = 'live'
      board.evidence = 'no rung has resolved, but the ladder moves in every 5-minute bucket'
    }

    const started = board.phase === 'live' || board.phase === 'finished'
    const preOver25 =
      (total25History ? preKickoffPrice(total25History.points, started) : null)
      ?? (started ? null : pmOver25(groups))

    const nextRung = board.goals != null
      ? histories.find((h) => matchTotalLine(h.question) === board.goals! + 0.5)
      : undefined
    const pressure = buildPressure(groups, board, nextRung?.points ?? [], preOver25)

    const headlines = buildHeadlines(groups, teams.home, teams.away)
    const pricedLike = buildPricedLike({
      groups,
      headlines,
      home: teams.home,
      away: teams.away,
      preOver25,
      started,
    })

    // The headline markets' own 24h price series, joined on the question so the
    // chart and the odds tiles above it are the same five markets. Sides were
    // already resolved by the alias-aware scorer inside buildHeadlines — doing
    // it a second time here is how the two would drift apart.
    const series = headlines
      .map((h) => {
        const hist = histories.find((x) => x.question === h.question)
        return hist && hist.points.length > 1
          ? { label: h.label, points: hist.points }
          : null
      })
      .filter((x): x is { label: string; points: PricePoint[] } => x !== null)
    const looks = buildLooks(groups, live, board, preOver25, competition)
    const watch = buildWatch(groups, live, board, competition, preOver25, movers)

    // The sparkline follows whatever the watch card is about, so the chart and
    // the number underneath it are the same market.
    const sparkFor =
      histories.find((h) => watch.market && h.question.includes(watch.market.split(' —')[0]))
      ?? total25History
      ?? histories[0]
    const history = sparkFor
      ? {
          tokenId: sparkFor.tokenId,
          label: `${sparkFor.question} — ${sparkFor.outcome}`,
          points: sparkFor.points,
        }
      : null

    const payload: GameData = {
      slug,
      title,
      home: teams.home,
      away: teams.away,
      competition,
      kickoff,
      pmUrl: `https://polymarket.com/event/${slug}`,
      live,
      board,
      pressure,
      watch,
      looks,
      pulse,
      headlines,
      series,
      movers: movers.slice(0, 3),
      groups,
      history,
      kalshi,
      notes,
      pricedLike,
    }

    return NextResponse.json(payload)
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Unknown error' },
      { status: 500 }
    )
  }
}
