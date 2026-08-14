// Game Center data layer — everything one fixture page needs, from feeds that
// cost nothing.
//
// Deliberately NOT included: DraftKings / FanDuel / BetMGM. The Odds API key is
// exhausted (0 of 500 this month) and retail US books are not our benchmark
// anyway — the whole edge framework prices against Pinnacle and Betfair. When
// there is budget for a sharp feed it plugs in here as another column.

export const GAMMA_API = 'https://gamma-api.polymarket.com'
export const CLOB_API = 'https://clob.polymarket.com'
export const KALSHI_API = 'https://api.elections.kalshi.com/trade-api/v2'

// Polymarket's taker fee: shares x 0.05 x p x (1-p), verified to 0.0001% on 87k
// real fills. In probability points it is exactly what a cross-venue price
// difference has to clear before it means anything.
export const PM_FEE_RATE = 0.05
// Kalshi charges ceil(0.07 x C x p x (1-p)) — 40% more than Polymarket.
export const KALSHI_FEE_RATE = 0.07

export function takerFeePp(price: number, rate = PM_FEE_RATE): number {
  const p = Math.min(Math.max(price, 0), 1)
  return 100 * rate * p * (1 - p)
}

export interface BookSide {
  bid: number | null
  ask: number | null
  bidDepthUsd: number | null
  askDepthUsd: number | null
}

export interface Outcome {
  name: string
  price: number | null      // Gamma mid
  tokenId: string | null
  book: BookSide | null     // CLOB top of book — what is actually executable
}

export interface MarketGroup {
  group: string
  question: string
  line: number | null
  outcomes: Outcome[]
  volume: number | null
  liquidity: number | null
  slug: string | null
}

export interface PricePoint {
  t: number
  p: number
}

export interface LiveState {
  minute: number | null
  homeGoals: number
  awayGoals: number
  status: string
  clockSource: 'api-football' | null
  stats: LiveStats | null
}

export interface LiveStats {
  homeXg: number | null
  awayXg: number | null
  homeShotsOn: number
  awayShotsOn: number
  homeShotsTotal: number
  awayShotsTotal: number
  homeCorners: number
  awayCorners: number
  homePossession: number | null
  awayPossession: number | null
  homeReds: number
  awayReds: number
}

export interface KalshiSide {
  name: string
  bid: number | null
  ask: number | null
}

export interface KalshiComparison {
  eventTicker: string
  title: string
  url: string
  sides: KalshiSide[]
  // Best cross-venue difference after BOTH venues' taker fees. Measured at
  // zero net arbs across 57 fixtures: the gross ceiling is one tick against a
  // ~3pp fee bar, so this is shown as a price comparison, never as an arb.
  bestNetPp: number | null
}

export interface GameData {
  slug: string
  title: string
  home: string
  away: string
  competition: string | null
  kickoff: string | null
  pmUrl: string
  live: LiveState | null
  groups: MarketGroup[]
  history: { tokenId: string; label: string; points: PricePoint[] } | null
  kalshi: KalshiComparison | null
  notes: string[]
}

