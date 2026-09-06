/** Scout — the matchday board.
 *
 *  What this file does NOT do is rank fixtures by a claimed edge against the
 *  sharp line. That number was measured and it is the spread plus the fee
 *  (finding_spread_floor; finding_pm_mid_is_pinnacle: +0.10pp CI[-0.01,+0.20]).
 *  Shipping it as "edge now" would sell a reading our own tests rejected.
 *
 *  What it ranks on instead is the one thing the 100-game review DID find
 *  separating: book quality. `real - ask` by spread, on 6,449 rows / 494
 *  fixtures, ran +3.64pp at 0-3pp of spread and -38.38pp at 20pp+. The tell is
 *  the spread, not the depth — a 1st-half book once quoted bid 0.55 / ask 0.99
 *  behind $30,117 of depth and traded at 0.56 two minutes later.
 */

import { fetchBook, teamScore } from './gamecenter'
import { matchEspn, type EspnLive } from './espn'

const GAMMA_API = 'https://gamma-api.polymarket.com'

/** Boards stay quotable through the whistle, so the window looks backwards far
 *  enough to keep a live match on the list. */
const WINDOW_BACK_H = 4
const WINDOW_FWD_H = 36

/** Gamma caps a page at 100 however large a `limit` you ask for, and honours
 *  `offset`. Stepping by anything other than the real page size silently skips
 *  events — a 300-step loop returned 26 fixtures out of a 344-event Saturday. */
const PAGE = 100
const MAX_PAGES = 12

/** The alias-aware scorer's own bar for "these are the same club". Side errors
 *  on a football board invert a reading rather than blunt it, so anything below
 *  this is treated as unknown rather than as a guess. */
const MIN_SIDE_SCORE = 0.6

// ── shapes ───────────────────────────────────────────────────────────────────

export type BookGrade = 'clean' | 'wide' | 'blown' | 'one-sided' | 'settled' | 'unknown'

/** Outside this band the market is decided and the decimal odds stop describing
 *  a bet anyone would place. Same convention the Game Center uses. */
const TRADEABLE_BAND: [number, number] = [0.03, 0.97]

export interface BookQuality {
  /** Which market was graded — named so the reading is checkable. */
  market: string
  bid: number | null
  ask: number | null
  spreadPp: number | null
  askDepthUsd: number | null
  /** `clob` is a live top-of-book round trip; `gamma` is the listing's own
   *  cached quote. Gamma's prices are known to lag the CLOB, so which one a
   *  grade came from is part of the reading, not an implementation detail. */
  source: 'clob' | 'gamma'
  grade: BookGrade
}

export interface ScoutFixture {
  /** Canonical event slug — what /game/<slug> takes. */
  slug: string
  home: string
  away: string
  competition: string | null
  /** Real kick-off: `startTime`, never `startDate`. The latter is when the
   *  board was listed, which on this feed is usually the same morning — using
   *  it as kick-off returns an empty board. */
  kickoff: string | null
  live: boolean
  /** What `live` is standing on. Never null when `live` is true. */
  liveSource: LiveSource | null
  /** From the feed only. Null when nothing authoritative knows the clock. */
  minute: number | null
  score: { home: number; away: number } | null
  finished: boolean
  markets: number
  volumeUsd: number
  liquidityUsd: number
  /** Probabilities. Sides are resolved with the alias-aware scorer against the
   *  fixture title, never by market order. */
  oneX2: { home: number | null; draw: number | null; away: number | null }
  over25: number | null
  hasTotals: boolean
  hasFirstHalf: boolean
  book: BookQuality | null
}

// ── raw Gamma access ─────────────────────────────────────────────────────────

type Raw = Record<string, unknown>

async function getJson(url: string, timeoutMs = 12000): Promise<unknown> {
  const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), cache: 'no-store' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

function num(v: unknown): number | null {
  const n = typeof v === 'string' ? parseFloat(v) : typeof v === 'number' ? v : NaN
  return Number.isFinite(n) ? n : null
}

function str(v: unknown): string {
  return v == null ? '' : String(v)
}

/** The fixture events, oldest kick-off first.
 *
 *  Two date fields on this feed are traps and both were walked into. An event's
 *  `startDate` is its LISTING time — usually the same morning — so filtering
 *  kick-off with `start_date_min` returns an empty board. And a fixture event's
 *  `endDate` EQUALS its `startTime`, so `end_date_min=now` silently drops every
 *  match the moment it kicks off: the exact set this page exists to show. The
 *  filter therefore reaches back over the whole window and the window itself
 *  does the bounding, in `buildFixtures`. */
