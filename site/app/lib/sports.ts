/** US sports on two exchanges — Kalshi and Polymarket — placed on ESPN's schedule.
 *
 *  🔑 ESPN is the spine, not a venue. It is the schedule both exchanges settle
 *     against, it knows which side is at home, and it carries the live score.
 *     A market is shown only once it has been placed on an ESPN game with BOTH
 *     teams matched and the date agreeing. One that cannot be placed is counted
 *     and left out, never guessed onto a game: a price shown against the wrong
 *     fixture is worse than no price, and a side error inverts it outright.
 *
 *  ⚠️ Names are compared WHOLE, never as substrings. "Miami" is two schools
 *     and "Tigers" is a dozen; the join survives only because both teams must
 *     land on different sides of one game at the same time.
 *
 *  ⚠️ ESPN: do not set a User-Agent. Its edge serves library defaults and 403s
 *     browser-shaped and custom agents (measured, see CLAUDE.md).
 *
 *  Prices are each venue's ASK before fees — what buying that side costs now.
 *  Fees differ by venue and are not netted out; the page says so.
 */

import { unstable_cache } from 'next/cache'
import { SPORT_META, type SportBoardData, type SportGame, type SportKey, type TeamRef } from './sportsMeta'
import {
  bestFor,
  combinedVolume,
  gradeOf,
  quoteOf,
  type OutcomeKey,
  type Quote,
  type VenueBook,
} from './venues'

interface Source {
  espn: string
  /** ESPN's FBS (80) and FCS (81) groups. Without a group the college
   *  scoreboard is a top-25 sample, and both exchanges list FCS games too. */
  espnGroups?: string[]
  kalshiSeries: string
  /** The series title slugged, the way kalshi.com builds a market's URL. */
  kalshiSlug: string
  /** Polymarket's league tag, from Gamma `/sports`. Its SERIES ids change by
   *  season (NFL's returned nothing on 2026-09-13); the tag does not. */
  pmTagId: number
}

const SOURCES: Record<SportKey, Source> = {
  nfl: { espn: 'football/nfl', kalshiSeries: 'KXNFLGAME', kalshiSlug: 'professional-football-game', pmTagId: 450 },
  cfb: {
    espn: 'football/college-football',
    espnGroups: ['80', '81'],
    kalshiSeries: 'KXNCAAFGAME',
    kalshiSlug: 'college-football-game',
    pmTagId: 100351,
  },
  mlb: { espn: 'baseball/mlb', kalshiSeries: 'KXMLBGAME', kalshiSlug: 'professional-baseball-game', pmTagId: 100381 },
  nba: { espn: 'basketball/nba', kalshiSeries: 'KXNBAGAME', kalshiSlug: 'pro-basketball-game', pmTagId: 745 },
  nhl: { espn: 'hockey/nhl', kalshiSeries: 'KXNHLGAME', kalshiSlug: 'nhl-game', pmTagId: 899 },
  wnba: { espn: 'basketball/wnba', kalshiSeries: 'KXWNBAGAME', kalshiSlug: 'womens-pro-basketball-game', pmTagId: 100254 },
}

const TIMEOUT_MS = 12_000

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS), cache: 'no-store' })
  if (!res.ok) throw new Error(`${new URL(url).host} answered ${res.status}`)
  return res.json() as Promise<T>
}

const num = (v: string | number | null | undefined): number | null => {
  const x = typeof v === 'number' ? v : parseFloat(v ?? '')
  return Number.isFinite(x) ? x : null
}
const round3 = (x: number) => Math.round(x * 1000) / 1000

// ── Eastern time: both ESPN and Kalshi file a game under its ET date ─────────

const ET = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

function etOf(d: Date): { ymd: string; min: number } {
  const p: Record<string, string> = {}
  for (const x of ET.formatToParts(d)) p[x.type] = x.value
  return { ymd: `${p.year}${p.month}${p.day}`, min: Number(p.hour) * 60 + Number(p.minute) }
}

// ── names ────────────────────────────────────────────────────────────────────

const norm = (s: string | null | undefined): string =>
  (s ?? '').toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9]/g, '')

interface EspnTeam {
  abbr: string
  location: string
  name: string
  display: string
  short: string
  logo: string | null
  score: number | null
}

