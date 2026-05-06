import { NextResponse } from 'next/server'
import {
  TARGET_SPORTS,
  SPORT_LABELS,
  vigRemove,
  sharpProbForOutcome,
  fuzzyFindEvent,
  classifyOutcome,
  extractTeamFromWinQ,
  is1x2Market,
  isTotalsMarket,
  edgeReasoning,
  type SharpEvent,
  type AnalyzedMarket,
  type ScanResult,
} from '../../lib/edge'

const ODDS_API_KEY = process.env.THE_ODDS_API_KEY || ''
const ODDS_API_BASE = 'https://api.the-odds-api.com/v4'
const GAMMA_API = 'https://gamma-api.polymarket.com'
const EDGE_THRESHOLD_PP = 3.0
const DAYS_AHEAD = 3

async function fetchJson(url: string, params?: Record<string, string>, timeout = 10000) {
  const qs = params ? '?' + new URLSearchParams(params).toString() : ''
  const res = await fetch(url + qs, { signal: AbortSignal.timeout(timeout) })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return { data: await res.json(), headers: res.headers }
}

async function fetchSharpOdds(): Promise<{ lookup: Record<string, SharpEvent>; remaining: string | null }> {
  if (!ODDS_API_KEY) return { lookup: {}, remaining: null }

  const now = new Date()
  const cutoff = new Date(now.getTime() + DAYS_AHEAD * 86400000)
  const lookup: Record<string, SharpEvent> = {}
  let remaining: string | null = null

  for (const sport of TARGET_SPORTS) {
    try {
      const { data: events, headers } = await fetchJson(`${ODDS_API_BASE}/sports/${sport}/odds`, {
        apiKey: ODDS_API_KEY,
        regions: 'eu',
        markets: 'h2h,totals',
        bookmakers: 'pinnacle,betfair_ex_eu',
        oddsFormat: 'decimal',
      })
      remaining = headers.get('x-requests-remaining')

      if (!Array.isArray(events)) continue
      for (const event of events) {
        const ct = new Date(event.commence_time)
        if (ct < now || ct > cutoff) continue
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const sharp = vigRemove(event as any)
        if (!sharp) continue
        sharp.sport = sport
        sharp.commence_time = event.commence_time

        const hn = norm(event.home_team)
        const an = norm(event.away_team)
        for (const key of [
          `${hn}_${an}`,
          `${hn.slice(0, 6)}_${an.slice(0, 6)}`,
          `${hn.slice(0, 8)}_${an.slice(0, 8)}`,
        ]) {
          lookup[key] = sharp
        }
      }
    } catch {
      continue
    }
  }

  return { lookup, remaining }
}

function norm(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, '')
    .replace(/\b(fc|cf|sc|ac|ss|afc|bsc|1\.|vfb|vfl|rb|sv|fk|sk|bv|borussia)\b/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

async function fetchPmFootballMarkets(): Promise<
  Array<{
    question: string
    yes_price: number
    event_title: string
    home: string | null
    away: string | null
    event_tags: string[]
  }>
> {
  const now = new Date()
  const cutoff = new Date(now.getTime() + DAYS_AHEAD * 86400000)

  const { data: events } = await fetchJson(`${GAMMA_API}/events`, {
    tag_slug: 'soccer',
    closed: 'false',
    active: 'true',
    limit: '200',
    order: 'volume24hr',
    ascending: 'false',
    end_date_min: now.toISOString(),
    end_date_max: cutoff.toISOString(),
  })

  if (!Array.isArray(events)) return []

  const markets: Array<{
    question: string
    yes_price: number
    event_title: string
    home: string | null
    away: string | null
    event_tags: string[]
  }> = []
  const seen = new Set<string>()

  for (const event of events) {
    const eventTitle = event.title || ''
    const endDate = event.endDate ? new Date(event.endDate) : null
    if (!endDate || endDate < now) continue

    let home: string | null = null
    let away: string | null = null
    const teamsMatch = eventTitle
      .replace(/\s+-\s+(?:More Markets|Halftime.*|Exact Score|.*Markets.*)$/i, '')
      .match(/(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s*[-:]|$)/)
    if (teamsMatch) {
      home = teamsMatch[1].trim()
      away = teamsMatch[2].trim()
    }

    const eventTags = (event.tags || [])
      .map((t: { label?: string }) => t.label || '')
      .filter((l: string) => l && !['Soccer', 'Sports', 'Games', 'sea'].includes(l))

    for (const mkt of event.markets || []) {
      if (!mkt.active || mkt.closed) continue
      const question = mkt.question || eventTitle
      const dedupKey = `${event.id}_${question}`
      if (seen.has(dedupKey)) continue
      seen.add(dedupKey)

      if (!is1x2Market(question) && !isTotalsMarket(question)) continue

      let yesPrice: number | null = null
      const raw = mkt.outcomePrices || mkt.outcome_prices
      if (raw) {
        const prices = typeof raw === 'string' ? JSON.parse(raw) : raw
        yesPrice = parseFloat(prices[0])
      }
      if (!yesPrice || yesPrice <= 0.03 || yesPrice >= 0.97) continue

      markets.push({
        question,
        yes_price: yesPrice,
        event_title: eventTitle,
        home,
        away,
        event_tags: eventTags,
      })
    }
  }

  return markets
}

export async function GET() {
  try {
    const [sharpResult, pmMarkets] = await Promise.all([fetchSharpOdds(), fetchPmFootballMarkets()])
    const { lookup, remaining } = sharpResult

    const analyzed: AnalyzedMarket[] = []
    const eventsSeen = new Set<string>()

    for (const pm of pmMarkets) {
      const home = pm.home || extractTeamFromWinQ(pm.question)
      const away = pm.away
      if (!home) continue

      const ev = fuzzyFindEvent(home, away, lookup)
      if (!ev) continue

      eventsSeen.add(`${ev.home}_${ev.away}`)

      const outcome = classifyOutcome(pm.question, ev.home, ev.away)
      if (!outcome) continue

      const sharpProb = sharpProbForOutcome(outcome.key, ev)
      if (sharpProb === null) continue

      const edgePp = (sharpProb - pm.yes_price) * 100
      const evPct = sharpProb > 0 ? ((sharpProb / pm.yes_price - 1) * 100) : 0
      const isEdge = edgePp >= EDGE_THRESHOLD_PP

      analyzed.push({
        market_title: pm.question,
        event_title: pm.event_title,
        home: ev.home,
        away: ev.away,
        sport: ev.sport,
        sport_label: SPORT_LABELS[ev.sport] || ev.sport,
        commence_time: ev.commence_time,
        outcome_key: outcome.key,
        outcome_label: outcome.label,
        pm_price: pm.yes_price,
        sharp_prob: sharpProb,
        edge_pp: Math.round(edgePp * 10) / 10,
        ev_pct: Math.round(evPct * 10) / 10,
        is_edge: isEdge,
        reasoning: edgeReasoning(
          pm.yes_price,
          sharpProb,
          edgePp,
          outcome.label,
          ev.home,
          ev.away,
          EDGE_THRESHOLD_PP
        ),
      })
    }

    analyzed.sort((a, b) => b.edge_pp - a.edge_pp)

    const result: ScanResult = {
      scanned_at: new Date().toISOString(),
      markets_analyzed: analyzed.length,
      events_analyzed: eventsSeen.size,
      edges_found: analyzed.filter((a) => a.is_edge).length,
      analyzed,
      odds_api_remaining: remaining,
    }

    return NextResponse.json(result)
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Unknown error' },
      { status: 500 }
    )
  }
}
