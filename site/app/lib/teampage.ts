// One club's page: form, splits, runs, its league table, and the next games
// it has a market on — the Sofascore team page, done the way only we can,
// because every match we hold carries the price it closed at.
//
// Server only (Postgres). The page imports the TYPES from here.
//
// ⚠️ The table is computed from the results in `matches`, which is Stage A:
//    as fresh as the last Football-Data CSV, and with no points deductions,
//    no play-off split and no conference split (MLS is one table here). The
//    page prints the date of the latest result it holds for that reason.

import { unstable_cache } from 'next/cache'
import { getSql } from './db'
import {
  devig3,
  gamesOf,
  marketRecord,
  n,
  PREDICATE_COUNT,
  resolveTeams,
  seasonStart,
  stats,
  streaksOf,
  type FormStats,
  type MarketRecord,
  type Streak,
  type TeamGame,
} from './teamform'
import { displayKeys, displayName } from './teamDisplay'
import type { ScoutFixture } from './scoutTypes'
import PM_ALIASES from './pm_team_aliases.json'

/** Polymarket's spellings of each of our clubs, from the models' alias file
 *  (Polymarket name → canonical). Inverted once: "Sp Lisbon" → "sporting cp",
 *  "sporting lisbon", … — the names the boards actually print. */
const PM_BY_CANON = (() => {
  const m = new Map<string, string[]>()
  for (const [pm, canon] of Object.entries(PM_ALIASES as Record<string, string>)) {
    if (canon === '__NOT_IN_MODEL__') continue
    const list = m.get(canon) ?? []
    list.push(pm)
    m.set(canon, list)
  }
  return m
})()

/** Every spelling of a club we know: ours, the database's aliases, ESPN's and
 *  Polymarket's. For matching and search, never for display. */
export function spellingsOf(id: number, canonical: string, aliases: string[] = []): string[] {
  return Array.from(
    new Set([canonical, ...displayKeys(id), ...aliases, ...(PM_BY_CANON.get(canonical) ?? [])])
  )
}

export interface TableRow {
  id: number
  team: string
  p: number
  w: number
  d: number
  l: number
  gf: number
  ga: number
  pts: number
  /** Points the closing prices of the same games expected: Σ 3·P(win) + P(draw). */
  xpts: number | null
  /** Games xpts is summed over — a game with no closing price adds nothing to either side. */
  priced: number
  /** Actual points on those same priced games, so the two numbers compare like with like. */
  ptsPriced: number
}

export interface LeagueTable {
  league: string
  country: string | null
  season: string
  rows: TableRow[]
  /** Latest result in the season we hold. */
  asOf: string | null
}

export interface TeamPageData {
  id: number
  name: string
  country: string | null
  league: string | null
  games: TeamGame[]
  splits: {
    last5: FormStats
    last10: FormStats
    home10: FormStats
    away10: FormStats
    season: FormStats
  }
  streaks: Streak[]
  market: { wins: MarketRecord | null; overs: MarketRecord | null }
  /** Over 2.5 across this season's priced games — the larger sample. */
  seasonOvers: MarketRecord | null
  table: LeagueTable | null
  lastPlayed: string | null
  /** Every spelling we hold, for matching the boards. */
  names: string[]
  /** How many patterns the runs were checked over — the multiple-comparisons denominator. */
  checked: number
}

export interface TeamRow {
  id: number
  canonical_name: string
  country: string | null
  aliases: string[]
}

export interface SeasonMatch {
  home_team_id: number
  away_team_id: number
  hn: string
  an: string
  hs: number
  as_: number
  kickoff_utc: Date
  pho: number | null; pdo: number | null; pao: number | null
  aho: number | null; ado: number | null; aao: number | null
}

async function currentSeason(teamId: number) {
  const sql = getSql()
  // The domestic league of the team's latest match. Cups and international
  // competitions have no table worth drawing from results alone.
  const rows = await sql<{ season_id: number; label: string; league: string; country: string | null }[]>`
    select s.id season_id, s.label, l.name league, l.country
      from matches m
      join seasons s on s.id = m.season_id
      join leagues l on l.id = s.league_id
     where (m.home_team_id = ${teamId} or m.away_team_id = ${teamId})
       and not l.is_cup and coalesce(l.country, '') <> 'INTL' and l.name <> 'NBA'
       and m.home_score is not null
     order by m.kickoff_utc desc
     limit 1
  `
  return rows[0] ?? null
}