/** Every whole form of a team's name ESPN gives, plus the city-and-initials
 *  form Kalshi uses to split two teams in one city: "New York M", "Chicago WS",
 *  "Los Angeles R". */
function keysOf(t: EspnTeam): Set<string> {
  const k = new Set<string>()
  for (const s of [t.abbr, t.location, t.name, t.display, t.short]) {
    const n = norm(s)
    if (n) k.add(n)
  }
  const words = t.name.split(/\s+/).filter(Boolean)
  if (t.location && words.length) {
    k.add(norm(t.location + words.map((w) => w[0]).join('')))
    k.add(norm(t.location + words[0][0]))
  }
  return k
}

// ── ESPN: the schedule ───────────────────────────────────────────────────────

interface EspnGame {
  id: string
  start: string
  state: 'pre' | 'in' | 'post'
  detail: string
  home: EspnTeam
  away: EspnTeam
  homeKeys: Set<string>
  awayKeys: Set<string>
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function espnTeam(c: any): EspnTeam {
  const t = c.team ?? {}
  const score = c.score === '' || c.score == null ? null : num(c.score)
  return {
    abbr: t.abbreviation ?? '',
    location: t.location ?? '',
    name: t.name ?? '',
    display: t.displayName ?? '',
    short: t.shortDisplayName ?? t.name ?? '',
    logo: t.logo ?? null,
    score,
  }
}

/** The schedule.
 *
 *  ⚠️ ESPN's date-RANGE query is not reliable. `dates=20260919-20260929`
 *     answered on 2026-09-13 and returns `400 Failed to get events endpoint`
 *     on 2026-09-20, for every sport, while a single day and a whole MONTH
 *     both answer fine. It is their bug, not ours, and it took all six US
 *     boards down until the cache expired.
 *
 *     So the window is asked for BY MONTH — one or two requests — and cut to
 *     size below. A day-by-day fan-out also works and was tried first; the
 *     month is the same guarantee at a ninth of the cost.
 */
async function espnGames(src: Source, days: number, now: Date): Promise<EspnGame[]> {
  // From last night (a late game can still be live) to the end of the window.
  const from = etOf(new Date(now.getTime() - 12 * 3600_000)).ymd
  const to = etOf(new Date(now.getTime() + days * 86400_000)).ymd

  // ⚠️ ESPN stopped answering a DATE RANGE (`dates=20260920-20260929` → 400,
  //    every sport, soccer included; found 2026-09-20 with every US board
  //    erroring). A single day and a whole MONTH still answer, so the window
  //    is asked for by month — one or two requests — and cut to size below.
  const months = [...new Set([from.slice(0, 6), to.slice(0, 6)])]
  const queries = months.flatMap((month) => {
    const base = { dates: month, limit: '500' }
    return src.espnGroups?.length
      ? src.espnGroups.map((groups) => new URLSearchParams({ ...base, groups }))
      : [new URLSearchParams(base)]
  })
  const pages = await Promise.all(
    queries.map((qs) => getJson<any>(`https://site.api.espn.com/apis/site/v2/sports/${src.espn}/scoreboard?${qs}`))
  )
  const out: EspnGame[] = []
  const seen = new Set<string>()
  // An FBS-v-FCS game is in both groups, and a fixture on the boundary is in
  // both months. It is one game.
  for (const e of pages.flatMap((d) => d.events ?? [])) {
    if (seen.has(String(e.id))) continue
    seen.add(String(e.id))
    const c = e.competitions?.[0]
    const h = c?.competitors?.find((x: any) => x.homeAway === 'home')
    const a = c?.competitors?.find((x: any) => x.homeAway === 'away')
    if (!h?.team || !a?.team || !e.date) continue
    // A month is wider than the window this board shows.
    const ymd = etOf(new Date(e.date)).ymd
    if (ymd < from || ymd > to) continue
    const st = c.status?.type ?? e.status?.type ?? {}
    const home = espnTeam(h)
    const away = espnTeam(a)
    out.push({
      id: String(e.id),
      start: new Date(e.date).toISOString(),
      state: st.state === 'in' ? 'in' : st.state === 'post' ? 'post' : 'pre',
      detail: st.shortDetail ?? '',
      home,
      away,
      homeKeys: keysOf(home),
      awayKeys: keysOf(away),
    })
  }
  return out
}

/* eslint-enable @typescript-eslint/no-explicit-any */

// The grade thresholds, the tradeable band and the best-price pick all live in
// ./venues now — one definition for football and the US sports both, because
// "cheapest venue" answering differently on two pages is a bug waiting.

// ── Kalshi ───────────────────────────────────────────────────────────────────

interface KMarket {
  ticker: string
  yes_sub_title?: string
  yes_bid_dollars?: string
  yes_ask_dollars?: string
  yes_ask_size_fp?: string
  volume_fp?: string
}

interface KEvent {
  event_ticker: string
  title: string
  markets?: KMarket[]
}

async function kalshiEvents(series: string): Promise<KEvent[]> {
  const out: KEvent[] = []
  let cursor = ''
  for (let page = 0; page < 4; page++) {
    const qs = new URLSearchParams({ series_ticker: series, status: 'open', limit: '200', with_nested_markets: 'true' })
    if (cursor) qs.set('cursor', cursor)
    const d = await getJson<{ events?: KEvent[]; cursor?: string }>(
      `https://api.elections.kalshi.com/trade-api/v2/events?${qs}`
    )
    const evs = d.events ?? []
    out.push(...evs)
    cursor = d.cursor ?? ''
    if (!cursor || evs.length === 0) break
  }
  return out
}

const MONTH: Record<string, string> = {
  JAN: '01', FEB: '02', MAR: '03', APR: '04', MAY: '05', JUN: '06',
  JUL: '07', AUG: '08', SEP: '09', OCT: '10', NOV: '11', DEC: '12',
}

/** "KXNFLGAME-26SEP14DENKC" → 2026-09-14; baseball adds the ET first pitch,
 *  "KXMLBGAME-26SEP151840MILPIT" → 18:40, which separates a doubleheader.
 *  The date is the ET date: Monday night's 00:15Z kick-off files under the 14th. */
function kalshiWhen(ticker: string): { ymd: string; min: number | null } | null {
  const m = /-(\d{2})([A-Z]{3})(\d{2})(\d{4})?/.exec(ticker)
  if (!m || !MONTH[m[2]]) return null
  const min = m[4] ? Number(m[4].slice(0, 2)) * 60 + Number(m[4].slice(2)) : null
  return { ymd: `20${m[1]}${MONTH[m[2]]}${m[3]}`, min }
}

const suffix = (ticker: string) => ticker.slice(ticker.lastIndexOf('-') + 1)

function kalshiSide(m: KMarket, g: EspnGame): 'home' | 'away' | null {
  const sub = m.yes_sub_title ?? ''
  const forms = [suffix(m.ticker), sub, sub.replace(/\bSt\.?$/i, 'State')].map(norm).filter(Boolean)
  const h = forms.some((x) => g.homeKeys.has(x))
  const a = forms.some((x) => g.awayKeys.has(x))
  return h === a ? null : h ? 'home' : 'away'
}

function placeKalshi(ev: KEvent, games: EspnGame[]): { game: EspnGame; home: KMarket; away: KMarket } | null {
  const when = kalshiWhen(ev.event_ticker)
  if (!when) return null
  const sides = (ev.markets ?? []).filter((m) => !/^(TIE|DRAW)$/i.test(suffix(m.ticker)))
  if (sides.length !== 2) return null
  const hits: { game: EspnGame; home: KMarket; away: KMarket }[] = []
  for (const g of games) {
    const et = etOf(new Date(g.start))
    if (et.ymd !== when.ymd) continue
    if (when.min != null && Math.abs(et.min - when.min) > 90) continue
    const s0 = kalshiSide(sides[0], g)
    const s1 = kalshiSide(sides[1], g)
    if (!s0 || !s1 || s0 === s1) continue
    hits.push({ game: g, home: s0 === 'home' ? sides[0] : sides[1], away: s0 === 'home' ? sides[1] : sides[0] })
  }
  return hits.length === 1 ? hits[0] : null
}

function kalshiQuote(m: KMarket): Quote {
  const ask = num(m.yes_ask_dollars)
  const size = num(m.yes_ask_size_fp)
  return quoteOf(num(m.yes_bid_dollars), ask, ask != null && size != null ? ask * size : null)
}

function kalshiLine(src: Source, ev: KEvent, home: KMarket, away: KMarket): VenueBook {
  const quotes: Partial<Record<OutcomeKey, Quote>> = {
    home: kalshiQuote(home),
    away: kalshiQuote(away),
  }
  const volume = (num(home.volume_fp) ?? 0) + (num(away.volume_fp) ?? 0)
  return {
    venue: 'kalshi',
    url: `https://kalshi.com/markets/${src.kalshiSeries.toLowerCase()}/${src.kalshiSlug}/${ev.event_ticker.toLowerCase()}`,
    volume: volume > 0 ? volume : null,
    grade: gradeOf([quotes.home, quotes.away]),
    quotes,
    source: 'kalshi',
  }
}

// ── Polymarket ───────────────────────────────────────────────────────────────

interface PmTeam {
  name?: string
  alias?: string
  abbreviation?: string
  ordering?: string
}

interface PmMarket {
  sportsMarketType?: string
  outcomes?: string
  clobTokenIds?: string
  bestBid?: number
  bestAsk?: number
  volumeNum?: number
}

interface PmEvent {
  slug: string
  startTime?: string
  ended?: boolean
  teams?: PmTeam[]
  markets?: PmMarket[]
}

interface PmGame {
  slug: string
  start: number
  home: PmTeam
  away: PmTeam
  tokens: { home: string; away: string }
  gamma: { home: Quote; away: Quote }
  volume: number | null
}

async function pmEvents(tagId: number, now: Date, days: number): Promise<PmEvent[]> {
  // `endDate` is the start on most leagues and start + 7 days on baseball, so
  // the far edge carries a week of slack; the start itself is filtered later.
  const d0 = new Date(now.getTime() - 86400_000).toISOString().slice(0, 10)
  const d1 = new Date(now.getTime() + (days + 8) * 86400_000).toISOString().slice(0, 10)
  const out: PmEvent[] = []
  // Gamma caps `limit` at 100 whatever is sent, and honours `offset`.
  for (let offset = 0; offset < 600; offset += 100) {
    const qs = new URLSearchParams({
      tag_id: String(tagId),
      closed: 'false',
      limit: '100',
      offset: String(offset),
      end_date_min: d0,
      end_date_max: d1,
    })
    const page = await getJson<PmEvent[]>(`https://gamma-api.polymarket.com/events?${qs}`)
    out.push(...page)
    if (page.length < 100) break
  }
  return out
}

function parsePm(ev: PmEvent): PmGame | null {
  const ml = (ev.markets ?? []).find((m) => (m.sportsMarketType ?? '').toLowerCase() === 'moneyline')
  const teams = ev.teams ?? []
  if (!ml || teams.length !== 2 || !ev.startTime || ev.ended) return null
  let outcomes: string[]
  let tokens: string[]
  try {
    outcomes = JSON.parse(ml.outcomes ?? '[]')
    tokens = JSON.parse(ml.clobTokenIds ?? '[]')
  } catch {
    return null
  }
  if (outcomes.length !== 2 || tokens.length !== 2) return null
  // Which outcome is which side: by the outcome's own label against each
  // team's names and Polymarket's `ordering` — never by position in a list.
  const sideOf = (label: string): 'home' | 'away' | null => {
    const t = teams.find((x) => [x.alias, x.name, x.abbreviation].some((n) => norm(n) === norm(label)))
    return t?.ordering === 'home' || t?.ordering === 'away' ? t.ordering : null
  }
  const s0 = sideOf(outcomes[0])
  const s1 = sideOf(outcomes[1])
  const home = teams.find((t) => t.ordering === 'home')
  const away = teams.find((t) => t.ordering === 'away')
  if (!s0 || !s1 || s0 === s1 || !home || !away) return null
  // Gamma's bestBid/bestAsk belong to outcome 0; the other side trades at
  // 1 − ask / 1 − bid. Only a fallback: the book itself is read below.
  const bb = num(ml.bestBid)
  const ba = num(ml.bestAsk)
  const q0 = quoteOf(bb, ba, null)
  const q1 = quoteOf(ba != null ? 1 - ba : null, bb != null ? 1 - bb : null, null)
  const first = s0 === 'home'
  return {
    slug: ev.slug,
    start: Date.parse(ev.startTime),
    home,
    away,
    tokens: { home: first ? tokens[0] : tokens[1], away: first ? tokens[1] : tokens[0] },
    gamma: { home: first ? q0 : q1, away: first ? q1 : q0 },
    volume: num(ml.volumeNum),
  }
}

function pmMatches(t: PmTeam, keys: Set<string>): boolean {
  return [t.name, t.alias, t.abbreviation].some((x) => {
    const n = norm(x)
    return n !== '' && keys.has(n)
  })
}

function placePm(p: PmGame, games: EspnGame[]): EspnGame | null {
  const hits = games.filter(
    (g) =>
      Math.abs(Date.parse(g.start) - p.start) <= 3 * 3600_000 &&
      pmMatches(p.home, g.homeKeys) &&
      pmMatches(p.away, g.awayKeys) &&
      !pmMatches(p.home, g.awayKeys) &&
      !pmMatches(p.away, g.homeKeys)
  )
  return hits.length === 1 ? hits[0] : null
}

interface ClobBook {
  asset_id: string
  bids?: { price: string; size: string }[]
  asks?: { price: string; size: string }[]
}

/** The live book for every placed token, in as few requests as possible —
 *  Gamma's quote lags the CLOB and carries no depth. */
async function pmBooks(tokens: string[]): Promise<Map<string, Quote>> {
  const out = new Map<string, Quote>()
  for (let i = 0; i < tokens.length; i += 400) {
    const books = await getJson<ClobBook[]>('https://clob.polymarket.com/books', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(tokens.slice(i, i + 400).map((token_id) => ({ token_id }))),
    })
    for (const b of books) {
      let bid: number | null = null
      let ask: number | null = null
      let askSize = 0
      for (const l of b.bids ?? []) {
        const p = Number(l.price)
        if (Number(l.size) > 0 && (bid == null || p > bid)) bid = p
      }
      for (const l of b.asks ?? []) {
        const p = Number(l.price)
        const s = Number(l.size)
        if (s > 0 && (ask == null || p < ask)) {
          ask = p
          askSize = s
        }
      }
      out.set(b.asset_id, quoteOf(bid, ask, ask != null ? ask * askSize : null))
    }
  }
  return out
}

