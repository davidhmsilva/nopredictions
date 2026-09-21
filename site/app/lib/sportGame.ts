/** One US game's page: the board's two moneylines, everything else Polymarket
 *  lists on the game, and ESPN's matchup file.
 *
 *  🔑 The moneyline comparison is NOT recomputed here. It is the board's own
 *     row (lib/sports.ts), so the price on this page and the price on the
 *     board it was clicked from cannot disagree.
 *
 *  ⚠️ ESPN: do not set a User-Agent. Its edge serves library defaults and 403s
 *     browser-shaped and custom agents (measured, see CLAUDE.md).
 *
 *  ⚠️ Polymarket's other markets (spreads, totals, quarters, props) are priced
 *     from the CLOB book, not Gamma's listing quote — measured across 810
 *     soccer markets, Gamma's ask is more than 1pp off the book on 15% of them.
 *     Kalshi lists these families too, under their own series; they are not
 *     compared here yet, and the page says the markets are Polymarket's.
 */

import { unstable_cache } from 'next/cache'
import { espnPathOf, getSportBoard } from './sports'
import type { SportKey } from './sportsMeta'
import type {
  GameTeam,
  Injury,
  Leader,
  PmMarketGroup,
  PmMarketRow,
  RecentGame,
  SportGamePage,
  StatLine,
} from './sportGameTypes'

const TIMEOUT_MS = 12_000

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  try {
    const r = await fetch(url, { ...init, signal: ctrl.signal, cache: 'no-store' })
    if (!r.ok) throw new Error(`${new URL(url).host} ${r.status}`)
    return (await r.json()) as T
  } finally {
    clearTimeout(t)
  }
}

const num = (x: unknown): number | null => {
  const n = typeof x === 'number' ? x : Number(x)
  return x == null || x === '' || !Number.isFinite(n) ? null : n
}

// ── ESPN ─────────────────────────────────────────────────────────────────────

/* eslint-disable @typescript-eslint/no-explicit-any */

function team(c: any): GameTeam {
  const t = c?.team ?? {}
  const recs: any[] = c?.record ?? []
  const total = recs.find((r) => r.type === 'total') ?? recs[0]
  const split = recs.find((r) => r.type === (c?.homeAway === 'home' ? 'home' : 'road'))
  return {
    name: t.displayName ?? t.name ?? '',
    short: t.shortDisplayName ?? t.name ?? '',
    abbr: t.abbreviation ?? '',
    logo: t.logos?.[0]?.href ?? t.logo ?? null,
    record: total?.summary ?? null,
    splitRecord: split?.summary ? `${split.summary} ${c?.homeAway === 'home' ? 'home' : 'away'}` : null,
    score: num(c?.score),
    periods: (c?.linescores ?? []).map((l: any) => num(l.displayValue ?? l.value) ?? 0),
  }
}

/** The ESPN side ids, so each team-keyed list lands on the right side. */
function sideOf(teamId: unknown, ids: { home: string; away: string }): 'home' | 'away' | null {
  const id = String(teamId ?? '')
  return id === ids.home ? 'home' : id === ids.away ? 'away' : null
}

function leadersOf(block: any): Leader[] {
  return (block?.leaders ?? [])
    .map((cat: any) => {
      const top = cat?.leaders?.[0]
      if (!top?.athlete) return null
      return {
        category: cat.displayName ?? cat.name ?? '',
        player: top.athlete.displayName ?? top.athlete.shortName ?? '',
        position: top.athlete.position?.abbreviation ?? null,
        value: top.displayValue ?? '',
      }
    })
    .filter((x: Leader | null): x is Leader => x != null)
    .slice(0, 4)
}

function injuriesOf(block: any): Injury[] {
  return (block?.injuries ?? [])
    .map((x: any) => ({
      player: x.athlete?.displayName ?? '',
      position: x.athlete?.position?.abbreviation ?? null,
      status: x.status ?? x.type?.description ?? '',
      detail: x.details?.type ?? null,
    }))
    .filter((x: Injury) => x.player)
    .slice(0, 8)
}

function recentOf(block: any): RecentGame[] {
  return (block?.events ?? [])
    .map((e: any) => ({
      result: e.gameResult ?? '',
      score: e.score ?? '',
      atVs: e.atVs ?? '',
      opponent: e.opponent?.abbreviation ?? e.opponent?.displayName ?? '',
      date: e.gameDate ?? null,
    }))
    .slice(-5)
    .reverse()
}