export async function fetchSoccerEvents(): Promise<Raw[]> {
  const out: Raw[] = []
  const endMin = new Date(Date.now() - WINDOW_BACK_H * 3600_000).toISOString()

  for (let page = 0; page < MAX_PAGES; page++) {
    const url =
      `${GAMMA_API}/events?closed=false&limit=${PAGE}&offset=${page * PAGE}` +
      `&order=startTime&ascending=true&tag_slug=soccer` +
      `&end_date_min=${encodeURIComponent(endMin)}`
    let body: unknown
    try {
      body = await getJson(url)
    } catch {
      break
    }
    if (!Array.isArray(body) || body.length === 0) break
    out.push(...(body as Raw[]))
    if (body.length < PAGE) break
  }
  return out
}

// ── fixture identity ─────────────────────────────────────────────────────────

/** Polymarket splits one fixture across several events — "A vs. B",
 *  "A vs. B - More Markets", "A vs. B - 1st Half Result". They share the title
 *  up to the dash, and the un-suffixed one is the canonical board. */
function fixtureKey(title: string): string {
  return title.split(' - ')[0].trim()
}

function teamsOf(title: string): { home: string; away: string } | null {
  const m = fixtureKey(title).match(/^(.+?)\s+vs\.?\s+(.+)$/i)
  if (!m) return null
  const home = m[1].trim()
  const away = m[2].trim()
  return home && away ? { home, away } : null
}

const TAG_NOISE = new Set(['soccer', 'sports', 'games', 'football', 'live', 'all'])

/** Polymarket's competition tags are hand-entered and some carry the slug as
 *  the label — Juventus v Milan's only competition tag is `sea` labelled "sea",
 *  and there is no better one on the event. These are the ones seen on a full
 *  card; anything not listed falls through to the tidy-up below rather than to
 *  a guess. */
const COMPETITION_ALIASES: Record<string, string> = {
  sea: 'Serie A',
  seb: 'Serie B',
  epl: 'Premier League',
  laliga: 'LaLiga',
  bundesliga: 'Bundesliga',
  ucl: 'Champions League',
  uel: 'Europa League',
  uecl: 'Conference League',
  mex: 'Liga MX',
  mls: 'MLS',
  'ligue-1': 'Ligue 1',
  eredivisie: 'Eredivisie',
}

/** A label Polymarket left in slug case: "bundesliga", "sea". A real label
 *  carries a capital or a space. */
