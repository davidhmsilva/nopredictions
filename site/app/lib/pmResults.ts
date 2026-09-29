// Finished Polymarket football fixtures: the 1X2 price at kick-off, and what
// happened. The "Yesterday" board, /results/<day>, and the daily cron that
// fills `pm_results` (db/059).
//
// Server only (Postgres + outbound fetch). Pages import the TYPES from here.
//
// Where each number comes from, and why:
// - The fixture list is Gamma's CLOSED soccer events for one UTC day, bounded
//   by `end_date_min/max` as plain dates (a fixture's endDate IS its kick-off;
//   see CLAUDE.md, "Gamma's four traps").
// - The price is the CLOB's own price history for each of the three "Yes"
//   tokens, read at the last point at or before kick-off. Measured on
//   2026-09-27: a point inside the kick-off minute on 97 of 98 fixtures, the
//   three summing to 1.005 at the median. They are normalised to sum to 1.
// - The outcome is Polymarket's RESOLUTION of the three questions — exactly
//   one "Yes" paid — never read off the score. Extra time and penalties change
//   what a score means; the resolution is what a trader was paid on.
// - The favourite's record at this price is computed once, when the row is
//   written, from games BEFORE this kick-off only.

import { getSql } from './db'
import { competitionOf, pmLiveOf } from './scout'
import { devig3, n, resolveTeams } from './teamform'

const GAMMA = 'https://gamma-api.polymarket.com'
const CLOB = 'https://clob.polymarket.com'
const PAGE = 100
const MAX_PAGES = 15
/** Six to eight at once was clean on the history endpoint (0 failures in 294). */
const HISTORY_WORKERS = 8
/** How far either side of the favourite's price counts as "this price". */
export const FAV_BAND = 0.075
/** A favourite is the side the market gave at least this much. */
const FAV_MIN = 0.5
/** How many earlier games at this price the record looks back over. */
const FAV_LOOKBACK = 100

type Raw = Record<string, unknown>
type Side = 'home' | 'draw' | 'away'

export interface FavRecord {
  side: 'home' | 'away'
  team: string
  p: number
  band: [number, number]
  /** Earlier Polymarket fixtures of this team at this price (by Polymarket's name). */
  pm: RecordCount | null
  /** Earlier league games of this club at this price, from our closing odds. */
  db: (RecordCount & { from: string | null }) | null
}

export interface RecordCount {
  n: number
  w: number
  d: number
  l: number
  /** Wins the prices of those games expected. */
  exp: number
}

export interface PmResult {
  slug: string
  kickoff: string
  title: string
  home: string
  away: string
  competition: string | null
  p: { home: number; draw: number; away: number } | null
  rawSum: number | null
  priceAt: string | null
  outcome: Side | null
  score: string | null
  period: string | null
  volume: number | null
  fav: FavRecord | null
}

// ── fetching ─────────────────────────────────────────────────────────────────