function statsOf(box: any, ids: { home: string; away: string }): StatLine[] {
  const teams: any[] = box?.teams ?? []
  const home = teams.find((t) => String(t.team?.id) === ids.home)
  const away = teams.find((t) => String(t.team?.id) === ids.away)
  if (!home || !away) return []
  const awayBy = new Map<string, string>(
    (away.statistics ?? []).map((s: any) => [s.name ?? s.label, s.displayValue ?? ''])
  )
  return (home.statistics ?? [])
    .map((s: any) => ({
      label: s.label ?? s.name ?? '',
      home: s.displayValue ?? '',
      away: awayBy.get(s.name ?? s.label) ?? '',
    }))
    .filter((s: StatLine) => s.label && (s.home || s.away))
    .slice(0, 14)
}

// ── Polymarket: every other market on the game ───────────────────────────────

interface PmMarket {
  question?: string
  groupItemTitle?: string
  sportsMarketType?: string
  outcomes?: string
  clobTokenIds?: string
  volumeNum?: number | string
  closed?: boolean
  bestAsk?: number | string
  bestBid?: number | string
}

/** Which heading a Polymarket market family goes under. The moneyline itself
 *  is shown against Kalshi above, so it is left out here. */
function groupOf(type: string): string | null {
  const t = type.toLowerCase()
  if (t === 'moneyline') return null
  if (/(^|_)(q\d|first_half|second_half|half|period|inning|quarter)/.test(t)) return 'Halves & periods'
  if (t === 'spreads') return 'Spread'
  if (t === 'totals') return 'Total'
  return 'Props'
}

const GROUP_ORDER = ['Spread', 'Total', 'Halves & periods', 'Props']

async function pmMarkets(slug: string): Promise<PmMarketGroup[]> {
  const events = await getJson<any[]>(
    `https://gamma-api.polymarket.com/events?slug=${encodeURIComponent(slug)}`
  )
  const markets: PmMarket[] = (events?.[0]?.markets ?? []).filter((m: PmMarket) => !m.closed)

  const parsed = markets
    .map((m) => {
      const group = groupOf(m.sportsMarketType ?? '')
      if (!group) return null
      let outcomes: string[]
      let tokens: string[]
      try {
        outcomes = JSON.parse(m.outcomes ?? '[]')
        tokens = JSON.parse(m.clobTokenIds ?? '[]')
      } catch {
        return null
      }
      if (outcomes.length !== 2 || tokens.length !== 2) return null
      return { m, group, outcomes, tokens }
    })
    .filter((x): x is NonNullable<typeof x> => x != null)

  // The whole game's book in one request. A failed read falls back to Gamma's
  // listing quote: outcome 0 at bestAsk, outcome 1 at 1 − bestBid.
  const asks = new Map<string, number | null>()
  const tokens = parsed.flatMap((p) => p.tokens)
  if (tokens.length) {
    try {
      const books = await getJson<{ asset_id: string; asks?: { price: string; size: string }[] }[]>(
        'https://clob.polymarket.com/books',
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(tokens.slice(0, 400).map((token_id) => ({ token_id }))),
        }
      )
      for (const b of books) {
        let ask: number | null = null
        for (const l of b.asks ?? []) {
          const p = Number(l.price)
          if (Number(l.size) > 0 && (ask == null || p < ask)) ask = p
        }
        asks.set(b.asset_id, ask)
      }
    } catch {
      /* Gamma below */
    }
  }

  const groups = new Map<string, PmMarketRow[]>()
  for (const p of parsed) {
    const ba = num(p.m.bestAsk)
    const bb = num(p.m.bestBid)
    const fallback = [ba, bb != null ? 1 - bb : null]
    const row: PmMarketRow = {
      title: (p.m.question ?? '').replace(/^.*?:\s*/, '') || p.m.groupItemTitle || '',
      outcomes: p.outcomes.map((label, i) => ({
        label,
        ask: asks.has(p.tokens[i]) ? (asks.get(p.tokens[i]) ?? null) : fallback[i],
      })),
      volume: num(p.m.volumeNum),
    }
    // A market nobody is offering on either side says nothing.
    if (row.outcomes.every((o) => o.ask == null || o.ask >= 0.99)) continue
    const list = groups.get(p.group) ?? []
    list.push(row)
    groups.set(p.group, list)
  }

  return GROUP_ORDER.filter((g) => groups.has(g)).map((name) => ({
    name,
    // Most traded first: that is the line people actually use.
    markets: (groups.get(name) ?? []).sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0)),
  }))
}