async function getJson(url: string, timeoutMs = 10000): Promise<unknown> {
  const res = await fetch(url, {
    signal: AbortSignal.timeout(timeoutMs),
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

function num(v: unknown): number | null {
  const n = typeof v === 'string' ? parseFloat(v) : typeof v === 'number' ? v : NaN
  return Number.isFinite(n) ? n : null
}

// ── market classification ────────────────────────────────────────────────────

const GROUP_RULES: Array<[RegExp, string]> = [
  [/halftime|half-time|1st half|first half/i, 'Halftime'],
  [/second half|2nd half/i, 'Second half'],
  [/exact score|correct score/i, 'Exact score'],
  [/both teams|btts/i, 'Both teams to score'],
  [/corner/i, 'Corners'],
  [/first to score|first goal/i, 'First to score'],
  [/card|booking/i, 'Cards'],
  [/O\/U|over|under|total/i, 'Totals'],
  [/draw|tie|\bwin\b|moneyline/i, 'Match result'],
]

export function classifyGroup(question: string): string {
  for (const [re, label] of GROUP_RULES) if (re.test(question)) return label
  return 'Other'
}

export function lineOf(question: string): number | null {
  const m = question.match(/(\d+\.5)/)
  return m ? parseFloat(m[1]) : null
}

// The order groups are shown in — the markets people actually trade first.
const GROUP_ORDER = [
  'Match result', 'Totals', 'Both teams to score', 'Halftime',
  'Second half', 'First to score', 'Exact score', 'Corners', 'Cards', 'Other',
]

// ── Polymarket ───────────────────────────────────────────────────────────────

export async function fetchEvent(slug: string): Promise<Record<string, unknown> | null> {
  const direct = (await getJson(`${GAMMA_API}/events?slug=${encodeURIComponent(slug)}`)) as unknown[]
  if (Array.isArray(direct) && direct.length) return direct[0] as Record<string, unknown>
  return null
}

/** Sibling events — Polymarket splits one fixture across "A vs B",
 *  "A vs B - More Markets", "A vs B - Total Corners" and so on, and the
 *  secondary markets live only in the siblings. A page built from the main
 *  event alone silently shows a third of the board. */
export async function fetchSiblings(
  slug: string,
  title: string
): Promise<Array<Record<string, unknown>>> {
  const base = slug.replace(
    /-(more-markets|halftime-result|second-half-result|exact-score|first-to-score|total-corners|cards)$/,
    ''
  )
  const suffixes = [
    '', '-more-markets', '-halftime-result', '-second-half-result',
    '-exact-score', '-first-to-score', '-total-corners',
  ]
  const settled = await Promise.allSettled(
    suffixes.map((s) => fetchEvent(`${base}${s}`))
  )
  const out: Array<Record<string, unknown>> = []
  const seen = new Set<string>()
  for (const r of settled) {
    if (r.status !== 'fulfilled' || !r.value) continue
    const ev = r.value
    const id = String(ev.id ?? ev.slug ?? '')
    // Titles must share the fixture, or a slug collision pulls in another game.
    const evTitle = String(ev.title ?? '')
    if (!evTitle.startsWith(title.split(' - ')[0].slice(0, 12))) continue
    if (id && !seen.has(id)) {
      seen.add(id)
      out.push(ev)
    }
  }
  return out
}

export async function fetchBook(tokenId: string): Promise<BookSide | null> {
  try {
    const book = (await getJson(
      `${CLOB_API}/book?token_id=${encodeURIComponent(tokenId)}`
    )) as { bids?: Array<{ price: string; size: string }>; asks?: Array<{ price: string; size: string }> }
    const bids = (book.bids ?? []).slice().sort((a, b) => num(b.price)! - num(a.price)!)
    const asks = (book.asks ?? []).slice().sort((a, b) => num(a.price)! - num(b.price)!)
    if (!bids.length && !asks.length) return null
    const notional = (side: Array<{ price: string; size: string }>) =>
      side.slice(0, 5).reduce((acc, x) => acc + (num(x.price) ?? 0) * (num(x.size) ?? 0), 0)
    return {
      bid: bids.length ? num(bids[0].price) : null,
      ask: asks.length ? num(asks[0].price) : null,
      bidDepthUsd: bids.length ? notional(bids) : null,
      askDepthUsd: asks.length ? notional(asks) : null,
    }
  } catch {
    return null
  }
}

/** Polymarket's own price history — free, native, and the reason the dead
 *  pm_ticks recorder is not needed for a sparkline. */
export async function fetchHistory(tokenId: string, hours = 24): Promise<PricePoint[]> {
  try {
    const startTs = Math.floor(Date.now() / 1000) - hours * 3600
    const data = (await getJson(
      `${CLOB_API}/prices-history?market=${encodeURIComponent(tokenId)}&startTs=${startTs}&fidelity=5`
    )) as { history?: Array<{ t: number; p: number }> }
    return (data.history ?? []).map((h) => ({ t: h.t, p: h.p }))
  } catch {
    return []
  }
}

export function buildGroups(events: Array<Record<string, unknown>>): MarketGroup[] {
  const groups: MarketGroup[] = []
  const seen = new Set<string>()

  for (const ev of events) {
    for (const raw of (ev.markets as Array<Record<string, unknown>>) ?? []) {
      if (raw.closed) continue
      const question = String(raw.question ?? '')
      if (!question) continue
      const key = String(raw.conditionId ?? question)
      if (seen.has(key)) continue
      seen.add(key)

      let prices: unknown[] = []
      let names: unknown[] = []
      let tokens: unknown[] = []
      try {
        prices = JSON.parse(String(raw.outcomePrices ?? '[]'))
        names = JSON.parse(String(raw.outcomes ?? '[]'))
        tokens = JSON.parse(String(raw.clobTokenIds ?? '[]'))
      } catch {
        continue
      }

      const outcomes: Outcome[] = names.map((n, i) => ({
        name: String(n),
        price: num(prices[i]),
        tokenId: tokens[i] ? String(tokens[i]) : null,
        book: null,
      }))
      if (!outcomes.length) continue

      groups.push({
        group: classifyGroup(question),
        question,
        line: lineOf(question),
        outcomes,
        volume: num(raw.volume),
        liquidity: num(raw.liquidity),
        slug: raw.slug ? String(raw.slug) : null,
      })
    }
  }

  groups.sort((a, b) => {
    const ga = GROUP_ORDER.indexOf(a.group)
    const gb = GROUP_ORDER.indexOf(b.group)
    if (ga !== gb) return (ga < 0 ? 99 : ga) - (gb < 0 ? 99 : gb)
    if (a.line != null && b.line != null) return a.line - b.line
    return (b.volume ?? 0) - (a.volume ?? 0)
  })
  return groups
}

// ── api-football live state ──────────────────────────────────────────────────

const AF_API = 'https://v3.football.api-sports.io'

// Team-name matching. Substring containment looks adequate and is not: on
// 2026-08-14 it paired "River Plate" with "Platense" ("plate" is inside
// "platense") and "Minnesota United" with "Minnesota United II", and each wrong
// pair produced a confident double-digit price discrepancy. Tokens, scored
// against the longer name, with reserve/youth sides disqualified.
const NAME_NOISE = new Set([
  'fc', 'cf', 'ca', 'aa', 'sc', 'ac', 'as', 'sv', 'sk', 'fk', 'afc', 'bk', 'if',
  'cd', 'ud', 'sd', 'rc', 'cs', 'club', 'de', 'do', 'da', 'the',
  'ff', 'bc', 'gf', 'ik', 'aik', 'os', 'vf', 'kv', 'us', 'usl',
  'football', 'futbol', 'calcio', 'cp', 'cr', 'ec', 'sp',
])
// Canonical, because the same reserve side is "Real Sociedad B" on Polymarket
// and "Real Sociedad II" on api-football.
const SQUAD_CANON: Record<string, string> = {
  ii: 'reserve', b: 'reserve', reserves: 'reserve', iii: 'third',
  u17: 'u17', u18: 'u18', u19: 'u19', u20: 'u20', u21: 'u21', u23: 'u23',
  legends: 'legends', youth: 'youth', academy: 'academy',
  women: 'women', w: 'women',
}
const MIN_SIDE_SCORE = 0.6
// An abbreviation is short: "Man" for "Manchester" yes, "plate" for "platense" no.
const MAX_ABBREV_LEN = 4

function normTeam(s: string): string {
  return s
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, ' ')
    .trim()
}

function teamTokens(name: string): { ident: string[]; markers: string[] } {
  const raw = normTeam(name).split(/\s+/).filter(Boolean)
  return {
    ident: raw.filter((t) => !(t in SQUAD_CANON) && !NAME_NOISE.has(t)),
    markers: [...new Set(raw.filter((t) => t in SQUAD_CANON).map((t) => SQUAD_CANON[t]))].sort(),
  }
}

function tokenHit(a: string, b: string): boolean {
  if (a === b) return true
  const [short, long] = a.length <= b.length ? [a, b] : [b, a]
  return short.length >= 3 && short.length <= MAX_ABBREV_LEN && long.startsWith(short)
}

/** 0-1 similarity between two spellings of a club.
 *
 *  Full containment scores 1.0 — the feeds disagree by ADDING words rather than
 *  changing them ("Coventry City FC" vs "Coventry"), so every token of the
 *  shorter name appearing in the longer is what agreement looks like here.
 *  Otherwise it is the shared fraction of the LONGER name, which is what keeps
 *  "Real Salt Lake" away from "Real Monarchs" at 0.33. */
export function teamScore(a: string, b: string): number {
  const ta = teamTokens(a)
  const tb = teamTokens(b)
  if (ta.markers.join(',') !== tb.markers.join(',')) return 0
  if (!ta.ident.length || !tb.ident.length) return 0

  const [short, long] = ta.ident.length <= tb.ident.length
    ? [ta.ident, tb.ident] : [tb.ident, ta.ident]
  if (short.every((x) => long.some((y) => tokenHit(x, y)))) return 1

  const shared = ta.ident.filter((x) => tb.ident.some((y) => tokenHit(x, y))).length
  return shared / Math.max(ta.ident.length, tb.ident.length)
}

export function fixtureMatches(
  pmHome: string, pmAway: string, otherHome: string, otherAway: string
): boolean {
  return (
    teamScore(pmHome, otherHome) >= MIN_SIDE_SCORE &&
    teamScore(pmAway, otherAway) >= MIN_SIDE_SCORE
  )
}

/** Live score, minute and in-game stats.
 *
 *  The minute comes from api-football's `elapsed`, NOT from Polymarket's listed
 *  start time. That distinction is not pedantic: on smaller-league fixtures PM's
 *  listed time runs ~30 minutes ahead of the real kick-off, and trusting it is
 *  what made 73k late-goal observations unusable. When api-football cannot be
 *  reached or does not cover the fixture, this returns null and the page says
 *  the clock is unverified rather than showing a number it cannot stand behind.
 */
export async function fetchLive(home: string, away: string): Promise<LiveState | null> {
  const key = process.env.FOOTBALL_API_KEY
  if (!key) return null

  try {
    const res = await fetch(`${AF_API}/fixtures?live=all`, {
      headers: { 'x-apisports-key': key },
      signal: AbortSignal.timeout(12000),
      next: { revalidate: 30 },
    })
    if (!res.ok) return null
    const data = (await res.json()) as { response?: Array<Record<string, unknown>> }

    for (const fx of data.response ?? []) {
      const teams = fx.teams as { home: { name: string }; away: { name: string } }
      if (!fixtureMatches(home, away, teams?.home?.name ?? '', teams?.away?.name ?? '')) {
        continue
      }

      const goals = fx.goals as { home: number; away: number }
      const status = (fx.fixture as { status: { elapsed: number | null; short: string } }).status
      const fixtureId = (fx.fixture as { id: number }).id

      return {
        minute: status?.elapsed ?? null,
        homeGoals: goals?.home ?? 0,
        awayGoals: goals?.away ?? 0,
        status: status?.short ?? '',
        clockSource: 'api-football',
        stats: await fetchStats(fixtureId, teams.home.name, key),
      }
    }
  } catch {
    return null
  }
  return null
}

/** In-game statistics. Most smaller competitions are not covered — the endpoint
 *  answers 200 with an empty array — so this returns null rather than a row of
 *  zeros, which would read on the page as "nothing is happening". */
async function fetchStats(
  fixtureId: number,
  homeName: string,
  key: string
): Promise<LiveStats | null> {
  try {
    const res = await fetch(`${AF_API}/fixtures/statistics?fixture=${fixtureId}`, {
      headers: { 'x-apisports-key': key },
      signal: AbortSignal.timeout(10000),
      next: { revalidate: 30 },
    })
    if (!res.ok) return null
    const data = (await res.json()) as {
      response?: Array<{ team: { name: string }; statistics: Array<{ type: string; value: unknown }> }>
    }
    if (!data.response?.length) return null

    const out: LiveStats = {
      homeXg: null, awayXg: null,
      homeShotsOn: 0, awayShotsOn: 0,
      homeShotsTotal: 0, awayShotsTotal: 0,
      homeCorners: 0, awayCorners: 0,
      homePossession: null, awayPossession: null,
      homeReds: 0, awayReds: 0,
    }
    let any = false

    for (const t of data.response) {
      const isHome = t.team?.name === homeName
      for (const s of t.statistics ?? []) {
        if (s.value == null) continue
        const v = typeof s.value === 'string' ? parseFloat(s.value.replace('%', '')) : Number(s.value)
        if (!Number.isFinite(v)) continue
        if (v !== 0) any = true
        switch (s.type) {
          case 'Shots on Goal': isHome ? (out.homeShotsOn = v) : (out.awayShotsOn = v); break
          case 'Total Shots': isHome ? (out.homeShotsTotal = v) : (out.awayShotsTotal = v); break
          case 'Corner Kicks': isHome ? (out.homeCorners = v) : (out.awayCorners = v); break
          case 'Ball Possession': isHome ? (out.homePossession = v) : (out.awayPossession = v); break
          case 'expected_goals': isHome ? (out.homeXg = v) : (out.awayXg = v); break
          case 'Red Cards': isHome ? (out.homeReds = v) : (out.awayReds = v); break
        }
      }
    }
    return any ? out : null
  } catch {
    return null
  }
}

// ── Kalshi ───────────────────────────────────────────────────────────────────

/** The same fixture on the other venue.
 *
 *  Two things this must not do. It must not present a price difference as an
 *  arbitrage — we measured 57 fixtures quoted on both venues and found zero net
 *  arbs, because the gross ceiling is one tick against a ~3pp combined fee bar.
 *  And it must not quote a placeholder book: about a quarter of Kalshi's soccer
 *  markets sit at a fixed 0.02/0.81 on every outcome, where the mid is
 *  meaningless. Those are filtered on spread.
 */
/** Kalshi's soccer game series, keyed by the competition name Polymarket uses.
 *
 *  Kalshi's /events endpoint has no free-text search and returns every open
 *  market across politics, economics and sport, so an unfiltered scan of the
 *  first page never reaches football at all. The series ticker has to be
 *  resolved up front. Only the competitions both venues actually list are here;
 *  a fixture outside them simply has no Kalshi column, which is the truth.
 */
const KALSHI_SERIES: Array<[RegExp, string]> = [
  [/premier league/i, 'KXEPLGAME'],
  [/champions league/i, 'KXUCLGAME'],
  [/europa league/i, 'KXUELGAME'],
  [/la ?liga 2|laliga 2|segunda/i, 'KXLALIGA2GAME'],
  [/la ?liga/i, 'KXLALIGAGAME'],
  [/serie a/i, 'KXSERIEAGAME'],
  [/bundesliga 2|2\. bundesliga/i, 'KXBUNDESLIGA2GAME'],
  [/bundesliga/i, 'KXBUNDESLIGAGAME'],
  [/ligue 2/i, 'KXLIGUE2GAME'],
  [/ligue 1/i, 'KXLIGUE1GAME'],
  [/eredivisie/i, 'KXEREDIVISIEGAME'],
  [/championship/i, 'KXEFLCHAMPIONSHIPGAME'],
  [/mls|major league soccer/i, 'KXMLSGAME'],
  [/allsvenskan/i, 'KXALLSVENSKANGAME'],
]

export function kalshiSeriesFor(competition: string | null): string | null {
  if (!competition) return null
  for (const [re, ticker] of KALSHI_SERIES) if (re.test(competition)) return ticker
  return null
}

export async function fetchKalshi(
  home: string,
  away: string,
  competition: string | null
): Promise<KalshiComparison | null> {
  const series = kalshiSeriesFor(competition)
  if (!series) return null

  try {
    const data = (await getJson(
      `${KALSHI_API}/events?series_ticker=${series}&status=open&limit=200&with_nested_markets=true`,
      12000
    )) as { events?: Array<Record<string, unknown>> }

    for (const ev of data.events ?? []) {
      const title = String(ev.title ?? '')
      // Kalshi event titles are "Home vs Away", verified against ESPN on 3/3
      // fixtures. Split and score each side rather than looking for either
      // team's words anywhere in the string.
      const parts = title.split(/\s+vs\.?\s+/i)
      if (parts.length !== 2) continue
      if (!fixtureMatches(home, away, parts[0], parts[1])) continue

      const sides: KalshiSide[] = []
      for (const m of (ev.markets as Array<Record<string, unknown>>) ?? []) {
        // The dollar fields are the live ones; the legacy integer-cent
        // yes_bid / yes_ask now come back null on every market.
        const bid = num(m.yes_bid_dollars)
        const ask = num(m.yes_ask_dollars)
        // The placeholder book — about a quarter of Kalshi's soccer markets sit
        // at a fixed 0.02/0.81 on every outcome. A 0.79-wide quote is not a
        // price, and its mid is meaningless.
        if (bid != null && ask != null && ask - bid > 0.10) continue
        sides.push({ name: String(m.yes_sub_title ?? m.ticker ?? ''), bid, ask })
      }
      if (!sides.length) continue

      return {
        eventTicker: String(ev.event_ticker ?? ''),
        title,
        url: `https://kalshi.com/markets/${series.toLowerCase()}`,
        sides,
        bestNetPp: null,
      }
    }
  } catch {
    return null
  }
  return null
}

export { GROUP_ORDER }