async function getJson(url: string, timeoutMs = 15000): Promise<unknown> {
  const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), cache: 'no-store' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} ${url}`)
  return res.json()
}

const str = (v: unknown) => (v == null ? '' : String(v))

function parseList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String)
  try {
    const j = JSON.parse(str(v))
    return Array.isArray(j) ? j.map(String) : []
  } catch {
    return []
  }
}

/** Closed soccer events whose kick-off falls on `day` (UTC, YYYY-MM-DD). */
async function closedEventsOn(day: string): Promise<Raw[]> {
  const next = addDays(day, 1)
  const out: Raw[] = []
  for (let page = 0; page < MAX_PAGES; page++) {
    const body = await getJson(
      `${GAMMA}/events?closed=true&limit=${PAGE}&offset=${page * PAGE}&tag_slug=soccer` +
        `&end_date_min=${day}&end_date_max=${next}&order=startTime&ascending=true`
    )
    if (!Array.isArray(body) || body.length === 0) break
    out.push(...(body as Raw[]))
    if (body.length < PAGE) break
  }
  const lo = Date.parse(`${day}T00:00:00Z`)
  const hi = lo + 86_400_000
  return out.filter((e) => {
    const t = Date.parse(str(e.startTime))
    return Number.isFinite(t) && t >= lo && t < hi
  })
}

interface Leg {
  side: Side
  token: string
  won: boolean | null
}

/** The three 1X2 questions of a fixture event, by side. Sides come from the
 *  question naming the team exactly as the title does ("Will Germany win on
 *  2026-09-27?"), never from market order. Anything short of three distinct
 *  sides returns null. */
function legsOf(ev: Raw, home: string, away: string): Leg[] | null {
  const h = home.toLowerCase()
  const a = away.toLowerCase()
  const legs: Leg[] = []
  for (const m of (ev.markets as Raw[]) ?? []) {
    if (str(m.sportsMarketType) !== 'moneyline') continue
    const q = str(m.question).toLowerCase()
    const side: Side | null = q.includes('draw')
      ? 'draw'
      : q.startsWith(`will ${h} win`)
        ? 'home'
        : q.startsWith(`will ${a} win`)
          ? 'away'
          : null
    if (!side || legs.some((l) => l.side === side)) return null
    const token = parseList(m.clobTokenIds)[0]
    if (!token) return null
    const prices = parseList(m.outcomePrices)
    const resolved = str(m.umaResolutionStatus) === 'resolved' || m.closed === true
    legs.push({
      side,
      token,
      won: resolved && prices.length ? prices[0] === '1' : null,
    })
  }
  return legs.length === 3 ? legs : null
}

function splitTitle(title: string): { home: string; away: string } | null {
  const m = title.match(/^(.+?)\s+vs\.?\s+(.+)$/i)
  if (!m) return null
  return { home: m[1].trim(), away: m[2].trim() }
}

async function priceAt(token: string, kickoffSec: number): Promise<{ p: number; t: number } | null> {
  const url =
    `${CLOB}/prices-history?market=${encodeURIComponent(token)}` +
    `&startTs=${kickoffSec - 3 * 3600}&endTs=${kickoffSec + 60}&fidelity=1`
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const j = (await getJson(url)) as { history?: Array<{ t: number; p: number }> }
      const pre = (j.history ?? []).filter((x) => x.t <= kickoffSec)
      const last = pre[pre.length - 1]
      return last ? { p: last.p, t: last.t } : null
    } catch {
      await new Promise((r) => setTimeout(r, 600 * (attempt + 1)))
    }
  }
  return null
}

async function pool<T, R>(items: T[], workers: number, fn: (x: T) => Promise<R>): Promise<R[]> {
  const out = new Array<R>(items.length)
  let next = 0
  await Promise.all(
    Array.from({ length: Math.min(workers, items.length) }, async () => {
      while (next < items.length) {
        const i = next++
        out[i] = await fn(items[i])
      }
    })
  )
  return out
}

/** Every fixture of one UTC day, priced at kick-off. `skip` holds slugs that
 *  already have prices stored, so a re-run only re-reads their resolution. */
export async function computeDay(day: string, skip: Set<string> = new Set()): Promise<PmResult[]> {
  const events = await closedEventsOn(day)
  type Job = { ev: Raw; home: string; away: string; legs: Leg[]; ko: number }
  const jobs: Job[] = []
  for (const ev of events) {
    const t = splitTitle(str(ev.title))
    if (!t) continue
    const legs = legsOf(ev, t.home, t.away)
    if (!legs) continue
    jobs.push({ ev, home: t.home, away: t.away, legs, ko: Math.floor(Date.parse(str(ev.startTime)) / 1000) })
  }

  const needPrices = jobs.filter((j) => !skip.has(str(j.ev.slug)))
  const flat = needPrices.flatMap((j) => j.legs.map((l) => ({ token: l.token, ko: j.ko })))
  const prices = await pool(flat, HISTORY_WORKERS, (x) => priceAt(x.token, x.ko))
  const priceByToken = new Map<string, { p: number; t: number } | null>()
  flat.forEach((x, i) => priceByToken.set(x.token, prices[i]))

  return jobs.map(({ ev, home, away, legs }) => {
    const slug = str(ev.slug)
    const got = legs.map((l) => priceByToken.get(l.token) ?? null)
    let p: PmResult['p'] = null
    let rawSum: number | null = null
    let at: string | null = null
    if (!skip.has(slug) && got.every((g) => g && g.p > 0)) {
      rawSum = got.reduce((s, g) => s + (g as { p: number }).p, 0)
      const by = Object.fromEntries(legs.map((l, i) => [l.side, (got[i] as { p: number }).p / (rawSum as number)]))
      p = { home: by.home, draw: by.draw, away: by.away }
      at = new Date(Math.min(...got.map((g) => (g as { t: number }).t)) * 1000).toISOString()
    }
    // A cancelled fixture has no result, whatever its markets settled to
    // (China v New Zealand, 2026-09-27: period CAN, one question paid).
    const cancelled = str(ev.period).toUpperCase() === 'CAN'
    const winners = cancelled ? [] : legs.filter((l) => l.won === true)
    const live = pmLiveOf(ev)
    return {
      slug,
      kickoff: new Date(Date.parse(str(ev.startTime))).toISOString(),
      title: `${home} vs ${away}`,
      home,
      away,
      competition: competitionOf(ev),
      p,
      rawSum,
      priceAt: at,
      // Exactly one question paid, or we do not know yet.
      outcome: winners.length === 1 ? winners[0].side : null,
      score: live?.score ? `${live.score.home}-${live.score.away}` : null,
      period: str(ev.period) || null,
      volume: Number.isFinite(Number(ev.volume)) ? Number(ev.volume) : null,
      fav: null,
    }
  })
}

// ── the favourite's record at this price ─────────────────────────────────────

function favOf(r: { home: string; away: string; p: PmResult['p'] }): { side: 'home' | 'away'; team: string; p: number } | null {
  if (!r.p) return null
  const side = r.p.home >= r.p.away ? 'home' : 'away'
  const p = r.p[side]
  return p >= FAV_MIN ? { side, team: side === 'home' ? r.home : r.away, p } : null
}

function count(games: Array<{ res: 'W' | 'D' | 'L'; p: number }>): RecordCount | null {
  if (!games.length) return null
  return {
    n: games.length,
    w: games.filter((g) => g.res === 'W').length,
    d: games.filter((g) => g.res === 'D').length,
    l: games.filter((g) => g.res === 'L').length,
    exp: Math.round(games.reduce((s, g) => s + g.p, 0) * 10) / 10,
  }
}

/** On Polymarket: this name's earlier fixtures where it was priced in the band. */
async function pmRecord(team: string, lo: number, hi: number, before: string): Promise<RecordCount | null> {
  const sql = getSql()
  const rows = await sql<{ side: 'home' | 'away'; p: number; outcome: Side }[]>`
    select case when lower(home) = lower(${team}) then 'home' else 'away' end side,
           case when lower(home) = lower(${team}) then p_home else p_away end p,
           outcome
      from pm_results
     where (lower(home) = lower(${team}) or lower(away) = lower(${team}))
       and kickoff < ${before} and outcome is not null and p_home is not null
       and (case when lower(home) = lower(${team}) then p_home else p_away end) between ${lo} and ${hi}
     order by kickoff desc
     limit ${FAV_LOOKBACK}
  `
  return count(
    rows.map((r) => ({
      p: Number(r.p),
      res: r.outcome === 'draw' ? 'D' : r.outcome === r.side ? 'W' : 'L',
    }))
  )
}

/** In our database: the club's earlier league games at this closing price
 *  (Pinnacle, else the market average). Clubs only — internationals carry no
 *  odds in `matches`. */
async function dbRecord(
  home: string,
  away: string,
  side: 'home' | 'away',
  lo: number,
  hi: number,
  before: string
): Promise<(RecordCount & { from: string | null }) | null> {
  const res = await resolveTeams(home, away)
  const team = side === 'home' ? res.home : res.away
  if (!team) return null
  const id = Number(team.id)
  const sql = getSql()
  const rows = await sql<
    {
      kickoff_utc: Date
      home_team_id: number
      hs: number
      as_: number
      pho: number | null; pdo: number | null; pao: number | null
      aho: number | null; ado: number | null; aao: number | null
    }[]
  >`
    select m.kickoff_utc, m.home_team_id, m.home_score hs, m.away_score as_,
           pc.home_odds pho, pc.draw_odds pdo, pc.away_odds pao,
           av.home_odds aho, av.draw_odds ado, av.away_odds aao
      from matches m
      left join lateral (
        select mo.home_odds, mo.draw_odds, mo.away_odds from match_odds mo
         where mo.match_id = m.id and mo.snapshot_type = 'closing'
           and mo.bookmaker_id = (select id from bookmakers where name = 'Pinnacle (closing)')
         limit 1
      ) pc on true
      left join lateral (
        select mo.home_odds, mo.draw_odds, mo.away_odds from match_odds mo
         where mo.match_id = m.id and mo.snapshot_type = 'closing'
           and mo.bookmaker_id = (select id from bookmakers where name = 'Market average')
         limit 1
      ) av on true
     where (m.home_team_id = ${id} or m.away_team_id = ${id})
       and m.home_score is not null and m.kickoff_utc < ${before}
     order by m.kickoff_utc desc
     limit 1200
  `
  const games: Array<{ res: 'W' | 'D' | 'L'; p: number; at: Date }> = []
  const seen = new Set<number>()
  for (const r of rows) {
    // The same fixture loaded twice (teamform.dedupeGames) counts once.
    const t = new Date(r.kickoff_utc).getTime()
    if (seen.has(t)) continue
    seen.add(t)
    const pr = devig3(n(r.pho), n(r.pdo), n(r.pao)) ?? devig3(n(r.aho), n(r.ado), n(r.aao))
    if (!pr) continue
    const isHome = Number(r.home_team_id) === id
    const p = isHome ? pr[0] : pr[2]
    if (p < lo || p > hi) continue
    const gf = isHome ? r.hs : r.as_
    const ga = isHome ? r.as_ : r.hs
    games.push({ p, res: gf > ga ? 'W' : gf === ga ? 'D' : 'L', at: new Date(r.kickoff_utc) })
    if (games.length >= FAV_LOOKBACK) break
  }
  const c = count(games)
  return c ? { ...c, from: games[games.length - 1].at.toISOString() } : null
}

export async function favRecordFor(r: PmResult): Promise<FavRecord | null> {
  const f = favOf(r)
  if (!f) return null
  const lo = Math.max(0, f.p - FAV_BAND)
  const hi = Math.min(1, f.p + FAV_BAND)
  const [pm, db] = await Promise.all([
    pmRecord(f.team, lo, hi, r.kickoff).catch(() => null),
    dbRecord(r.home, r.away, f.side, lo, hi, r.kickoff).catch(() => null),
  ])
  return { side: f.side, team: f.team, p: f.p, band: [lo, hi], pm, db }
}

// ── storing ──────────────────────────────────────────────────────────────────

/** Slugs of a day already stored WITH prices: their history is not re-read. */
export async function pricedSlugs(day: string): Promise<Set<string>> {
  const sql = getSql()
  const rows = await sql<{ slug: string }[]>`
    select slug from pm_results
     where kickoff >= ${`${day}T00:00:00Z`} and kickoff < ${`${addDays(day, 1)}T00:00:00Z`}
       and p_home is not null
  `
  return new Set(rows.map((r) => r.slug))
}

/** Upsert. Prices and the favourite's record are written once and never
 *  overwritten by a later run that skipped them; the outcome, score and
 *  volume follow the latest read. */
export async function storeRows(rows: PmResult[]): Promise<number> {
  if (!rows.length) return 0
  const sql = getSql()
  let n = 0
  for (const r of rows) {
    await sql`
      insert into pm_results
        (slug, kickoff, title, home, away, competition, p_home, p_draw, p_away, raw_sum, price_at,
         outcome, score, period, volume_usd, fav_record, resolved_at)
      values
        (${r.slug}, ${r.kickoff}, ${r.title}, ${r.home}, ${r.away}, ${r.competition},
         ${r.p?.home ?? null}, ${r.p?.draw ?? null}, ${r.p?.away ?? null}, ${r.rawSum}, ${r.priceAt},
         ${r.outcome}, ${r.score}, ${r.period}, ${r.volume},
         ${r.fav ? sql.json(r.fav as unknown as Parameters<typeof sql.json>[0]) : null},
         ${r.outcome ? new Date().toISOString() : null})
      on conflict (slug) do update set
        competition = coalesce(excluded.competition, pm_results.competition),
        p_home      = coalesce(pm_results.p_home, excluded.p_home),
        p_draw      = coalesce(pm_results.p_draw, excluded.p_draw),
        p_away      = coalesce(pm_results.p_away, excluded.p_away),
        raw_sum     = coalesce(pm_results.raw_sum, excluded.raw_sum),
        price_at    = coalesce(pm_results.price_at, excluded.price_at),
        outcome     = coalesce(excluded.outcome, pm_results.outcome),
        score       = coalesce(excluded.score, pm_results.score),
        period      = coalesce(excluded.period, pm_results.period),
        volume_usd  = coalesce(excluded.volume_usd, pm_results.volume_usd),
        fav_record  = coalesce(pm_results.fav_record, excluded.fav_record),
        resolved_at = coalesce(pm_results.resolved_at, excluded.resolved_at),
        updated_at  = now()
    `
    n++
  }
  return n
}

/** Rows priced but with no favourite record yet (a run that ran out of time). */
export async function rowsMissingFav(day: string): Promise<PmResult[]> {
  const rows = await readRange(`${day}T00:00:00Z`, `${addDays(day, 1)}T00:00:00Z`)
  return rows.filter((r) => r.p && !r.fav && favOf(r))
}

export async function storeFav(slug: string, fav: FavRecord): Promise<void> {
  const sql = getSql()
  await sql`
    update pm_results set fav_record = ${sql.json(fav as unknown as Parameters<typeof sql.json>[0])}, updated_at = now()
     where slug = ${slug} and fav_record is null
  `
}

// ── reading ──────────────────────────────────────────────────────────────────

interface DbRow {
  slug: string
  kickoff: Date
  title: string
  home: string
  away: string
  competition: string | null
  p_home: number | null
  p_draw: number | null
  p_away: number | null
  raw_sum: number | null
  price_at: Date | null
  outcome: Side | null
  score: string | null
  period: string | null
  volume_usd: number | null
  fav_record: FavRecord | null
}

function fromDb(r: DbRow): PmResult {
  return {
    slug: r.slug,
    kickoff: new Date(r.kickoff).toISOString(),
    title: r.title,
    home: r.home,
    away: r.away,
    competition: r.competition,
    p:
      r.p_home != null && r.p_draw != null && r.p_away != null
        ? { home: Number(r.p_home), draw: Number(r.p_draw), away: Number(r.p_away) }
        : null,
    rawSum: r.raw_sum == null ? null : Number(r.raw_sum),
    priceAt: r.price_at ? new Date(r.price_at).toISOString() : null,
    outcome: r.outcome,
    score: r.score,
    period: r.period,
    volume: r.volume_usd == null ? null : Number(r.volume_usd),
    fav: r.fav_record,
  }
}

export async function readRange(fromIso: string, toIso: string): Promise<PmResult[]> {
  const sql = getSql()
  const rows = await sql<DbRow[]>`
    select slug, kickoff, title, home, away, competition, p_home, p_draw, p_away, raw_sum, price_at,
           outcome, score, period, volume_usd, fav_record
      from pm_results
     where kickoff >= ${fromIso} and kickoff < ${toIso}
     order by kickoff
  `
  return rows.map(fromDb)
}

export interface CalibrationBand {
  lo: number
  hi: number
  n: number
  /** Share of favourites in the band that won. */
  won: number
  /** Their average price. */
  priced: number
}

/** Every stored favourite with a price and an outcome, by price band: does a
 *  60-70% favourite win 60-70% of the time? Only books that traded $1k+. */
export async function calibration(): Promise<{ bands: CalibrationBand[]; since: string | null; total: number }> {
  const sql = getSql()
  const rows = await sql<{ b: number; n: number; won: number; priced: number; since: Date | null }[]>`
    with f as (
      select kickoff,
             greatest(p_home, p_away) p,
             case when p_home >= p_away then outcome = 'home' else outcome = 'away' end won
        from pm_results
       where p_home is not null and outcome is not null and coalesce(volume_usd, 0) >= 1000
         and greatest(p_home, p_away) >= 0.5
    )
    select least(floor(p * 10), 9)::int b, count(*)::int n, avg(won::int)::float won, avg(p)::float priced,
           min(kickoff) since
      from f group by 1 order by 1
  `
  const total = rows.reduce((s, r) => s + r.n, 0)
  const since = rows.reduce<Date | null>((m, r) => (r.since && (!m || r.since < m) ? r.since : m), null)
  return {
    bands: rows.map((r) => ({ lo: r.b / 10, hi: (r.b + 1) / 10, n: r.n, won: r.won, priced: r.priced })),
    since: since ? new Date(since).toISOString() : null,
    total,
  }
}

// ── dates ────────────────────────────────────────────────────────────────────

export function addDays(day: string, k: number): string {
  const t = Date.parse(`${day}T00:00:00Z`) + k * 86_400_000
  return new Date(t).toISOString().slice(0, 10)
}

export const isDay = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s) && Number.isFinite(Date.parse(`${s}T00:00:00Z`))

