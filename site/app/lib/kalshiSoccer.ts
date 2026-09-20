/** Kalshi's football, swept whole.
 *
 *  Polymarket's soccer board comes from one paged Gamma sweep. Kalshi has no
 *  equivalent: `/events` takes ONE `series_ticker` at a time (a comma list
 *  returns nothing, and there is no category or tag filter), and football is
 *  spread across 139 separate game series — one per competition. So the whole
 *  board costs 139 requests.
 *
 *  ⚠️ It also rate-limits hard. Measured 2026-09-20 on this exact sweep:
 *
 *      10 concurrent, no pacing   →  112 of 139 refused with 429
 *       6 workers, 0.10s apart    →  105 retries, 27.9s
 *       5 workers, 0.15s apart    →   57 retries, 31.1s
 *       4 workers, 0.25s apart    →    0 retries, 34.7s   ← what ships
 *
 *     Pushing the pace does not make it faster, because every 429 costs a
 *     backoff and a second request. 0.25s between starts is the floor, and
 *     34s is therefore the honest cost of a full sweep.
 *
 *  🔑 Which is why the index and the prices are separate tiers. The INDEX —
 *     which fixture exists, where, and under which market tickers — changes
 *     when Kalshi lists a game, so it is swept rarely and cached for 15
 *     minutes. The PRICES change every tick, so they are re-read on their own
 *     45-second clock through `/markets?tickers=`, which DOES take a batch and
 *     costs about three requests for the whole board.
 *
 *  Nothing here can place an order. Kalshi's market data is public: no key, no
 *  account, read-only.
 */

import { unstable_cache } from 'next/cache'
import { quoteOf, type Quote } from './venues'
import { sameFixture } from './venueMatch'
import { etDateOf } from './etDate'
import type {
  KalshiFixture,
  KalshiLeg,
  KalshiSide,
  KalshiSoccerIndex,
} from './kalshiSoccerTypes'

export type { KalshiFixture, KalshiLeg, KalshiSide, KalshiSoccerIndex }

const KALSHI_API = 'https://api.elections.kalshi.com/trade-api/v2'

/** Between the START of one request and the next. Not a delay after: the
 *  measurement above is a rate, and four workers sharing a 0.25s gate is what
 *  produced the zero-refusal run. */
const GAP_MS = 250
const WORKERS = 4
const RETRIES = 3
const TIMEOUT_MS = 15_000

/** How many tickers go in one `/markets?tickers=` call. */
const QUOTE_BATCH = 100

// ── the throttle ─────────────────────────────────────────────────────────────

let nextSlot = 0

async function paced<T>(fn: () => Promise<T>): Promise<T> {
  const now = Date.now()
  const at = Math.max(now, nextSlot)
  nextSlot = at + GAP_MS
  if (at > now) await new Promise((r) => setTimeout(r, at - now))
  return fn()
}