// ── the page ─────────────────────────────────────────────────────────────────

async function build(sport: SportKey, id: string): Promise<SportGamePage> {
  const [summary, board] = await Promise.all([
    getJson<any>(`https://site.api.espn.com/apis/site/v2/sports/${espnPathOf(sport)}/summary?event=${encodeURIComponent(id)}`),
    getSportBoard(sport).catch(() => null),
  ])

  const comp = summary?.header?.competitions?.[0]
  const cs: any[] = comp?.competitors ?? []
  const h = cs.find((c) => c.homeAway === 'home')
  const a = cs.find((c) => c.homeAway === 'away')
  if (!comp || !h || !a) throw new Error('ESPN has no game with that id')
  const ids = { home: String(h.team?.id ?? h.id), away: String(a.team?.id ?? a.id) }

  const row = board?.games.find((g) => g.id === id) ?? null
  const pmBook = row?.venues.find((v) => v.venue === 'polymarket') ?? null
  const kalshiBook = row?.venues.find((v) => v.venue === 'kalshi') ?? null
  const pmSlug = pmBook?.url.match(/\/event\/([^/?#]+)/)?.[1] ?? null
  const markets = pmSlug ? await pmMarkets(pmSlug).catch(() => []) : []

  const byTeam = <T,>(list: any[] | undefined, pick: (x: any) => T[]): { home: T[]; away: T[] } => {
    const out = { home: [] as T[], away: [] as T[] }
    for (const x of list ?? []) {
      const s = sideOf(x?.team?.id, ids)
      if (s) out[s] = pick(x)
    }
    return out
  }

  const st = comp.status?.type ?? {}
  const pick = (summary.pickcenter ?? [])[0]
  const pred = summary.predictor
  const ph = num(pred?.homeTeam?.gameProjection)
  const pa = num(pred?.awayTeam?.gameProjection)
  const addr = summary.gameInfo?.venue?.address

  return {
    sport,
    id,
    start: comp.date ? new Date(comp.date).toISOString() : (row?.start ?? new Date().toISOString()),
    state: st.state === 'in' ? 'in' : st.state === 'post' ? 'post' : 'pre',
    detail: st.shortDetail ?? '',
    home: team(h),
    away: team(a),
    venue: summary.gameInfo?.venue?.fullName
      ? `${summary.gameInfo.venue.fullName}${addr?.city ? `, ${addr.city}${addr.state ? `, ${addr.state}` : ''}` : ''}`
      : null,
    broadcasts: Array.from(
      new Set<string>(
        (summary.broadcasts ?? [])
          .map((b: any) => b.media?.shortName ?? b.station ?? '')
          .filter(Boolean)
      )
    ).slice(0, 3),
    board: row,
    sportsbook: pick
      ? {
          provider: pick.provider?.name ?? 'Sportsbook',
          details: pick.details ?? null,
          overUnder: num(pick.overUnder),
          homeMoneyline: num(pick.homeTeamOdds?.moneyLine),
          awayMoneyline: num(pick.awayTeamOdds?.moneyLine),
        }
      : null,
    predictor: ph != null && pa != null ? { home: ph, away: pa } : null,
    leaders: byTeam(summary.leaders, leadersOf),
    injuries: byTeam(summary.injuries, injuriesOf),
    recent: byTeam(summary.lastFiveGames, recentOf),
    stats: statsOf(summary.boxscore, ids),
    markets,
    pmUrl: pmBook?.url ?? null,
    kalshiUrl: kalshiBook?.url ?? null,
    generatedAt: new Date().toISOString(),
  }
}

/* eslint-enable @typescript-eslint/no-explicit-any */

/** A minute, like the board: shared across instances, so a busy game costs
 *  one ESPN summary, one Gamma event and one book read a minute. */
const buildShared = unstable_cache((sport: SportKey, id: string) => build(sport, id), ['sport-game-v1'], {
  revalidate: 60,
  tags: ['sport-game'],
})

export async function getSportGame(sport: SportKey, id: string): Promise<SportGamePage> {
  return buildShared(sport, id)
}