async function tableOf(seasonId: number, league: string, country: string | null, label: string): Promise<LeagueTable> {
  const sql = getSql()
  const ms = await sql<SeasonMatch[]>`
    select m.home_team_id, m.away_team_id, th.canonical_name hn, ta.canonical_name an,
           m.home_score hs, m.away_score as_, m.kickoff_utc,
           pc.home_odds pho, pc.draw_odds pdo, pc.away_odds pao,
           av.home_odds aho, av.draw_odds ado, av.away_odds aao
      from matches m
      join teams th on th.id = m.home_team_id
      join teams ta on ta.id = m.away_team_id
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
     where m.season_id = ${seasonId}
       and m.home_score is not null and m.away_score is not null
  `
  return buildTable(ms, league, country, label)
}

/** The table from a season's results. Pure, so it can be checked on real rows
 *  without a database. */
export function buildTable(ms: SeasonMatch[], league: string, country: string | null, label: string): LeagueTable {
  const by = new Map<number, TableRow & { xs: number }>()
  const row = (id: number, team: string) => {
    let r = by.get(id)
    if (!r) {
      r = { id, team, p: 0, w: 0, d: 0, l: 0, gf: 0, ga: 0, pts: 0, xpts: null, priced: 0, ptsPriced: 0, xs: 0 }
      by.set(id, r)
    }
    return r
  }
  // The same fixture loaded twice under two spellings of one club shares the
  // kick-off and the other club. Keep the row whose ids are the long-standing
  // ones (lowest); the duplicate would otherwise add a phantom club and a
  // second copy of the game to the real one.
  const seen = new Set<string>()
  const unique = [...ms]
    .sort((x, y) => Math.max(Number(x.home_team_id), Number(x.away_team_id)) - Math.max(Number(y.home_team_id), Number(y.away_team_id)))
    .filter((m) => {
      const t = new Date(m.kickoff_utc).getTime()
      const k = [`${t}|${Number(m.home_team_id)}`, `${t}|${Number(m.away_team_id)}`]
      if (k.some((x) => seen.has(x))) return false
      k.forEach((x) => seen.add(x))
      return true
    })
  let asOf: number | null = null
  for (const m of unique) {
    const t = new Date(m.kickoff_utc).getTime()
    if (asOf == null || t > asOf) asOf = t
    const p = devig3(n(m.pho), n(m.pdo), n(m.pao)) ?? devig3(n(m.aho), n(m.ado), n(m.aao))
    const sides: Array<[number, string, number, number, number | null]> = [
      [Number(m.home_team_id), m.hn, m.hs, m.as_, p ? 3 * p[0] + p[1] : null],
      [Number(m.away_team_id), m.an, m.as_, m.hs, p ? 3 * p[2] + p[1] : null],
    ]
    for (const [id, name, f, a, x] of sides) {
      const r = row(id, displayName(id, name))
      const pts = f > a ? 3 : f === a ? 1 : 0
      r.p++
      r.gf += f
      r.ga += a
      r.pts += pts
      if (f > a) r.w++
      else if (f === a) r.d++
      else r.l++
      if (x != null) {
        r.xs += x
        r.priced++
        r.ptsPriced += pts
      }
    }
  }
  const rows: TableRow[] = Array.from(by.values())
    .map(({ xs, ...r }) => ({ ...r, xpts: r.priced ? xs : null }))
    .sort(
      (a, b) =>
        b.pts - a.pts || b.gf - b.ga - (a.gf - a.ga) || b.gf - a.gf || a.team.localeCompare(b.team)
    )
  return {
    league,
    country,
    season: label,
    rows,
    asOf: asOf == null ? null : new Date(asOf).toISOString(),
  }
}