async function getJson<T>(path: string): Promise<T> {
  let lastErr: unknown
  for (let attempt = 0; attempt < RETRIES; attempt++) {
    try {
      return await paced(async () => {
        const res = await fetch(`${KALSHI_API}${path}`, {
          signal: AbortSignal.timeout(TIMEOUT_MS),
          cache: 'no-store',
        })
        if (res.status === 429) throw new Error('429')
        if (!res.ok) throw new Error(`kalshi answered ${res.status}`)
        return (await res.json()) as T
      })
    } catch (e) {
      lastErr = e
      if (e instanceof Error && e.message === '429') {
        await new Promise((r) => setTimeout(r, 500 * (attempt + 1)))
        continue
      }
      throw e
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error('kalshi unreachable')
}

/** `WORKERS` at a time over a list, preserving order. The pacer above is what
 *  actually limits the rate; this just stops 139 promises opening at once. */
async function pool<T, R>(items: T[], fn: (x: T) => Promise<R>): Promise<R[]> {
  const out = new Array<R>(items.length)
  let i = 0
  await Promise.all(
    Array.from({ length: Math.min(WORKERS, items.length) }, async () => {
      for (;;) {
        const k = i++
        if (k >= items.length) return
        out[k] = await fn(items[k])
      }
    })
  )
  return out
}

// ── series discovery ─────────────────────────────────────────────────────────

interface KSeries {
  ticker: string
  title?: string
  category?: string
  tags?: string[]
}

/** Every soccer GAME series.
 *
 *  ⚠️ Filtered on the `tags` array, never on a substring of the blob: a naive
 *     text match pulls in a Diana Ross NYE market carrying a stray "Soccer"
 *     tag beside "Music". And only the `GAME` suffix — Kalshi carries ~1,400
 *     soccer series in total (totals, BTTS, spreads, correct score, golden
 *     boot, manager sackings), and this board is the 1X2. */
export interface SoccerSeries {
  ticker: string
  /** The competition, from the series title: "Serie A", "Liga MX". */
  title: string
  /** Everything between the KX prefix and the family suffix. `KXMLSGAME` and
   *  `KXMLSTOTAL` share the stem `MLS`, which is how the goals ladder for a
   *  competition is found once its 1X2 sweep says the competition is on. */
  stem: string
}

async function fetchSoccerSeries(): Promise<{ game: SoccerSeries[]; totals: [string, string][] }> {
  const d = await getJson<{ series?: KSeries[] }>('/series?category=Sports')
  const soccer = (d.series ?? []).filter(
    (s) => s.category === 'Sports' && (s.tags ?? []).includes('Soccer') && s.ticker
  )
  const game = soccer
    .filter((s) => /GAME$/.test(s.ticker))
    .map((s) => ({
      ticker: s.ticker,
      title: (s.title ?? s.ticker).replace(/\s+Game$/i, ''),
      stem: s.ticker.replace(/^KX/, '').replace(/GAME$/, ''),
    }))
    .sort((a, b) => a.ticker.localeCompare(b.ticker))

  // The match-goals ladder, and only that one. TEAMTOTAL is one team's goals
  // and 1HTOTAL is the first half; neither is the number this site's tables
  // are keyed to, and folding them in is the Polymarket corners bug wearing
  // Kalshi's clothes.
  const totals: [string, string][] = []
  for (const s of soccer) {
    if (!/TOTAL$/.test(s.ticker) || /(TEAMTOTAL|1HTOTAL)$/.test(s.ticker)) continue
    totals.push([s.ticker.replace(/^KX/, '').replace(/TOTAL$/, ''), s.ticker])
  }
  return { game, totals }
}

/** Six hours. A competition does not appear or vanish inside a day, and this
 *  is the one call that must succeed before any of the other 139 can. */
const soccerSeries = unstable_cache(fetchSoccerSeries, ['kalshi-soccer-series-v1'], {
  revalidate: 6 * 3600,
  tags: ['kalshi-soccer'],
})

// ── one event ────────────────────────────────────────────────────────────────

interface KMarket {
  ticker: string
  yes_sub_title?: string
  yes_bid_dollars?: string
  yes_ask_dollars?: string
  yes_ask_size_fp?: string
  volume_fp?: string
  occurrence_datetime?: string
  status?: string
  /** On a totals ladder, the line itself: 2.5 on "Over 2.5 goals scored".
   *  Read this, never the sub-title's text. */
  floor_strike?: number
}

interface KEvent {
  event_ticker: string
  title?: string
  markets?: KMarket[]
}

const num = (v: string | number | null | undefined): number | null => {
  const x = typeof v === 'number' ? v : parseFloat(v ?? '')
  return Number.isFinite(x) ? x : null
}

function splitTitle(title: string): { home: string; away: string } | null {
  const m = /^\s*(.+?)\s+vs\.?\s+(.+?)\s*$/i.exec(title)
  if (!m) return null
  const home = m[1].trim()
  const away = m[2].trim()
  return home && away ? { home, away } : null
}

const DRAW_RE = /^(tie|draw)$/i

const MONTH: Record<string, string> = {
  JAN: '01', FEB: '02', MAR: '03', APR: '04', MAY: '05', JUN: '06',
  JUL: '07', AUG: '08', SEP: '09', OCT: '10', NOV: '11', DEC: '12',
}

/** The Eastern date a Kalshi event is filed under, from its own ticker:
 *  `KXBRASILEIROGAME-26SEP20VITCRU` → `20260920`.
 *
 *  🔑 This, not `occurrence_datetime`, is what the cross-venue join uses. The
 *     latter is the expected settlement — kick-off plus about three hours —
 *     and reading it as a start time silently drops every competition whose
 *     games are expected to run longer. */
export function kalshiEtDate(eventTicker: string): string | null {
  const m = /-(\d{2})([A-Z]{3})(\d{2})/.exec(eventTicker)
  return m && MONTH[m[2]] ? `20${m[1]}${MONTH[m[2]]}${m[3]}` : null
}

function legQuote(m: KMarket): Quote {
  const ask = num(m.yes_ask_dollars)
  const size = num(m.yes_ask_size_fp)
  return quoteOf(num(m.yes_bid_dollars), ask, ask != null && size != null ? ask * size : null)
}

/** One Kalshi event → one fixture, or nothing.
 *
 *  Fails closed on every ambiguity. A side error on a football board does not
 *  blunt the reading, it inverts it: the home price shown against the away
 *  team is worse than no price at all. So the tie leg must be named as one,
 *  both teams must resolve to different sides, and anything else is dropped. */
export function parseKalshiEvent(
  ev: KEvent,
  series: string,
  competition: string
): KalshiFixture | null {
  const teams = splitTitle(String(ev.title ?? ''))
  const markets = (ev.markets ?? []).filter((m) => (m.status ?? 'active') !== 'settled')
  if (!teams || markets.length < 2) return null

  const legs: Record<KalshiSide, KalshiLeg | null> = { home: null, draw: null, away: null }
  const put = (side: KalshiSide, m: KMarket) => {
    // Two markets claiming one side is a ladder we cannot read. Drop the event.
    if (legs[side]) throw new Error('ambiguous')
    legs[side] = { ticker: m.ticker, label: String(m.yes_sub_title ?? ''), quote: legQuote(m) }
  }

  try {
    for (const m of markets) {
      const label = String(m.yes_sub_title ?? '').trim()
      if (!label) return null
      if (DRAW_RE.test(label)) {
        put('draw', m)
        continue
      }
      // Kalshi writes the sub-title in the same short form as its own title,
      // so this is an exact-ish comparison within ONE feed — no cross-venue
      // scoring needed here. The cross-venue join happens in `matchVenues`.
      const h = sameName(label, teams.home)
      const a = sameName(label, teams.away)
      if (h === a) return null
      put(h ? 'home' : 'away', m)
    }
  } catch {
    return null
  }
  if (!legs.home || !legs.away) return null

  const settles = markets.map((m) => m.occurrence_datetime).find((x) => x) ?? null
  const volume = markets.reduce((s, m) => s + (num(m.volume_fp) ?? 0), 0)
  const eventTicker = String(ev.event_ticker ?? '')

  return {
    eventTicker,
    series,
    competition,
    home: teams.home,
    away: teams.away,
    settlesAt: settles ? new Date(settles).toISOString() : null,
    etDate: kalshiEtDate(eventTicker),
    url: `https://kalshi.com/markets/${series.toLowerCase()}/${String(ev.event_ticker ?? '').toLowerCase()}`,
    legs,
    totals: {},
    volume: volume > 0 ? volume : null,
  }
}

/** A "Home vs Away: Total Goals" event → the over leg at each line.
 *
 *  Keyed on `floor_strike`, never on the sub-title's wording. The line is the
 *  market's own field; the sentence around it is prose that can change. */
export function parseKalshiTotals(ev: KEvent): {
  teams: { home: string; away: string }
  totals: Record<string, KalshiLeg>
  volume: number
} | null {
  const title = String(ev.title ?? '').replace(/:\s*Total Goals\s*$/i, '')
  const teams = splitTitle(title)
  if (!teams) return null
  const markets = (ev.markets ?? []).filter((m) => (m.status ?? 'active') !== 'settled')
  const totals: Record<string, KalshiLeg> = {}
  for (const m of markets) {
    const line = typeof m.floor_strike === 'number' ? m.floor_strike : null
    if (line == null || !/^over\b/i.test(String(m.yes_sub_title ?? ''))) continue
    const key = line.toFixed(1)
    // Two rungs on one line is a ladder we cannot read.
    if (totals[key]) return null
    totals[key] = { ticker: m.ticker, label: String(m.yes_sub_title ?? ''), quote: legQuote(m) }
  }
  if (Object.keys(totals).length === 0) return null
  const volume = markets.reduce((s, m) => s + (num(m.volume_fp) ?? 0), 0)
  return { teams, totals, volume }
}

/** Two spellings inside Kalshi's own feed. Deliberately not the cross-venue
 *  scorer: here the sub-title is drawn from the same string the title is, so
 *  containment either way is the whole test. */
function sameName(a: string, b: string): boolean {
  const n = (s: string) =>
    s
      .toLowerCase()
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9]/g, '')
  const x = n(a)
  const y = n(b)
  if (!x || !y) return false
  return x === y || x.includes(y) || y.includes(x)
}

