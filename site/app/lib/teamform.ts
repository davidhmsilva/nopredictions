// Team form from our own database — the half the Game Center that a stats site
// also has, done the way only we can: every past match carries its closing
// price, so a run of overs can be set against what the market expected.
//
// Server only: imports the Postgres client. Pages import the TYPES from here.
//
// ⚠️ What this is not. Recent form was tested as an information class against
// the closing price — 10 frozen form features over 14,365 held-out matches —
// and added nothing (Brier −0.00006). Streaks are shown because people want to
// see them, with the rarity worked out honestly, never as a reason to bet.

import { unstable_cache } from 'next/cache'
import { getSql } from './db'
import { teamScore } from './gamecenter'
import PM_ALIASES from './pm_team_aliases.json'
import TABLE from './priced_like.json'

const MIN_SIDE_SCORE = 0.6
const GAMES = 40

// ── identity ─────────────────────────────────────────────────────────────────

interface Candidate {
  id: number
  name: string
  names: string[]
  leagues: number[]
  league: string | null
  n: number
}

function norm(s: string): string {
  return s
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

const NOISE = new Set(['fc', 'cf', 'sc', 'afc', 'ac', 'cd', 'club', 'de', 'the', 'sv', 'fk', 'sk'])
const bare = (s: string) => norm(s).split(' ').filter((t) => t && !NOISE.has(t)).join(' ')

const ALIAS: Record<string, string> = Object.fromEntries(
  Object.entries(PM_ALIASES as Record<string, string>).map(([k, v]) => [norm(k), v])
)

const loadCandidates = unstable_cache(
  async (): Promise<Candidate[]> => {
    const sql = getSql()
    const rows = await sql<
      { id: number; canonical_name: string; aliases: string[]; leagues: number[]; league: string | null; n: number }[]
    >`
      with recent as (
        select home_team_id tid, season_id, kickoff_utc from matches
         where kickoff_utc > now() - interval '420 days'
        union all
        select away_team_id, season_id, kickoff_utc from matches
         where kickoff_utc > now() - interval '420 days'
      ), agg as (
        select r.tid,
               array_agg(distinct s.league_id) leagues,
               (array_agg(l.name order by r.kickoff_utc desc))[1] league,
               count(*)::int n
          from recent r
          join seasons s on s.id = r.season_id
          join leagues l on l.id = s.league_id
         where l.name <> 'NBA'
         group by r.tid
      )
      select t.id, t.canonical_name, agg.leagues, agg.league, agg.n,
             coalesce((select array_agg(distinct a.alias) from team_aliases a where a.team_id = t.id), '{}') aliases
        from agg join teams t on t.id = agg.tid
    `
    return rows.map((r) => ({
      id: r.id,
      name: r.canonical_name,
      names: [r.canonical_name, ...(r.aliases ?? [])],
      leagues: r.leagues ?? [],
      league: r.league,
      n: r.n,
    }))
  },
  ['gc-team-candidates-v1'],
  { revalidate: 12 * 3600 }
)

interface Ranked {
  c: Candidate
  score: number
  exact: boolean
  extra: number
}

function rankSide(pmName: string, pool: Candidate[]): Ranked[] | 'not-in-db' {
  // The alias file is maintained for the models and is the strongest evidence
  // there is — including its explicit "not ours" verdicts. "Inter Miami" scores
  // 1.0 against our "Inter" under token containment; the file is what says no.
  const aliased = ALIAS[norm(pmName)] ?? ALIAS[bare(pmName)]
  if (aliased === '__NOT_IN_MODEL__') return 'not-in-db'
  if (aliased) {
    const hit = pool.filter((c) => c.name === aliased)
    if (hit.length) return hit.map((c) => ({ c, score: 1, exact: true, extra: 0 }))
  }

  const pmBare = bare(pmName)
  const pmLen = pmBare.split(' ').length
  const out: Ranked[] = []
  for (const c of pool) {
    let score = 0
    let exact = false
    let extra = 99
    for (const n of c.names) {
      const s = teamScore(pmName, n)
      if (s < MIN_SIDE_SCORE) continue
      const nb = bare(n)
      if (s > score) score = s
      if (nb === pmBare) exact = true
      extra = Math.min(extra, Math.abs(nb.split(' ').length - pmLen))
    }
    if (score >= MIN_SIDE_SCORE) out.push({ c, score, exact, extra })
  }
  return out
    .sort((a, b) => b.score - a.score || Number(b.exact) - Number(a.exact) || a.extra - b.extra || b.c.n - a.c.n)
    .slice(0, 6)
}

export interface ResolvedTeam {
  id: number
  name: string
  league: string | null
}

export interface Resolution {
  home: ResolvedTeam | null
  away: ResolvedTeam | null
  sameLeague: boolean
  note: string | null
}

/** Our `teams` rows for a Polymarket pair, or null where the names cannot
 *  separate the candidates. A missed team costs a panel; a wrong one puts
 *  another club's form under this fixture, so ties fail closed. */
export async function resolveTeams(home: string, away: string): Promise<Resolution> {
  const pool = await loadCandidates()
  const h = rankSide(home, pool)
  const a = rankSide(away, pool)
  const hs = h === 'not-in-db' ? [] : h
  const as = a === 'not-in-db' ? [] : a

  // Pair them. Two clubs on one Polymarket board have almost always played in
  // the same competition inside the window — the strongest disambiguator there
  // is, and the one that separates a "Rangers" from a "Queens Park Rangers".
  let best: { h: Ranked; a: Ranked; v: number; shared: boolean } | null = null
  let tie = false
  for (const x of hs) {
    for (const y of as) {
      if (x.c.id === y.c.id) continue
      const shared = x.c.leagues.some((l) => y.c.leagues.includes(l))
      const v =
        x.score + y.score + (x.exact ? 0.2 : 0) + (y.exact ? 0.2 : 0) + (shared ? 1 : 0) -
        0.02 * (x.extra + y.extra)
      if (!best || v > best.v + 1e-9) {
        best = { h: x, a: y, v, shared }
        tie = false
      } else if (Math.abs(v - best.v) <= 1e-9) {
        tie = true
      }
    }
  }

  const pick = (r: Ranked): ResolvedTeam => ({ id: r.c.id, name: r.c.name, league: r.c.league })

  if (best && !tie) {
    return {
      home: pick(best.h),
      away: pick(best.a),
      sameLeague: best.shared,
      note: best.shared ? null : 'The two clubs have not shared a competition in our data this season.',
    }
  }

  // One side alone may still be unambiguous.
  const single = (r: Ranked[]) =>
    r.length === 1 || (r.length > 1 && r[0].score > r[1].score + 1e-9) ? pick(r[0]) : null
  return {
    home: single(hs),
    away: single(as),
    sameLeague: false,
    note:
      h === 'not-in-db' || a === 'not-in-db'
        ? 'At least one of these clubs is outside the leagues our database covers.'
        : tie
          ? 'The team names match more than one club in our database, so neither is shown.'
          : null,
  }
}

// ── games ────────────────────────────────────────────────────────────────────

export interface TeamGame {
  date: string
  venue: 'H' | 'A'
  opponent: string
  league: string
  gf: number
  ga: number
  hf: number | null
  ha: number | null
  result: 'W' | 'D' | 'L'
  /** De-vigged closing probability of THIS team winning — Pinnacle, else the market average. */
  pWin: number | null
  /** De-vigged closing P(over 2.5). */
  pOver: number | null
  /** Closing decimal odds on this team. */
  odds: number | null
  oddsSource: 'pinnacle' | 'average' | null
}

interface Row {
  kickoff_utc: Date
  home_team_id: number
  hn: string
  an: string
  hs: number
  as_: number
  hht: number | null
  aht: number | null
  league: string
  pho: number | null; pdo: number | null; pao: number | null; poo: number | null; puo: number | null
  aho: number | null; ado: number | null; aao: number | null; aoo: number | null; auo: number | null
}

const n = (v: unknown): number | null => {
  const x = v == null ? NaN : Number(v)
  return Number.isFinite(x) && x > 1 ? x : null
}

function devig3(h: number | null, d: number | null, a: number | null): [number, number, number] | null {
  if (!h || !d || !a) return null
  const s = 1 / h + 1 / d + 1 / a
  return [1 / h / s, 1 / d / s, 1 / a / s]
}

function devig2(o: number | null, u: number | null): number | null {
  if (!o || !u) return null
  return 1 / o / (1 / o + 1 / u)
}

function toGame(r: Row, teamId: number): TeamGame {
  const home = r.home_team_id === teamId
  const pin = devig3(n(r.pho), n(r.pdo), n(r.pao))
  const avg = devig3(n(r.aho), n(r.ado), n(r.aao))
  const p = pin ?? avg
  const gf = home ? r.hs : r.as_
  const ga = home ? r.as_ : r.hs
  const hasHt = r.hht != null && r.aht != null
  return {
    date: new Date(r.kickoff_utc).toISOString(),
    venue: home ? 'H' : 'A',
    opponent: home ? r.an : r.hn,
    league: r.league,
    gf,
    ga,
    hf: hasHt ? (home ? r.hht : r.aht) : null,
    ha: hasHt ? (home ? r.aht : r.hht) : null,
    result: gf > ga ? 'W' : gf === ga ? 'D' : 'L',
    pWin: p ? (home ? p[0] : p[2]) : null,
    pOver: devig2(n(r.poo), n(r.puo)) ?? devig2(n(r.aoo), n(r.auo)),
    odds: home ? (n(r.pho) ?? n(r.aho)) : (n(r.pao) ?? n(r.aao)),
    oddsSource: pin ? 'pinnacle' : avg ? 'average' : null,
  }
}

const SELECT_GAMES = (sql: ReturnType<typeof getSql>) => sql`
  m.kickoff_utc, m.home_team_id, th.canonical_name hn, ta.canonical_name an,
  m.home_score hs, m.away_score as_, m.home_score_ht hht, m.away_score_ht aht, l.name league,
  pc.home_odds pho, pc.draw_odds pdo, pc.away_odds pao, pc.over_2_5_odds poo, pc.under_2_5_odds puo,
  av.home_odds aho, av.draw_odds ado, av.away_odds aao, av.over_2_5_odds aoo, av.under_2_5_odds auo
`

const JOIN_ODDS = (sql: ReturnType<typeof getSql>) => sql`
  join seasons s on s.id = m.season_id
  join leagues l on l.id = s.league_id
  join teams th on th.id = m.home_team_id
  join teams ta on ta.id = m.away_team_id
  left join lateral (
    select mo.home_odds, mo.draw_odds, mo.away_odds, mo.over_2_5_odds, mo.under_2_5_odds
      from match_odds mo
     where mo.match_id = m.id and mo.snapshot_type = 'closing'
       and mo.bookmaker_id = (select id from bookmakers where name = 'Pinnacle (closing)')
     limit 1
  ) pc on true
  left join lateral (
    select mo.home_odds, mo.draw_odds, mo.away_odds, mo.over_2_5_odds, mo.under_2_5_odds
      from match_odds mo
     where mo.match_id = m.id and mo.snapshot_type = 'closing'
       and mo.bookmaker_id = (select id from bookmakers where name = 'Market average')
     limit 1
  ) av on true
`

async function gamesOf(teamId: number): Promise<TeamGame[]> {
  const sql = getSql()
  const rows = await sql<Row[]>`
    select ${SELECT_GAMES(sql)}
      from matches m ${JOIN_ODDS(sql)}
     where (m.home_team_id = ${teamId} or m.away_team_id = ${teamId})
       and m.home_score is not null and m.away_score is not null
       and m.kickoff_utc < now()
     order by m.kickoff_utc desc
     limit ${GAMES}
  `
  return rows.map((r) => toGame(r, teamId))
}

export interface H2HGame {
  date: string
  home: string
  away: string
  hs: number
  as: number
  hht: number | null
  aht: number | null
  league: string
}

async function h2hOf(a: number, b: number): Promise<H2HGame[]> {
  const sql = getSql()
  const rows = await sql<Row[]>`
    select ${SELECT_GAMES(sql)}
      from matches m ${JOIN_ODDS(sql)}
     where ((m.home_team_id = ${a} and m.away_team_id = ${b})
         or (m.home_team_id = ${b} and m.away_team_id = ${a}))
       and m.home_score is not null
     order by m.kickoff_utc desc
     limit 10
  `
  return rows.map((r) => ({
    date: new Date(r.kickoff_utc).toISOString(),
    home: r.hn,
    away: r.an,
    hs: r.hs,
    as: r.as_,
    hht: r.hht,
    aht: r.aht,
    league: r.league,
  }))
}

// ── stats ────────────────────────────────────────────────────────────────────

/** k of n — kept as counts so the page can say "7 of 10", not just "70%". */
export interface Count {
  k: number
  n: number
}

export interface FormStats {
  games: number
  w: number
  d: number
  l: number
  gf: number
  ga: number
  o15: Count
  o25: Count
  o35: Count
  btts: Count
  cleanSheet: Count
  failedToScore: Count
  htGoal: Count
  htO15: Count
  scored1h: Count
  conceded1h: Count
  ledAtHt: Count
  shGoal: Count
  avgHtGoals: number | null
  /** Share of this team's matches' goals that came before half time. */
  share1h: number | null
}

const cnt = (xs: boolean[]): Count => ({ k: xs.filter(Boolean).length, n: xs.length })

function stats(g: TeamGame[]): FormStats {
  const ht = g.filter((x) => x.hf != null && x.ha != null)
  const tot = g.map((x) => x.gf + x.ga)
  const htTot = ht.map((x) => (x.hf as number) + (x.ha as number))
  const htSum = htTot.reduce((s, v) => s + v, 0)
  const ftSumHt = ht.reduce((s, x) => s + x.gf + x.ga, 0)
  return {
    games: g.length,
    w: g.filter((x) => x.result === 'W').length,
    d: g.filter((x) => x.result === 'D').length,
    l: g.filter((x) => x.result === 'L').length,
    gf: g.length ? g.reduce((s, x) => s + x.gf, 0) / g.length : 0,
    ga: g.length ? g.reduce((s, x) => s + x.ga, 0) / g.length : 0,
    o15: cnt(tot.map((t) => t > 1)),
    o25: cnt(tot.map((t) => t > 2)),
    o35: cnt(tot.map((t) => t > 3)),
    btts: cnt(g.map((x) => x.gf > 0 && x.ga > 0)),
    cleanSheet: cnt(g.map((x) => x.ga === 0)),
    failedToScore: cnt(g.map((x) => x.gf === 0)),
    htGoal: cnt(htTot.map((t) => t > 0)),
    htO15: cnt(htTot.map((t) => t > 1)),
    scored1h: cnt(ht.map((x) => (x.hf as number) > 0)),
    conceded1h: cnt(ht.map((x) => (x.ha as number) > 0)),
    ledAtHt: cnt(ht.map((x) => (x.hf as number) > (x.ha as number))),
    shGoal: cnt(ht.map((x) => x.gf + x.ga - (x.hf as number) - (x.ha as number) > 0)),
    avgHtGoals: ht.length ? htSum / ht.length : null,
    share1h: ftSumHt > 0 ? htSum / ftSumHt : null,
  }
}

// ── streaks, with their rarity worked out ────────────────────────────────────

type LeagueKey = keyof (typeof TABLE.leagues)[keyof typeof TABLE.leagues]

const PREDICATES: Array<{ key: LeagueKey; label: string; ht?: boolean; test: (g: TeamGame) => boolean }> = [
  { key: 'win', label: 'Won', test: (g) => g.result === 'W' },
  { key: 'unbeaten', label: 'Unbeaten', test: (g) => g.result !== 'L' },
  { key: 'loss', label: 'Lost', test: (g) => g.result === 'L' },
  { key: 'winless', label: 'Without a win', test: (g) => g.result !== 'W' },
  { key: 'draw', label: 'Drew', test: (g) => g.result === 'D' },
  { key: 'btts', label: 'Both teams scored', test: (g) => g.gf > 0 && g.ga > 0 },
  { key: 'no_btts', label: 'Not both teams scored', test: (g) => !(g.gf > 0 && g.ga > 0) },
  { key: 'o25', label: 'Over 2.5 goals', test: (g) => g.gf + g.ga > 2 },
  { key: 'u25', label: 'Under 2.5 goals', test: (g) => g.gf + g.ga < 3 },
  { key: 'scored', label: 'Scored', test: (g) => g.gf > 0 },
  { key: 'failed_to_score', label: 'Failed to score', test: (g) => g.gf === 0 },
  { key: 'clean_sheet', label: 'Kept a clean sheet', test: (g) => g.ga === 0 },
  { key: 'conceded', label: 'Conceded', test: (g) => g.ga > 0 },
  { key: 'ht_goal', label: 'Goal before half time', ht: true, test: (g) => (g.hf as number) + (g.ha as number) > 0 },
  { key: 'ht_no_goal', label: 'Goalless at half time', ht: true, test: (g) => (g.hf as number) + (g.ha as number) === 0 },
  { key: 'scored_1h', label: 'Scored in the 1st half', ht: true, test: (g) => (g.hf as number) > 0 },
  { key: 'conceded_1h', label: 'Conceded in the 1st half', ht: true, test: (g) => (g.ha as number) > 0 },
]

// A rarer run that implies a commoner one of the same length says it twice.
const IMPLIES: Partial<Record<LeagueKey, LeagueKey[]>> = {
  win: ['unbeaten'],
  loss: ['winless'],
  clean_sheet: ['no_btts'],
  failed_to_score: ['no_btts'],
  btts: ['scored', 'conceded'],
  scored_1h: ['ht_goal'],
  conceded_1h: ['ht_goal'],
  ht_no_goal: [],
}

export interface Streak {
  key: string
  label: string
  kind: 'run' | 'pattern'
  scope: 'all' | 'venue'
  /** For a run: its length. For a pattern: k of the last `of`. */
  k: number
  of: number
  /** The league rate the rarity is computed from. */
  base: number
  /** Chance of a run this long, or a count this extreme, at the league rate. */
  chance: number
}

function binomUpper(n: number, k: number, p: number): number {
  let s = 0
  for (let i = k; i <= n; i++) s += choose(n, i) * p ** i * (1 - p) ** (n - i)
  return s
}
function choose(n: number, k: number): number {
  let r = 1
  for (let i = 1; i <= k; i++) r = (r * (n - k + i)) / i
  return r
}

const MAX_RUN_CHANCE = 0.05
const MAX_PATTERN_CHANCE = 0.02

function leagueRates(league: string | null): Record<string, number | null> {
  const all = TABLE.leagues as Record<string, Record<string, number | null>>
  if (league && all[league]) return all[league]
  // No base for this competition: the average across every league we hold.
  const keys = Object.keys(Object.values(all)[0] ?? {})
  const out: Record<string, number | null> = {}
  for (const k of keys) {
    const v = Object.values(all).map((r) => r[k]).filter((x): x is number => typeof x === 'number')
    out[k] = v.length ? v.reduce((s, x) => s + x, 0) / v.length : null
  }
  return out
}

function streaksOf(games: TeamGame[], venueGames: TeamGame[], league: string | null): Streak[] {
  const rates = leagueRates(league)
  const found: Streak[] = []

  for (const [scope, list] of [['all', games], ['venue', venueGames]] as const) {
    for (const p of PREDICATES) {
      const base = rates[p.key]
      if (typeof base !== 'number' || base <= 0 || base >= 1) continue

      // The current run: from the latest match backwards until it breaks. A
      // match with no half-time score ends a half-time run rather than
      // extending it — unknown is not "yes".
      let run = 0
      for (const g of list) {
        if (p.ht && (g.hf == null || g.ha == null)) break
        if (!p.test(g)) break
        run++
      }
      const chance = base ** run
      if (run >= 3 && chance <= MAX_RUN_CHANCE) {
        found.push({ key: p.key, label: p.label, kind: 'run', scope, k: run, of: run, base, chance })
        continue
      }

      // "8 of the last 10" — the pattern a run hides when one match broke it.
      const last = list.slice(0, 10).filter((g) => !p.ht || (g.hf != null && g.ha != null))
      if (last.length >= 8) {
        const k = last.filter(p.test).length
        const tailP = binomUpper(last.length, k, base)
        if (k >= Math.ceil(last.length * 0.7) && tailP <= MAX_PATTERN_CHANCE) {
          found.push({ key: p.key, label: p.label, kind: 'pattern', scope, k, of: last.length, base, chance: tailP })
        }
      }
    }
  }

  found.sort((a, b) => a.chance - b.chance)
  const kept: Streak[] = []
  for (const s of found) {
    const redundant = kept.some(
      (x) =>
        x.scope === s.scope &&
        x.k === s.k &&
        x.kind === s.kind &&
        (IMPLIES[x.key as LeagueKey] ?? []).includes(s.key as LeagueKey)
    )
    // The venue version of a run that already shows at every venue adds nothing.
    const echo = s.scope === 'venue' && kept.some((x) => x.key === s.key && x.scope === 'all' && x.k >= s.k)
    if (!redundant && !echo) kept.push(s)
  }
  return kept.slice(0, 6)
}

// ── the market's own expectation of the same games ───────────────────────────

export interface MarketRecord {
  games: number
  actual: number
  expected: number
}

function marketRecord(games: TeamGame[]) {
  const last = games.slice(0, 10)
  const w = last.filter((g) => g.pWin != null)
  const o = last.filter((g) => g.pOver != null)
  return {
    wins: w.length >= 5
      ? { games: w.length, actual: w.filter((g) => g.result === 'W').length, expected: w.reduce((s, g) => s + (g.pWin as number), 0) }
      : null,
    overs: o.length >= 5
      ? { games: o.length, actual: o.filter((g) => g.gf + g.ga > 2).length, expected: o.reduce((s, g) => s + (g.pOver as number), 0) }
      : null,
  }
}

// ── assembled ────────────────────────────────────────────────────────────────

export interface TeamForm {
  id: number
  name: string
  league: string | null
  venue: 'H' | 'A'
  games: TeamGame[]
  /** Both venue splits, because which one applies is only known once ESPN has
   *  said who is really at home — Polymarket's title order is not evidence. */
  splits: {
    last5: FormStats
    last10: FormStats
    home10: FormStats
    away10: FormStats
    season: FormStats
  }
  streaks: Streak[]
  market: { wins: MarketRecord | null; overs: MarketRecord | null }
  lastPlayed: string | null
}

function seasonStart(): string {
  const now = new Date()
  const y = now.getUTCMonth() >= 6 ? now.getUTCFullYear() : now.getUTCFullYear() - 1
  return new Date(Date.UTC(y, 6, 1)).toISOString()
}

async function buildTeam(team: ResolvedTeam, venue: 'H' | 'A'): Promise<TeamForm> {
  const games = await gamesOf(team.id)
  const venueGames = games.filter((g) => g.venue === venue)
  const since = seasonStart()
  return {
    id: team.id,
    name: team.name,
    league: games[0]?.league ?? team.league,
    venue,
    games: games.slice(0, 20),
    splits: {
      last5: stats(games.slice(0, 5)),
      last10: stats(games.slice(0, 10)),
      home10: stats(games.filter((g) => g.venue === 'H').slice(0, 10)),
      away10: stats(games.filter((g) => g.venue === 'A').slice(0, 10)),
      season: stats(games.filter((g) => g.date >= since)),
    },
    streaks: streaksOf(games, venueGames, games[0]?.league ?? team.league),
    market: marketRecord(games),
    lastPlayed: games[0]?.date ?? null,
  }
}

export interface TeamContext {
  resolution: Resolution
  home: TeamForm | null
  away: TeamForm | null
  h2h: H2HGame[]
  /** How many patterns were checked — the multiple-comparisons denominator. */
  checked: number
}

export const teamContext = unstable_cache(
  async (home: string, away: string): Promise<TeamContext> => {
    const resolution = await resolveTeams(home, away)
    const [h, a, h2h] = await Promise.all([
      resolution.home ? buildTeam(resolution.home, 'H') : Promise.resolve(null),
      resolution.away ? buildTeam(resolution.away, 'A') : Promise.resolve(null),
      resolution.home && resolution.away
        ? h2hOf(resolution.home.id, resolution.away.id)
        : Promise.resolve([] as H2HGame[]),
    ])
    return { resolution, home: h, away: a, h2h, checked: PREDICATES.length * 2 * 2 }
  },
  ['gc-team-context-v1'],
  { revalidate: 3 * 3600 }
)
