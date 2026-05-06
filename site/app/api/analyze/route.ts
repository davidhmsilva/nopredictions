import { NextResponse } from 'next/server'
import {
  TARGET_SPORTS,
  SPORT_LABELS,
  vigRemove,
  sharpProbForOutcome,
  classifyOutcome,
  is1x2Market,
  isTotalsMarket,
  oddsToLambda,
  poissonInplay,
  edgeReasoning,
  type SharpEvent,
  type LiveAnalysis,
} from '../../lib/edge'

const ODDS_API_KEY = process.env.THE_ODDS_API_KEY || ''
const ODDS_API_BASE = 'https://api.the-odds-api.com/v4'
const GAMMA_API = 'https://gamma-api.polymarket.com'
const EDGE_THRESHOLD_PP = 3.0

async function fetchJson(url: string, params?: Record<string, string>, timeout = 10000) {
  const qs = params ? '?' + new URLSearchParams(params).toString() : ''
  const res = await fetch(url + qs, { signal: AbortSignal.timeout(timeout) })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return { data: await res.json(), headers: res.headers }
}

function norm(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, '')
    .replace(/\b(fc|cf|sc|ac|ss|afc|bsc|1\.|vfb|vfl|rb|sv|fk|sk|bv|borussia)\b/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function slugFromUrl(url: string): string | null {
  // https://polymarket.com/event/ucl-bay-psg-2026-05-06
  // https://polymarket.com/pt/sports/ucl/ucl-bay-psg-2026-05-06
  const m = url.match(/polymarket\.com\/(?:pt\/)?(?:event|sports\/[^/]+)\/([^/?#]+)/)
  return m ? m[1] : null
}

async function fetchEventBySlug(slug: string) {
  const { data } = await fetchJson(`${GAMMA_API}/events`, {
    slug,
    closed: 'false',
    limit: '5',
  })
  if (Array.isArray(data) && data.length > 0) return data[0]

  // Try broader search — slug might be partial
  const { data: data2 } = await fetchJson(`${GAMMA_API}/events`, {
    slug,
    limit: '5',
  })
  if (Array.isArray(data2) && data2.length > 0) return data2[0]
  return null
}

async function findSharpEvent(
  home: string,
  away: string
): Promise<{ sharp: SharpEvent; score: [number, number] | null; minute: number | null; isLive: boolean } | null> {
  if (!ODDS_API_KEY) return null

  const hn = norm(home)
  const an = norm(away)
  const h6 = hn.slice(0, 6)
  const a6 = an.slice(0, 6)

  for (const sport of TARGET_SPORTS) {
    try {
      // Fetch scores to check if live
      const { data: scores } = await fetchJson(`${ODDS_API_BASE}/sports/${sport}/scores`, {
        apiKey: ODDS_API_KEY,
        daysFrom: '1',
      })

      if (!Array.isArray(scores)) continue

      for (const ev of scores) {
        const eh = norm(ev.home_team || '')
        const ea = norm(ev.away_team || '')
        const hMatch = hn.includes(eh) || eh.includes(hn) || (h6 && eh.includes(h6))
        const aMatch = an.includes(ea) || ea.includes(an) || (a6 && ea.includes(a6))
        if (!hMatch || !aMatch) continue

        const isLive = ev.completed === false && ev.scores != null
        let score: [number, number] | null = null
        let minute: number | null = null

        if (isLive && ev.scores) {
          const scoreMap: Record<string, string> = {}
          for (const s of ev.scores) scoreMap[s.name] = s.score
          const hg = parseInt(scoreMap[ev.home_team] || '0')
          const ag = parseInt(scoreMap[ev.away_team] || '0')
          score = [hg, ag]

          const commence = new Date(ev.commence_time)
          minute = Math.round((Date.now() - commence.getTime()) / 60000)
          if (minute < 0) minute = 0
          if (minute > 90) minute = 90
        }

        // Now fetch odds for this event
        const { data: oddsEvents } = await fetchJson(`${ODDS_API_BASE}/sports/${sport}/odds`, {
          apiKey: ODDS_API_KEY,
          regions: 'eu',
          markets: 'h2h,totals',
          bookmakers: 'pinnacle,betfair_ex_eu',
          oddsFormat: 'decimal',
          eventIds: ev.id,
        })

        if (Array.isArray(oddsEvents) && oddsEvents.length > 0) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const sharp = vigRemove(oddsEvents[0] as any)
          if (sharp) {
            sharp.sport = sport
            sharp.commence_time = ev.commence_time
            return { sharp, score, minute, isLive }
          }
        }

        // Fallback: return with no sharp odds but score info
        return { sharp: null as unknown as SharpEvent, score, minute, isLive }
      }
    } catch {
      continue
    }
  }

  return null
}

export async function POST(request: Request) {
  try {
    const body = await request.json()
    const url: string = body.url || ''

    if (!url || !url.includes('polymarket.com')) {
      return NextResponse.json({ error: 'Invalid Polymarket URL' }, { status: 400 })
    }

    const slug = slugFromUrl(url)
    if (!slug) {
      return NextResponse.json({ error: 'Could not extract event slug from URL' }, { status: 400 })
    }

    // 1. Fetch PM event data
    const event = await fetchEventBySlug(slug)
    if (!event) {
      // Try fetching all soccer events and matching by slug substring
      const { data: allEvents } = await fetchJson(`${GAMMA_API}/events`, {
        tag_slug: 'soccer',
        closed: 'false',
        active: 'true',
        limit: '200',
      })

      const matched = Array.isArray(allEvents)
        ? allEvents.find((e: { slug?: string }) => e.slug && (e.slug.includes(slug) || slug.includes(e.slug)))
        : null

      if (!matched) {
        return NextResponse.json({ error: `Event not found for slug: ${slug}` }, { status: 404 })
      }

      return analyzeEvent(matched)
    }

    return analyzeEvent(event)
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Unknown error' },
      { status: 500 }
    )
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function analyzeEvent(event: any) {
  const eventTitle: string = event.title || ''

  // Extract teams from event title
  const clean = eventTitle.replace(/\s+-\s+(?:More Markets|Halftime.*|Exact Score|.*Markets.*)$/i, '').trim()
  const teamsMatch = clean.match(/(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s*[-:?]|$)/)

  let home = teamsMatch ? teamsMatch[1].trim() : ''
  let away = teamsMatch ? teamsMatch[2].trim() : ''

  if (!home || !away) {
    return NextResponse.json({ error: 'Could not extract team names from event' }, { status: 400 })
  }

  // 2. Find sharp odds + live score
  const sharpResult = await findSharpEvent(home, away)

  if (sharpResult?.sharp) {
    home = sharpResult.sharp.home || home
    away = sharpResult.sharp.away || away
  }

  const isLive = sharpResult?.isLive ?? false
  const score = sharpResult?.score ?? null
  const minute = sharpResult?.minute ?? null

  // 3. Build Poisson model if live
  let poisson: Record<string, number> | null = null
  if (isLive && score && minute != null && sharpResult?.sharp) {
    const s = sharpResult.sharp
    const bestH2h = s.sources[Object.keys(s.sources)[0]]
    if (bestH2h) {
      const hOdds = bestH2h.home_odds ?? (1 / s.home_prob)
      const dOdds = bestH2h.draw_odds ?? (s.draw_prob ? 1 / s.draw_prob : 3.5)
      const aOdds = bestH2h.away_odds ?? (1 / s.away_prob)
      if (hOdds && dOdds && aOdds) {
        const [lh, la] = oddsToLambda(hOdds as number, dOdds as number, aOdds as number)
        poisson = poissonInplay(lh, la, score[0], score[1], minute)
      }
    }
  }

  // 4. Analyze each PM market
  const pmMarkets: LiveAnalysis['pm_markets'] = []
  for (const mkt of event.markets || []) {
    if (!mkt.active && !mkt.closed) continue
    const question: string = mkt.question || eventTitle

    if (!is1x2Market(question) && !isTotalsMarket(question)) continue

    let yesPrice: number | null = null
    const raw = mkt.outcomePrices || mkt.outcome_prices
    if (raw) {
      const prices = typeof raw === 'string' ? JSON.parse(raw) : raw
      yesPrice = parseFloat(prices[0])
    }
    if (!yesPrice || yesPrice <= 0.01 || yesPrice >= 0.99) continue

    const outcome = classifyOutcome(question, home, away)
    if (!outcome) continue

    let fairProb: number | null = null
    let source = ''

    // Use Poisson for live, sharp odds for pre-match
    if (poisson && isLive) {
      const poissonKey = outcome.key === 'home' ? 'home_win'
        : outcome.key === 'away' ? 'away_win'
        : outcome.key === 'draw' ? 'draw'
        : outcome.key.startsWith('over_') ? `over_${outcome.key.split('_')[1] === '2.5' ? '2_5' : '1_5'}`
        : outcome.key.startsWith('under_') ? `under_${outcome.key.split('_')[1] === '2.5' ? '2_5' : '1_5'}`
        : outcome.key
      fairProb = poisson[poissonKey] ?? null
      source = 'Poisson in-play'
    }

    if (fairProb === null && sharpResult?.sharp) {
      fairProb = sharpProbForOutcome(outcome.key, sharpResult.sharp)
      source = 'Sharp consensus'
    }

    const edgePp = fairProb != null ? (fairProb - yesPrice) * 100 : null
    const isEdge = edgePp != null && edgePp >= EDGE_THRESHOLD_PP

    let reasoning = ''
    if (fairProb != null && edgePp != null) {
      reasoning = edgeReasoning(yesPrice, fairProb, edgePp, outcome.label, home, away, EDGE_THRESHOLD_PP)
      if (source) reasoning = `[${source}] ${reasoning}`
    } else {
      reasoning = `${outcome.label}: PM=${(yesPrice * 100).toFixed(1)}% — no sharp benchmark available for comparison.`
    }

    pmMarkets.push({
      title: question,
      pm_price: yesPrice,
      fair_prob: fairProb,
      edge_pp: edgePp != null ? Math.round(edgePp * 10) / 10 : null,
      is_edge: isEdge,
      reasoning,
    })
  }

  pmMarkets.sort((a, b) => (b.edge_pp ?? -999) - (a.edge_pp ?? -999))

  const sport = sharpResult?.sharp?.sport || ''

  const result: LiveAnalysis = {
    home,
    away,
    score: score ? `${score[0]}-${score[1]}` : null,
    minute,
    is_live: isLive,
    commence_time: sharpResult?.sharp?.commence_time || event.endDate || '',
    sport,
    sport_label: SPORT_LABELS[sport] || sport || 'Football',
    sharp_odds: sharpResult?.sharp
      ? {
          home_prob: sharpResult.sharp.home_prob,
          draw_prob: sharpResult.sharp.draw_prob,
          away_prob: sharpResult.sharp.away_prob,
          totals: sharpResult.sharp.totals,
        }
      : null,
    poisson,
    pm_markets: pmMarkets,
  }

  return NextResponse.json(result)
}