// ── the index ────────────────────────────────────────────────────────────────

async function sweepIndex(): Promise<KalshiSoccerIndex> {
  const { game, totals } = await soccerSeries()
  const totalOf = new Map(totals)
  let ok = 0

  const perSeries = await pool(game, async (s) => {
    const evs = await events(s.ticker)
    if (evs == null) return []
    ok++
    return evs.map((ev) => parseKalshiEvent(ev, s.ticker, s.title)).filter(notNull)
  })
  const fixtures = perSeries.flat()

  // 🔑 The goals ladder is swept only for the competitions the 1X2 sweep just
  //    found a fixture in. Kalshi has 138 soccer TOTAL series against 139 GAME
  //    ones, so asking for all of them would double a 34-second sweep to buy
  //    nothing — on a live board about 25 competitions are actually playing.
  const withTotals = game
    .filter((_, i) => perSeries[i].length > 0)
    .map((s) => totalOf.get(s.stem))
    .filter(notNull)
  const ladders = await pool(withTotals, async (ticker) => (await events(ticker)) ?? [])

  // 🔑 The 1X2 and the goals ladder for one fixture share an event ticker but
  //    for the family — KXMLSGAME-26SEP20MIASD ↔ KXMLSTOTAL-26SEP20MIASD — so
  //    this join is an exact key, not a name score. The teams are checked
  //    anyway, because an exact key that is wrong is the worst kind.
  const bySuffix = new Map<string, KalshiFixture>()
  for (const f of fixtures) bySuffix.set(eventSuffix(f.eventTicker), f)
  for (const ev of ladders.flat()) {
    const parsed = parseKalshiTotals(ev)
    if (!parsed) continue
    const hit = bySuffix.get(eventSuffix(String(ev.event_ticker ?? '')))
    if (hit && sameName(hit.home, parsed.teams.home) && sameName(hit.away, parsed.teams.away)) {
      hit.totals = parsed.totals
      // The ladder's own contracts count too, or Kalshi's volume would be
      // three legs against Polymarket's whole board.
      hit.volume = (hit.volume ?? 0) + parsed.volume
    }
  }

  return {
    fixtures,
    generatedAt: new Date().toISOString(),
    series: game.length + withTotals.length,
    seriesOk: ok,
  }
}