function tidyLabel(label: string, slug: string): string {
  const alias = COMPETITION_ALIASES[slug]
  if (alias) return alias
  if (/[A-Z]/.test(label) || label.includes(' ')) return label
  return label.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Events carry several tags in no guaranteed order, so the first non-noise one
 *  is not reliably the best one. Prefer a label that reads as written prose over
 *  a bare slug, and the longest of those. */
function competitionOf(ev: Raw): string | null {
  const candidates: string[] = []
  for (const t of (ev.tags as Raw[]) ?? []) {
    const slug = str(t.slug).toLowerCase()
    const label = str(t.label).trim()
    if (!label || TAG_NOISE.has(slug)) continue
    candidates.push(tidyLabel(label, slug))
  }
  if (candidates.length === 0) return null
  const written = candidates.filter((c) => /[A-Z]/.test(c) || c.includes(' '))
  const pool = written.length ? written : candidates
  return pool.sort((a, b) => b.length - a.length)[0]
}

/** Polymarket's own live block, present on the LIST response and not just on a
 *  single-event query: `live`, `score` ("0-1"), `period` ("1H"/"2H"/"FT"/"VFT")
 *  and `elapsed` ("90").
 *
 *  This was here the whole time and the board was inferring liveness from
 *  resolved markets instead — Portland Thorns read as `board` with no clock
 *  while the same payload said 2H, 59', 1-0. */
interface PmLive {
  live: boolean
  finished: boolean
  minute: number | null
  score: { home: number; away: number } | null
}

function pmLiveOf(ev: Raw): PmLive | null {
  if (!('live' in ev) && !('period' in ev)) return null

  const period = str(ev.period).toUpperCase()
  // VFT is "verified full time"; FT is the whistle before verification. Both
  // are over.
  const finished = ev.ended === true || period === 'FT' || period === 'VFT'
  const live = ev.live === true && !finished

  const raw = str(ev.score)
  const m = raw.match(/^(\d+)\s*-\s*(\d+)$/)
  const score = m ? { home: parseInt(m[1], 10), away: parseInt(m[2], 10) } : null

  const elapsed = parseInt(str(ev.elapsed), 10)
  return {
    live,
    finished,
    minute: live && Number.isFinite(elapsed) && elapsed > 0 ? elapsed : null,
    score,
  }
}

function kickoffOf(ev: Raw): string | null {
  const t = str(ev.startTime).trim()
  return t || null
}

// ── one market, normalised ───────────────────────────────────────────────────

export interface Mkt {
  question: string
  /** Polymarket's own classification: moneyline / totals / spreads / … */
  type: string
  /** The YES (or OVER) leg's price, from the listing's mid. */
  yes: number | null
  bestBid: number | null
  bestAsk: number | null
  /** Gamma's own spread, in probability points. */
  spreadPp: number | null
  yesTokenId: string | null
  liquidity: number
  volume: number
}

function normaliseMarket(raw: Raw): Mkt | null {
  if (raw.closed) return null
  const question = str(raw.question)
  if (!question) return null

  let prices: unknown[] = []
  let names: unknown[] = []
  let tokens: unknown[] = []
  try {
    prices = JSON.parse(str(raw.outcomePrices) || '[]')
    names = JSON.parse(str(raw.outcomes) || '[]')
    tokens = JSON.parse(str(raw.clobTokenIds) || '[]')
  } catch {
    return null
  }

  // "Yes" is not reliably index 0 across every market family, so the leg is
  // found by name and only falls back to the first when the market is not
  // yes/no at all.
  let i = names.findIndex((n) => /^yes$/i.test(str(n)))
  if (i < 0) i = names.findIndex((n) => /^over$/i.test(str(n)))
  if (i < 0) i = 0

  const spread = num(raw.spread)
  return {
    question,
    type: str(raw.sportsMarketType).toLowerCase(),
    yes: num(prices[i]),
    bestBid: num(raw.bestBid),
    bestAsk: num(raw.bestAsk),
    spreadPp: spread != null ? spread * 100 : null,
    yesTokenId: tokens[i] != null ? str(tokens[i]) : null,
    liquidity: num(raw.liquidityNum) ?? num(raw.liquidity) ?? 0,
    volume: num(raw.volumeNum) ?? num(raw.volume) ?? 0,
  }
}

// ── market classification ────────────────────────────────────────────────────
//
// Polymarket stamps every sports market with its own `sportsMarketType`, and it
// is exact where question text is not. Reading the text instead is what graded
// Arsenal v Chelsea as a blown book on $1.18M of volume: "…: Everton FC O/U 2.5
// Corners" matches an "O/U 2.5" pattern perfectly, and a corners book on an
// unopened line quotes 0.02/0.98. The match-goals ladder is `totals` and
// nothing else — team totals, corner totals and half totals all carry their own
// type and none of them is the number this site's tables are keyed to.

/** The match-goals O/U ladder: "A vs. B: O/U 2.5". */
const T_TOTALS = 'totals'
/** The first-half goals ladder: "A vs. B: 1st Half O/U 0.5" — strategy 17's
 *  market, and the one our empirical first-half table measures. */
const T_FIRST_HALF_TOTALS = 'first_half_totals'
const T_MONEYLINE = 'moneyline'

function lineOfTotal(q: string): number | null {
  const m = q.match(/O\/U\s+(\d+\.5)/i)
  return m ? parseFloat(m[1]) : null
}

/** A market with no type at all — older boards, and anything Polymarket has not
 *  classified. Falling back to text is a last resort, so it is deliberately
 *  narrow: the exact shape of the match-goals question and nothing adjacent. */
function untypedMatchTotal(m: Mkt): boolean {
  return (
    m.type === '' &&
    /:\s*O\/U\s+\d+\.5\s*$/i.test(m.question) &&
    !/corner|card|booking/i.test(m.question)
  )
}

function isMatchTotal(m: Mkt): boolean {
  return m.type === T_TOTALS || untypedMatchTotal(m)
}

function isFirstHalfTotal(m: Mkt): boolean {
  return m.type === T_FIRST_HALF_TOTALS
}

function isMoneyline(m: Mkt): boolean {
  return m.type === T_MONEYLINE || (m.type === '' && /\bwin on\b|\bend in a (draw|tie)\b/i.test(m.question))
}

const DRAW_RE = /\bend in a (draw|tie)\b/i

/** The team a "Will <Team> win on <date>?" market is about. */
function moneylineTeam(q: string): string | null {
  const m = q.match(/^Will\s+(.+?)\s+win\b/i)
  return m ? m[1].trim() : null
}

// ── the 1X2 ladder ───────────────────────────────────────────────────────────
//
// A side error here inverts a reading rather than blunting it, so the side is
// resolved with the alias-aware scorer against the fixture title, ambiguity
// fails closed, and market ORDER is never trusted.

function oneX2Of(markets: Mkt[], home: string, away: string) {
  const out: { home: number | null; draw: number | null; away: number | null } =
    { home: null, draw: null, away: null }

  for (const m of markets) {
    if (!isMoneyline(m)) continue

    if (DRAW_RE.test(m.question)) {
      out.draw ??= m.yes
      continue
    }

    const team = moneylineTeam(m.question)
    if (!team) continue
    const sh = teamScore(team, home)
    const sa = teamScore(team, away)
    if (Math.max(sh, sa) < MIN_SIDE_SCORE) continue
    // A name that reads equally well as either side is not a side.
    if (Math.abs(sh - sa) < 0.05) continue
    if (sh > sa) out.home ??= m.yes
    else out.away ??= m.yes
  }
  return out
}

function over25Of(markets: Mkt[]): number | null {
  const g = markets.find((m) => isMatchTotal(m) && lineOfTotal(m.question) === 2.5)
  return g?.yes ?? null
}

// ── board state ──────────────────────────────────────────────────────────────

const SETTLED = 0.99

/** A football match occupies about two and a half hours of wall clock. */
const MATCH_WINDOW_MS = 2.5 * 3600_000

/** How a live reading was arrived at.
 *
 *  `pm`    — Polymarket's own event says so, and carries the score, the half
 *  and the minute. Strongest by a distance: it is the same event the board comes
 *  from, so it needs no name matching and no second request, and it is exactly
 *  what Polymarket's own page shows the trader.
 *  `feed`  — ESPN, for the fixtures Polymarket has not tagged.
 *  `board` — a market on this fixture has resolved, so the match has certainly
 *  started. This is the only moment a Polymarket board timestamps for free.
 *  `clock` — the listed kick-off has passed and nothing else knows anything.
 *  Probable, not certain: Polymarket's listed start ran ~30 minutes early on the
 *  smaller leagues that invalidated 73k of our own observations, and eight hours
 *  late on Sevilla v Rayo. The card says which, because those are different
 *  claims — and since the feed landed, `clock` is what is left over rather than
 *  the usual answer. */
export type LiveSource = 'pm' | 'feed' | 'board' | 'clock'

function pastKickoff(kickoff: string | null): number | null {
  if (!kickoff) return null
  const t = new Date(kickoff).getTime()
  return Number.isFinite(t) && Date.now() >= t ? t : null
}

/** Resolved 1X2 rungs mean full time — but only once the match has kicked off.
 *
 *  Without the clock guard this fired on unopened boards: Cúcuta Deportivo v
 *  AD Pasto was reported FINISHED nine and a half hours before kick-off,
 *  because an unfunded 1X2 ladder quotes every leg at a placeholder extreme and
 *  that is indistinguishable from three settled markets. */
function boardFinished(markets: Mkt[], kickoff: string | null): boolean {
  if (pastKickoff(kickoff) == null) return false
  const prices = markets
    .filter(isMoneyline)
    .map((m) => m.yes)
    .filter((p): p is number => p != null)
  return prices.length >= 2 && prices.every((p) => p >= SETTLED || p <= 1 - SETTLED)
}

/** Has the match started, and how do we know?
 *
 *  The clock is necessary but never sufficient on its own for the strong claim.
 *  Board evidence alone marked a fixture LIVE seven hours before kick-off: an
 *  unopened first-half ladder quotes every rung at the 0.02 placeholder, which
 *  reads exactly like a first half that finished goalless. */
function liveSourceOf(markets: Mkt[], kickoff: string | null): LiveSource | null {
  const t = pastKickoff(kickoff)
  if (t == null) return null

  // Every 1st-half market resolving at once is half time.
  const fh = markets.filter(isFirstHalfTotal)
  if (fh.length > 0 && fh.every((m) => m.yes != null && (m.yes >= SETTLED || m.yes <= 0.02))) {
    return 'board'
  }

  // An Over 0.5 that has already paid means the goals are on the board.
  const over05 = markets.find((m) => isMatchTotal(m) && lineOfTotal(m.question) === 0.5)
  if (over05?.yes != null && over05.yes >= SETTLED) return 'board'

  // Nothing has resolved. Inside the match window that is the ordinary state of
  // a goalless opening twenty minutes, so it is reported — as the weaker claim.
  return Date.now() < t + MATCH_WINDOW_MS ? 'clock' : null
}

// ── book quality ─────────────────────────────────────────────────────────────

/** The thresholds are the ones the 100-game review shipped as `MAX_SPREAD`:
 *  6pp on the full-match and first-half over arms, 3pp on the favourite arm.
 *  `blown` is the 20pp+ bucket — 430 rows quoting an ask near 0.90 that
 *  resolved at 0.529, a lone sell order parked far from any bid rather than a
 *  market. A grade is never inferred from depth: the case that settled it
 *  quoted bid 0.55 / ask 0.99 behind $30,117. */
export function gradeSpread(
  bid: number | null,
  ask: number | null,
  spreadPp: number | null
): BookGrade {
  if (bid == null || ask == null || bid <= 0 || ask <= 0) return 'one-sided'

  // A decided token quotes tight around nothing: 0.001 / 0.009 is a 0.8pp
  // spread and would grade CLEAN, which reads as "there is a price here" when
  // what is left is a settled market with no bet in it. Outside this band the
  // decimal stops describing a bet anyone would place.
  const mid = (bid + ask) / 2
  if (mid <= TRADEABLE_BAND[0] || mid >= TRADEABLE_BAND[1]) return 'settled'

  const s = spreadPp ?? (ask - bid) * 100
  if (!Number.isFinite(s)) return 'unknown'
  if (s >= 20) return 'blown'
  if (s > 6) return 'wide'
  return 'clean'
}

/** Which market to grade. Over 2.5 first — it is the deepest football book on
 *  Polymarket and the line every measured table on this site is keyed to — then
 *  the draw, the 1X2 rung that stays quoted longest. */
function probeMarketOf(markets: Mkt[]): Mkt | null {
  const over25 = markets.find((m) => isMatchTotal(m) && lineOfTotal(m.question) === 2.5)
  const draw = markets.find((m) => isMoneyline(m) && DRAW_RE.test(m.question))
  const anyTotal = markets.find(isMatchTotal)
  return over25 ?? draw ?? anyTotal ?? null
}

function gammaBook(markets: Mkt[]): BookQuality | null {
  const m = probeMarketOf(markets)
  if (!m) return null
  const derived =
    m.bestBid != null && m.bestAsk != null ? (m.bestAsk - m.bestBid) * 100 : null
  return {
    market: m.question,
    bid: m.bestBid,
    ask: m.bestAsk,
    spreadPp: m.spreadPp ?? derived,
    askDepthUsd: null,
    source: 'gamma',
    grade: gradeSpread(m.bestBid, m.bestAsk, m.spreadPp),
  }
}

/** A live top-of-book read for the fixtures where a stale quote costs most.
 *  Returns null rather than a worse grade when the round trip fails — a probe
 *  that could not be made is not evidence of a bad book. */
export async function refreshBook(markets: Mkt[]): Promise<BookQuality | null> {
  const m = probeMarketOf(markets)
  if (!m?.yesTokenId) return null
  const book = await fetchBook(m.yesTokenId)
  if (!book) return null
  return {
    market: m.question,
    bid: book.bid,
    ask: book.ask,
    spreadPp: book.bid != null && book.ask != null ? (book.ask - book.bid) * 100 : null,
    askDepthUsd: book.askDepthUsd,
    source: 'clob',
    grade: gradeSpread(book.bid, book.ask, null),
  }
}

// ── assembly ─────────────────────────────────────────────────────────────────

export function buildFixtures(
  events: Raw[],
  espn: EspnLive[] = []
): { fixtures: ScoutFixture[]; marketsBySlug: Map<string, Mkt[]> } {
  const now = Date.now()
  const from = now - WINDOW_BACK_H * 3600_000
  const to = now + WINDOW_FWD_H * 3600_000

  const byFixture = new Map<string, Raw[]>()
  for (const ev of events) {
    const title = str(ev.title)
    if (!teamsOf(title)) continue
    const ko = kickoffOf(ev)
    // An event with no kick-off is an outright ("Premier League: Winner"),
    // however much its title looks like a fixture.
    if (!ko) continue
    const t = new Date(ko).getTime()
    if (!Number.isFinite(t) || t < from || t > to) continue

    const key = fixtureKey(title).toLowerCase()
    const list = byFixture.get(key)
    if (list) list.push(ev)
    else byFixture.set(key, [ev])
  }

  const fixtures: ScoutFixture[] = []
  const marketsBySlug = new Map<string, Mkt[]>()

  for (const siblings of Array.from(byFixture.values())) {
    const canonical =
      siblings.find((e) => !str(e.title).includes(' - ')) ??
      siblings.slice().sort((a, b) => (num(b.volume) ?? 0) - (num(a.volume) ?? 0))[0]

    const teams = teamsOf(str(canonical.title))
    if (!teams) continue
    const slug = str(canonical.slug)
    if (!slug) continue

    const markets: Mkt[] = []
    const seen = new Set<string>()
    let volumeUsd = 0
    let liquidityUsd = 0
    for (const ev of siblings) {
      volumeUsd += num(ev.volume) ?? 0
      liquidityUsd += num(ev.liquidity) ?? 0
      for (const raw of (ev.markets as Raw[]) ?? []) {
        const m = normaliseMarket(raw)
        if (!m) continue
        const key = str(raw.conditionId) || m.question
        if (seen.has(key)) continue
        seen.add(key)
        markets.push(m)
      }
    }
    if (markets.length === 0) continue

    const kickoff = kickoffOf(canonical)

    // Polymarket first: same event, no name matching, no extra request, and it
    // is what the trader sees on Polymarket's own page. Any sibling can carry
    // the block, so the first one that does wins — the canonical event is not
    // always the one they tag.
    const pm = siblings.map(pmLiveOf).find((x) => x !== null) ?? null
    // ESPN second, for fixtures Polymarket has not tagged at all.
    const feed = !pm && espn.length ? matchEspn(teams.home, teams.away, espn) : null
    const feedLive = feed != null && !feed.finished

    const finished = pm?.finished ?? feed?.finished ?? boardFinished(markets, kickoff)
    const liveSource: LiveSource | null = finished
      ? null
      : pm?.live
        ? 'pm'
        : feedLive
          ? 'feed'
          : liveSourceOf(markets, kickoff)

    fixtures.push({
      slug,
      home: teams.home,
      away: teams.away,
      competition: competitionOf(canonical),
      kickoff,
      live: liveSource != null,
      liveSource,
      minute: pm?.live ? pm.minute : feedLive ? feed!.minute : null,
      score: pm?.score ?? (feed ? { home: feed.homeGoals, away: feed.awayGoals } : null),
      finished,
      markets: markets.length,
      volumeUsd,
      liquidityUsd,
      oneX2: oneX2Of(markets, teams.home, teams.away),
      over25: over25Of(markets),
      hasTotals: markets.some(isMatchTotal),
      hasFirstHalf: markets.some(isFirstHalfTotal),
      book: gammaBook(markets),
    })
    marketsBySlug.set(slug, markets)
  }

  return { fixtures, marketsBySlug }
}

/** The order the board opens in: the games people are actually betting.
 *
 *  This used to lead with anything in play, which put a $13k J-League board
 *  above Everton v Manchester United at $4.9M — backwards for anyone opening
 *  the page to see what is on today. Money traded is the honest proxy for
 *  "hot", and it also settles the quality question by itself: a fixture with
 *  millions through it has a real two-sided book by construction, so the book
 *  grade can go back to being a column rather than the pitch.
 *
 *  A live game still gets a nudge, not a promotion — a tenth of the board's
 *  volume is enough to lift an in-play fixture past a slightly bigger one that
 *  has not kicked off, and nowhere near enough to lift a small one past a big
 *  one. Finished games sort last whatever they traded. */
const LIVE_BOOST = 1.1

export function heatOf(f: ScoutFixture): number {
  return f.volumeUsd * (f.live ? LIVE_BOOST : 1)
}

export function rankFixtures(fixtures: ScoutFixture[]): ScoutFixture[] {
  return fixtures.slice().sort((a, b) => {
    if (a.finished !== b.finished) return a.finished ? 1 : -1
    return heatOf(b) - heatOf(a)
  })
}
