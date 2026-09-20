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
  fetchKalshi,
  fetchLive,
  fetchSiblings,
  inferBoardState,
  looksLive,
  matchTotalLine,
  pmOver25,
  takerFeePp,
  KALSHI_FEE_RATE,
  type GameData,
  type MarketGroup,
  type PricePoint,
  type LiveState,
} from '../../lib/gamecenter'
import { buildLooks, buildPulse } from '../../lib/looks'
import { pmLiveOf } from '../../lib/scout'
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

/** Polymarket's published minute and score as a LiveState. No statistics:
 *  PM carries none, and a row of zeros would read on the page as "nothing is
 *  happening". Half time has no minute in the feed, so it produces none here. */
function pmClock(main: Record<string, unknown>): LiveState | null {
  const pm = pmLiveOf(main)
  if (!pm?.live || pm.minute == null || !pm.score) return null
  return {
    minute: pm.minute,
    homeGoals: pm.score.home,
    awayGoals: pm.score.away,
    status: pm.phase ?? 'LIVE',
    clockSource: 'polymarket',
    stats: null,
  }
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
    const [af, kalshi] = await Promise.all([
      fetchLive(teams.home, teams.away),
      fetchKalshi(teams.home, teams.away, competition),
    ])

    // api-football first, because it also brings the in-game statistics.
    // Polymarket's own live block is the fallback and costs nothing: it is on
    // the event already fetched, it is the clock PM shows the trader, and it
    // covers every listed fixture by construction. Without it this page had no
    // minute at all whenever that key was refusing — which is most evenings —
    // and every measured read stayed empty.
    const live: LiveState | null = af ?? pmClock(main)

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

    const kickoff = main.startTime ? String(main.startTime) : null

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