function notNull<T>(x: T | null | undefined): x is T {
  return x != null
}

/** Everything after the series: `KXMLSGAME-26SEP20MIASD` → `26SEP20MIASD`. */
function eventSuffix(ticker: string): string {
  return ticker.split('-').slice(1).join('-')
}

/** One series' open events, or null when Kalshi would not answer. Null and an
 *  empty list are different claims and the caller counts them apart. */
async function events(seriesTicker: string): Promise<KEvent[] | null> {
  try {
    const qs = new URLSearchParams({
      series_ticker: seriesTicker,
      status: 'open',
      limit: '200',
      with_nested_markets: 'true',
    })
    const d = await getJson<{ events?: KEvent[] }>(`/events?${qs}`)
    return d.events ?? []
  } catch {
    // One competition refusing costs that competition, never the board.
    return null
  }
}

/** ⚠️ The key carries a version because the Data Cache outlives a deploy, and
 *     a fixture written under an older shape is served straight into the new
 *     code. Bump it whenever `KalshiFixture` changes.
 *
 *  Fifteen minutes. Long enough that the 34-second sweep runs about four times
 *  an hour across the whole deployment, short enough that a game listed at
 *  lunchtime is on the board before kick-off. Next's Data Cache serves the
 *  stale index while it rebuilds, so only the very first call ever waits. */
const indexShared = unstable_cache(sweepIndex, ['kalshi-soccer-index-v2'], {
  revalidate: 900,
  tags: ['kalshi-soccer'],
})

// ── the prices ───────────────────────────────────────────────────────────────

/** Fresh quotes for a set of market tickers.
 *
 *  `/markets?tickers=a,b,c` DOES take a batch, which is what makes a 45-second
 *  price clock affordable on top of a 15-minute index. */
export async function fetchKalshiQuotes(tickers: string[]): Promise<Map<string, Quote>> {
  const out = new Map<string, Quote>()
  const uniq = Array.from(new Set(tickers.filter(Boolean)))
  const batches: string[][] = []
  for (let i = 0; i < uniq.length; i += QUOTE_BATCH) batches.push(uniq.slice(i, i + QUOTE_BATCH))
  const pages = await pool(batches, async (batch) => {
    try {
      const qs = new URLSearchParams({ tickers: batch.join(','), limit: String(QUOTE_BATCH) })
      const d = await getJson<{ markets?: KMarket[] }>(`/markets?${qs}`)
      return d.markets ?? []
    } catch {
      return []
    }
  })
  for (const m of pages.flat()) out.set(m.ticker, legQuote(m))
  return out
}