// ── the board ────────────────────────────────────────────────────────────────

function teamRef(t: EspnTeam): TeamRef {
  return { name: t.display || t.name, short: t.short || t.name, abbr: t.abbr, logo: t.logo, score: t.score }
}

async function build(sport: SportKey): Promise<SportBoardData> {
  const src = SOURCES[sport]
  const days = SPORT_META[sport].days
  const now = new Date()
  const horizon = now.getTime() + days * 86400_000

  // ESPN is the spine: without it nothing can be placed, so its failure is the
  // board's. A venue that fails costs its own column and nothing else.
  const [games, kal, pm] = await Promise.all([
    espnGames(src, days, now),
    kalshiEvents(src.kalshiSeries).catch(() => [] as KEvent[]),
    pmEvents(src.pmTagId, now, days).catch(() => [] as PmEvent[]),
  ])

  const from = etOf(new Date(now.getTime() - 12 * 3600_000)).ymd
  const to = etOf(new Date(horizon)).ymd
  const slots = new Map<string, { kalshi: VenueBook | null; pm: PmGame | null }>()
  const slot = (id: string) => {
    let s = slots.get(id)
    if (!s) slots.set(id, (s = { kalshi: null, pm: null }))
    return s
  }
  // Two markets landing on one game is a join we cannot trust: drop both.
  const kalshiSeen = new Set<string>()
  const pmSeen = new Set<string>()
  const conflicted = { kalshi: new Set<string>(), pm: new Set<string>() }

  let kalshiN = 0
  let kalshiPlaced = 0
  for (const ev of kal) {
    const when = kalshiWhen(ev.event_ticker)
    if (!when || when.ymd < from || when.ymd > to) continue
    kalshiN++
    const hit = placeKalshi(ev, games)
    if (!hit) continue
    if (kalshiSeen.has(hit.game.id)) {
      conflicted.kalshi.add(hit.game.id)
      continue
    }
    kalshiSeen.add(hit.game.id)
    kalshiPlaced++
    slot(hit.game.id).kalshi = kalshiLine(src, ev, hit.home, hit.away)
  }

  let pmN = 0
  let pmPlaced = 0
  for (const ev of pm) {
    const p = parsePm(ev)
    if (!p || p.start < now.getTime() - 12 * 3600_000 || p.start > horizon) continue
    pmN++
    const g = placePm(p, games)
    if (!g) continue
    if (pmSeen.has(g.id)) {
      conflicted.pm.add(g.id)
      continue
    }
    pmSeen.add(g.id)
    pmPlaced++
    slot(g.id).pm = p
  }
  for (const id of Array.from(conflicted.kalshi)) slot(id).kalshi = null
  for (const id of Array.from(conflicted.pm)) slot(id).pm = null
  kalshiPlaced -= conflicted.kalshi.size
  pmPlaced -= conflicted.pm.size

  const tokens: string[] = []
  slots.forEach((s) => {
    if (s.pm) tokens.push(s.pm.tokens.home, s.pm.tokens.away)
  })
  // A failed book read leaves Gamma's quote in place, without depth.
  const books = tokens.length ? await pmBooks(tokens).catch(() => new Map<string, Quote>()) : new Map()

  const rows: SportGame[] = []
  for (const g of games) {
    const s = slots.get(g.id)
    if (!s || (!s.kalshi && !s.pm) || g.state === 'post' || Date.parse(g.start) > horizon) continue
    let polymarket: VenueBook | null = null
    if (s.pm) {
      const quotes: Partial<Record<OutcomeKey, Quote>> = {
        home: books.get(s.pm.tokens.home) ?? s.pm.gamma.home,
        away: books.get(s.pm.tokens.away) ?? s.pm.gamma.away,
      }
      polymarket = {
        venue: 'polymarket',
        url: `https://polymarket.com/event/${s.pm.slug}`,
        volume: s.pm.volume,
        grade: gradeOf([quotes.home, quotes.away]),
        quotes,
        // The book itself where it answered, Gamma's listing quote otherwise.
        source: books.has(s.pm.tokens.home) || books.has(s.pm.tokens.away) ? 'clob' : 'gamma',
      }
    }
    // Polymarket first, the way every board on the site orders them.
    const venues = [polymarket, s.kalshi].filter((v): v is VenueBook => v != null)
    rows.push({
      id: g.id,
      start: g.start,
      state: g.state,
      detail: g.detail,
      home: teamRef(g.home),
      away: teamRef(g.away),
      venues,
      best: bestFor(venues),
      volumeCombined: combinedVolume({
        polymarket: polymarket?.volume ?? null,
        kalshi: s.kalshi?.volume ?? null,
      }),
    })
  }
  rows.sort((a, b) => {
    const live = Number(b.state === 'in') - Number(a.state === 'in')
    return live || Date.parse(a.start) - Date.parse(b.start)
  })

  return {
    sport,
    games: rows,
    generatedAt: now.toISOString(),
    counts: { espn: games.length, kalshi: kalshiN, kalshiPlaced, polymarket: pmN, polymarketPlaced: pmPlaced },
  }
}

// ── cache: the scoutCache pattern, one board per sport ────────────────────────

/** A minute: an in-play price is not stale in that time, and a visitor
 *  landing on an instance that never swept reads someone else's sweep. */
const TTL_MS = 60_000

const buildShared = unstable_cache((sport: SportKey) => build(sport), ['sport-board-v2'], {
  revalidate: TTL_MS / 1000,
  tags: ['sport-board'],
})

const l1 = new Map<SportKey, { at: number; board: SportBoardData }>()
const inFlight = new Map<SportKey, Promise<SportBoardData>>()

export async function getSportBoard(sport: SportKey): Promise<SportBoardData> {
  const hit = l1.get(sport)
  if (hit && Date.now() - hit.at < TTL_MS) return hit.board
  const running = inFlight.get(sport)
  if (running) return running

  const p = buildShared(sport)
    .then((board) => {
      l1.set(sport, { at: Date.now(), board })
      return board
    })
    .finally(() => inFlight.delete(sport))
  inFlight.set(sport, p)
  try {
    return await p
  } catch (e) {
    // A sweep that fails does not throw away a board we already have.
    if (hit) return hit.board
    throw e
  }
}