function seasonOvers(games: TeamGame[]): MarketRecord | null {
  const since = seasonStart()
  const g = games.filter((x) => x.date >= since && x.pOver != null)
  if (g.length < 5) return null
  return {
    games: g.length,
    actual: g.filter((x) => x.gf + x.ga > 2).length,
    expected: g.reduce((s, x) => s + (x.pOver as number), 0),
  }
}

export const teamPage = unstable_cache(
  async (id: number): Promise<TeamPageData | null> => {
    const sql = getSql()
    const [t] = await sql<TeamRow[]>`
      select t.id, t.canonical_name, t.country,
             coalesce((select array_agg(distinct a.alias) from team_aliases a where a.team_id = t.id), '{}') aliases
        from teams t where t.id = ${id}
    `
    if (!t) return null
    const games = await gamesOf(id)
    if (!games.length) return null

    const season = await currentSeason(id)
    const table = season ? await tableOf(season.season_id, season.league, season.country, season.label) : null
    return assemble(t, games, table)
  },
  ['team-page-v2'],
  { revalidate: 3 * 3600 }
)

/** Everything on the page that is arithmetic on rows already read. */
export function assemble(t: TeamRow, games: TeamGame[], table: LeagueTable | null): TeamPageData {
  const since = seasonStart()
  const league = table?.league ?? games[0]?.league ?? null
  return {
    id: Number(t.id),
    name: displayName(t.id, t.canonical_name),
    country: t.country,
    league,
    games: games.slice(0, 30),
    splits: {
      last5: stats(games.slice(0, 5)),
      last10: stats(games.slice(0, 10)),
      home10: stats(games.filter((g) => g.venue === 'H').slice(0, 10)),
      away10: stats(games.filter((g) => g.venue === 'A').slice(0, 10)),
      season: stats(games.filter((g) => g.date >= since)),
    },
    // No venue scope: a team page has no "next venue" to scope a run to.
    streaks: streaksOf(games, [], league),
    market: marketRecord(games),
    seasonOvers: seasonOvers(games),
    table,
    lastPlayed: games[0]?.date ?? null,
    names: spellingsOf(Number(t.id), t.canonical_name, t.aliases ?? []),
    checked: PREDICATE_COUNT(),
  }
}

// ── the next games with a market ─────────────────────────────────────────────

// Words that carry no identity on their own: a board fixture sharing only
// "United" with this club is not worth the resolver's time.
const COMMON = new Set([
  'fc', 'cf', 'sc', 'afc', 'ac', 'cd', 'club', 'de', 'the', 'sv', 'fk', 'sk', 'united', 'city', 'real',
  'sporting', 'athletic', 'atletico', 'town', 'county', 'rovers', 'wanderers', 'deportivo', 'olympique',
  'racing', 'union', 'dynamo', 'dinamo', 'sport', 'clube', 'calcio', 'borussia', 'fsv', 'vfb', 'vfl', 'tsg',
  'rb', 'ud', 'sd', 'rc', 'as', 'ss', 'us', 'cs', 'if', 'bk', 'st', 'saint', 'san', 'santa', 'de', 'da', 'do',
])