/** The index, re-priced. The index may be up to fifteen minutes old; the
 *  numbers on it are never more than the price clock's age.
 *
 *  A failed re-price leaves the index's own quotes in place rather than
 *  emptying the column — a round trip that could not be made is not evidence
 *  that a book is gone. */
async function pricedIndex(): Promise<KalshiSoccerIndex> {
  const idx = await indexShared()
  const tickers: string[] = []
  for (const f of idx.fixtures) {
    for (const leg of [f.legs.home, f.legs.draw, f.legs.away]) if (leg) tickers.push(leg.ticker)
    for (const leg of Object.values(f.totals)) tickers.push(leg.ticker)
  }
  if (tickers.length === 0) return idx

  const quotes = await fetchKalshiQuotes(tickers).catch(() => new Map<string, Quote>())
  if (quotes.size === 0) return idx

  const fresh = (leg: KalshiLeg | null) =>
    leg ? { ...leg, quote: quotes.get(leg.ticker) ?? leg.quote } : null
  const fixtures = idx.fixtures.map((f) => ({
    ...f,
    legs: { home: fresh(f.legs.home), draw: fresh(f.legs.draw), away: fresh(f.legs.away) },
    totals: Object.fromEntries(
      Object.entries(f.totals).map(([line, leg]) => [line, fresh(leg) as KalshiLeg])
    ),
  }))
  return { ...idx, fixtures, generatedAt: new Date().toISOString() }
}

const TTL_MS = 45_000

const pricedShared = unstable_cache(pricedIndex, ['kalshi-soccer-priced-v2'], {
  revalidate: TTL_MS / 1000,
  tags: ['kalshi-soccer'],
})

let l1: { at: number; idx: KalshiSoccerIndex } | null = null
// eslint-disable-next-line prefer-const
let inFlight: Promise<KalshiSoccerIndex> | null = null

function refresh(): Promise<KalshiSoccerIndex> {
  if (inFlight) return inFlight
  inFlight = pricedShared()
    .then((idx) => {
      l1 = { at: Date.now(), idx }
      return idx
    })
    .finally(() => {
      inFlight = null
    })
  return inFlight
}

/** What every caller should use.
 *
 *  L1 is this instance; L2 is the Data Cache the instances share — the same
 *  two-layer shape `scoutCache` uses, and for the same reason: a module cache
 *  alone described a warm experience almost nobody had.
 *
 *  🔑 Stale beats waiting. A cold sweep is ~34 seconds, and an index whose
 *     prices are a minute old is a far better answer than a spinner for most
 *     of a minute. So anything already in L1 is returned IMMEDIATELY and the
 *     refresh runs behind it; only an instance that has never swept waits.
 *     The board is built to render without this column anyway, so the one
 *     caller that does wait still gets a board.
 */
export async function getKalshiSoccer(): Promise<KalshiSoccerIndex> {
  if (l1 && Date.now() - l1.at < TTL_MS) return l1.idx
  if (l1) {
    // Stale, but real. Kick the refresh off and answer now.
    void refresh().catch(() => {
      /* the next caller tries again; the stale index stands until then */
    })
    return l1.idx
  }
  try {
    return await refresh()
  } catch (e) {
    const stale = l1 as { at: number; idx: KalshiSoccerIndex } | null
    if (stale) return stale.idx
    throw e
  }
}

/** Kalshi's side of ONE fixture, for the Game Center.
 *
 *  ⚠️ Time-budgeted on purpose. A cold index is a ~34-second sweep, and a
 *     fixture page must not wait for it — it renders Polymarket's board with
 *     no Kalshi column instead, which is the honest degradation. In practice
 *     the index is warm: the football board asks for it every minute.
 */
export async function findKalshiFixture(
  fixture: { home: string; away: string; kickoff: string | null },
  budgetMs = 4000
): Promise<KalshiFixture | null> {
  if (!fixture.kickoff) return null
  const idx = await Promise.race([
    getKalshiSoccer().catch(() => null),
    new Promise<null>((r) => setTimeout(() => r(null), budgetMs)),
  ])
  if (!idx) return null
  // The same one-to-one rule the board uses: the ET date both schedules file
  // the game under, the alias-aware scorer on both names, and the crossed
  // orientation tested. Two candidates is not a match.
  const hits = idx.fixtures.filter((k) =>
    sameFixture({ ...fixture, etDate: etDateOf(fixture.kickoff) }, k)
  )
  return hits.length === 1 ? hits[0] : null
}