function flat(s: string): string {
  return s
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function tokens(s: string): string[] {
  return flat(s)
    .split(' ')
    .filter((w) => w.length >= 3 && !COMMON.has(w))
}

export interface UpcomingGame {
  slug: string
  title: string
  home: string
  away: string
  competition: string | null
  kickoff: string | null
  live: boolean
  score: { home: number; away: number } | null
  minute: number | null
  oneX2: { home: number | null; draw: number | null; away: number | null }
  venues: string[]
}

/** Board fixtures this club plays in. The board's names are Polymarket's, so
 *  each candidate goes through the same resolver as the Game Center — and
 *  both sides together, which is what separates Rangers from QPR. A cheap
 *  token prefilter decides which fixtures are worth resolving at all. */
export async function upcomingFor(team: TeamPageData, fixtures: ScoutFixture[]): Promise<UpcomingGame[]> {
  // Two ways in: a whole name we already know ("Sporting CP" is one of our
  // spellings of "Sp Lisbon", though every word of it is generic or short),
  // or a distinctive word in common.
  const whole = new Set(team.names.map(flat))
  const mine = new Set(team.names.flatMap(tokens))
  const rank = (f: ScoutFixture) =>
    whole.has(flat(f.home)) || whole.has(flat(f.away))
      ? 2
      : [...tokens(f.home), ...tokens(f.away)].some((w) => mine.has(w))
        ? 1
        : 0
  // Whole-name hits first, so a word shared with a dozen other boards cannot
  // push the club's own fixture past the cut.
  const maybe = fixtures
    .filter((f) => !f.finished)
    .map((f) => ({ f, r: rank(f) }))
    .filter((x) => x.r > 0)
    .sort((a, b) => b.r - a.r)
    .slice(0, 12)
    .map((x) => x.f)

  const out: UpcomingGame[] = []
  for (const f of maybe) {
    const r = await resolveTeams(f.home, f.away)
    const hit = [r.home, r.away].some((x) => x && Number(x.id) === team.id)
    if (!hit) continue
    out.push({
      slug: f.slug,
      title: `${f.home} vs ${f.away}`,
      home: f.home,
      away: f.away,
      competition: f.competition,
      kickoff: f.kickoff,
      live: f.live,
      score: f.score,
      minute: f.minute,
      oneX2: f.oneX2,
      venues: f.venues.map((v) => v.venue),
    })
  }
  return out.sort((a, b) => (a.kickoff ?? '').localeCompare(b.kickoff ?? ''))
}

// ── every team with a page worth listing ─────────────────────────────────────

export interface TeamIndexEntry {
  id: number
  /** The display name (lib/teamDisplay). */
  name: string
  league: string
  country: string | null
  tier: number | null
  /** Every other spelling we hold, for the search box. */
  spellings: string[]
}

/** Clubs that played a domestic league match in the last 120 days — the
 *  sitemap, the /teams index and the search box. Older ones still have a
 *  page; they are just not advertised. */
export const listTeams = unstable_cache(
  async (): Promise<TeamIndexEntry[]> => {
    const sql = getSql()
    const rows = await sql<
      { id: number; name: string; league: string; country: string | null; tier: number | null; aliases: string[] }[]
    >`
      with recent as (
        select m.home_team_id tid, s.league_id, m.kickoff_utc from matches m join seasons s on s.id = m.season_id
         where m.kickoff_utc > now() - interval '120 days' and m.home_score is not null
        union all
        select m.away_team_id, s.league_id, m.kickoff_utc from matches m join seasons s on s.id = m.season_id
         where m.kickoff_utc > now() - interval '120 days' and m.home_score is not null
      ), latest as (
        select distinct on (r.tid) r.tid, r.league_id
          from recent r join leagues l on l.id = r.league_id
         where not l.is_cup and coalesce(l.country, '') <> 'INTL' and l.name <> 'NBA'
           -- A club seen once is usually a second spelling of one we already
           -- list (the duplicated La Liga fixtures of Aug 2026), not a club.
           and r.tid in (select tid from recent group by tid having count(*) >= 3)
         order by r.tid, r.kickoff_utc desc
      )
      select t.id, t.canonical_name name, l.name league, l.country, l.tier,
             coalesce((select array_agg(distinct a.alias) from team_aliases a where a.team_id = t.id), '{}') aliases
        from latest x join teams t on t.id = x.tid join leagues l on l.id = x.league_id
    `
    return rows
      .map((r) => {
        const id = Number(r.id)
        const name = displayName(id, r.name)
        return {
          id,
          name,
          league: r.league,
          country: r.country,
          tier: r.tier,
          spellings: spellingsOf(id, r.name, r.aliases ?? []).filter((s) => s !== name),
        }
      })
      .sort(
        (a, b) =>
          (a.country ?? '~').localeCompare(b.country ?? '~') ||
          (a.tier ?? 99) - (b.tier ?? 99) ||
          a.league.localeCompare(b.league) ||
          a.name.localeCompare(b.name)
      )
  },
  ['team-index-v2'],
  { revalidate: 12 * 3600 }
)
