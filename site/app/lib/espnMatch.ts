// One ESPN match summary — lineups, timeline, box score, commentary, the table.
//
// Free, no key, one request per fixture (after a per-date index that every
// Game Center shares). Same edge rules as espn.ts: DO NOT SET A USER-AGENT.
//
// ⚠️ Sides are mapped to POLYMARKET'S order (the page's left and right), with
// `swapped` saying when ESPN's real home team is Polymarket's second name.
// Reading ESPN's array order instead of `homeAway` is the bug that puts a score
// the wrong way round; reading PM's title order as home/away is the one that
// puts a team's form under the wrong venue.

import { unstable_cache } from 'next/cache'
import { BASE, LEAGUES } from './espn'
import { teamScore } from './gamecenter'

const MIN_SIDE_SCORE = 0.6

type J = Record<string, unknown>
const obj = (v: unknown): J => (v && typeof v === 'object' ? (v as J) : {})
const arr = (v: unknown): J[] => (Array.isArray(v) ? (v as J[]) : [])
const str = (v: unknown): string => (v == null ? '' : String(v))
const num = (v: unknown): number | null => {
  const x = typeof v === 'number' ? v : parseFloat(str(v))
  return Number.isFinite(x) ? x : null
}

// ── finding the event ────────────────────────────────────────────────────────

interface IndexEntry {
  id: string
  league: string
  home: string
  away: string
  date: string
}

async function scoreboard(code: string, ymd: string): Promise<IndexEntry[]> {
  try {
    const res = await fetch(`${BASE}/${code}/scoreboard?dates=${ymd}`, {
      signal: AbortSignal.timeout(8000),
      cache: 'no-store',
    })
    if (!res.ok) return []
    const body = obj(await res.json())
    const out: IndexEntry[] = []
    for (const ev of arr(body.events)) {
      const comp = arr(ev.competitions)[0]
      if (!comp) continue
      const sides = arr(comp.competitors)
      const h = sides.find((s) => s.homeAway === 'home')
      const a = sides.find((s) => s.homeAway === 'away')
      if (!h || !a) continue
      out.push({
        id: str(ev.id),
        league: code,
        home: str(obj(h.team).displayName),
        away: str(obj(a.team).displayName),
        date: str(ev.date),
      })
    }
    return out
  } catch {
    return []
  }
}

/** Every ESPN fixture on one date across the mapped leagues. Shared by every
 *  Game Center through the data cache, so the ~50-request sweep is paid once
 *  per date per quarter hour, not once per page view. */
const indexFor = unstable_cache(
  async (ymd: string): Promise<IndexEntry[]> => {
    const all = await Promise.allSettled(LEAGUES.map((c) => scoreboard(c, ymd)))
    return all.flatMap((r) => (r.status === 'fulfilled' ? r.value : []))
  },
  ['gc-espn-index-v1'],
  { revalidate: 900 }
)

function ymd(d: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' })
    .format(d)
  return parts.replace(/-/g, '')
}

async function findEvent(
  home: string,
  away: string,
  kickoff: string | null
): Promise<{ entry: IndexEntry; swapped: boolean } | null> {
  const when = kickoff ? new Date(kickoff) : new Date()
  // ESPN files a fixture under its US-Eastern date; a 01:00Z kick-off in South
  // America is the previous day there. Ask for both when they differ.
  const dates = Array.from(new Set([ymd(when, 'America/New_York'), ymd(when, 'UTC')]))
  const pool = (await Promise.all(dates.map((d) => indexFor(d)))).flat()

  const scored = pool
    .map((e) => {
      const straight = Math.min(teamScore(home, e.home), teamScore(away, e.away))
      const swapped = Math.min(teamScore(home, e.away), teamScore(away, e.home))
      return swapped > straight
        ? { entry: e, swapped: true, s: swapped }
        : { entry: e, swapped: false, s: straight }
    })
    .filter((x) => x.s >= MIN_SIDE_SCORE)
    .sort((a, b) => b.s - a.s)

  if (!scored.length) return null
  // Two fixtures scoring the same means the names cannot separate them.
  if (scored.length > 1 && Math.abs(scored[0].s - scored[1].s) < 1e-9 && scored[0].entry.id !== scored[1].entry.id) {
    return null
  }
  return { entry: scored[0].entry, swapped: scored[0].swapped }
}

// ── the summary ──────────────────────────────────────────────────────────────

export interface EspnPlayer {
  id: string
  name: string
  short: string
  jersey: string
  pos: string | null
  place: number
  starter: boolean
  subIn: string | null
  subOut: string | null
  goals: number
  yellow: number
  red: number
  shots: number
  shotsOn: number
  saves: number
  fouls: number
}

export interface EspnFormGame {
  result: string
  score: string
  venue: 'H' | 'A'
  opponent: string
  competition: string
  date: string
}

export interface EspnSide {
  id: string
  name: string
  abbr: string
  logo: string | null
  color: string | null
  score: number | null
  halves: number[]
  formation: string | null
  starters: EspnPlayer[]
  bench: EspnPlayer[]
  form: EspnFormGame[]
}

export type EspnEventKind = 'goal' | 'own-goal' | 'penalty' | 'pen-miss' | 'yellow' | 'red' | 'sub' | 'period'

export interface EspnEvent {
  minute: string
  /** Seconds, for ordering and for placing a marker on a 90-minute axis. */
  t: number
  kind: EspnEventKind
  side: 'home' | 'away' | null
  player: string | null
  other: string | null
  text: string
}

export interface EspnStat {
  key: string
  label: string
  home: string
  away: string
  h: number | null
  a: number | null
}

export interface EspnTableRow {
  rank: number
  team: string
  gp: number
  w: number
  d: number
  l: number
  gd: number
  pts: number
  mark: 'home' | 'away' | null
}

export interface EspnMatch {
  eventId: string
  league: string
  url: string
  state: 'pre' | 'in' | 'post'
  clock: string | null
  detail: string
  /** ESPN's real home team is Polymarket's SECOND name. */
  swapped: boolean
  venue: string | null
  city: string | null
  attendance: number | null
  referee: string | null
  broadcasts: string[]
  home: EspnSide
  away: EspnSide
  lineupsConfirmed: boolean
  events: EspnEvent[]
  stats: EspnStat[]
  commentary: Array<{ minute: string; text: string }>
  table: { name: string; rows: EspnTableRow[] } | null
}

const STAT_ORDER: Array<[string, string]> = [
  ['possessionPct', 'Possession'],
  ['totalShots', 'Shots'],
  ['shotsOnTarget', 'On target'],
  ['blockedShots', 'Blocked shots'],
  ['wonCorners', 'Corners'],
  ['offsides', 'Offsides'],
  ['foulsCommitted', 'Fouls'],
  ['yellowCards', 'Yellow cards'],
  ['redCards', 'Red cards'],
  ['saves', 'Saves'],
  ['totalPasses', 'Passes'],
  ['passPct', 'Pass accuracy'],
  ['totalCrosses', 'Crosses'],
  ['totalLongBalls', 'Long balls'],
  ['totalTackles', 'Tackles'],
  ['interceptions', 'Interceptions'],
  ['totalClearance', 'Clearances'],
]

function statOf(p: J, name: string): number {
  const s = arr(p.stats).find((x) => x.name === name)
  return num(s?.value) ?? 0
}

function playOf(p: J, flag: string): string | null {
  const play = arr(p.plays).find((x) => x[flag] === true)
  return play ? str(obj(play.clock).displayValue) || null : null
}

function player(p: J): EspnPlayer {
  const a = obj(p.athlete)
  return {
    id: str(a.id),
    name: str(a.displayName),
    short: str(a.shortName || a.lastName || a.displayName),
    jersey: str(p.jersey),
    pos: str(obj(p.position).abbreviation) || null,
    place: num(p.formationPlace) ?? 0,
    starter: p.starter === true,
    subIn: p.subbedIn ? playOf(p, 'substitution') : null,
    subOut: p.subbedOut ? playOf(p, 'substitution') : null,
    goals: statOf(p, 'totalGoals'),
    yellow: statOf(p, 'yellowCards'),
    red: statOf(p, 'redCards'),
    shots: statOf(p, 'totalShots'),
    shotsOn: statOf(p, 'shotsOnTarget'),
    saves: statOf(p, 'saves'),
    fouls: statOf(p, 'foulsCommitted'),
  }
}

function kindOf(type: string, text: string): EspnEventKind | null {
  const t = type.toLowerCase()
  if (t.includes('own-goal') || /own goal/i.test(text)) return 'own-goal'
  if (t.includes('penalty') && (t.includes('missed') || t.includes('saved'))) return 'pen-miss'
  if (t.includes('penalty') && t.includes('scored')) return 'penalty'
  if (t.includes('goal')) return 'goal'
  if (t.includes('red') || t.includes('yellow-red')) return 'red'
  if (t.includes('yellow')) return 'yellow'
  if (t.includes('substitution')) return 'sub'
  if (t === 'halftime' || t.includes('end-regular') || t === 'kickoff' || t.includes('full-time')) return 'period'
  return null
}

function seconds(clock: J): number {
  const v = num(clock.value)
  if (v != null) return v
  const parts = str(clock.displayValue).replace(/'/g, ' ').replace(/\+/g, ' ').split(/\s+/).filter((x) => /^\d+$/.test(x))
  return parts.reduce((s, x) => s + parseInt(x, 10), 0) * 60
}

function parse(body: J, entry: IndexEntry, swapped: boolean): EspnMatch | null {
  const header = obj(body.header)
  const comp = arr(header.competitions)[0]
  if (!comp) return null
  const competitors = arr(comp.competitors)
  const espnHome = competitors.find((c) => c.homeAway === 'home')
  const espnAway = competitors.find((c) => c.homeAway === 'away')
  if (!espnHome || !espnAway) return null

  // PM's first name is ESPN's home unless swapped.
  const pmFirst = swapped ? espnAway : espnHome
  const pmSecond = swapped ? espnHome : espnAway
  const idFirst = str(obj(pmFirst.team).id)
  const idSecond = str(obj(pmSecond.team).id)
  const sideOfId = (id: string): 'home' | 'away' | null =>
    id === idFirst ? 'home' : id === idSecond ? 'away' : null

  const rosters = arr(body.rosters)
  const lastFive = arr(body.lastFiveGames)

  const side = (c: J): EspnSide => {
    const team = obj(c.team)
    const id = str(team.id)
    const roster = rosters.find((r) => str(obj(r.team).id) === id)
    const players = arr(roster?.roster).map(player)
    const logos = arr(team.logos)
    // `score` is not from this team's side — Union's 2-4 home defeat to Ipswich
    // arrives as "4-2" beside an L. Rebuilt as goals for-against from the ids.
    const form = arr(lastFive.find((t) => str(obj(t.team).id) === id)?.events)
      .map((e) => {
        const hid = str(e.homeTeamId)
        const isHome = hid ? hid === id : str(e.atVs) !== '@'
        const hs = num(e.homeTeamScore)
        const as = num(e.awayTeamScore)
        return {
          result: str(e.gameResult),
          score: hs != null && as != null ? (isHome ? `${hs}-${as}` : `${as}-${hs}`) : str(e.score),
          venue: (isHome ? 'H' : 'A') as 'H' | 'A',
          opponent: str(obj(e.opponent).displayName),
          competition: str(e.competitionName),
          date: str(e.gameDate),
        }
      })
      // Latest first, like every other list on the page.
      .sort((a, b) => b.date.localeCompare(a.date))
    return {
      id,
      name: str(team.displayName),
      abbr: str(team.abbreviation),
      logo: logos.length ? str(logos[0].href) : null,
      color: team.color ? `#${str(team.color)}` : null,
      score: num(c.score),
      halves: arr(c.linescores).map((l) => num(l.displayValue) ?? 0),
      formation: roster?.formation ? str(roster.formation) : null,
      starters: players.filter((p) => p.starter).sort((a, b) => a.place - b.place),
      bench: players.filter((p) => !p.starter),
      form,
    }
  }

  const status = obj(comp.status)
  const stype = obj(status.type)
  const state = (['pre', 'in', 'post'].includes(str(stype.state)) ? str(stype.state) : 'pre') as EspnMatch['state']

  const events: EspnEvent[] = []
  for (const k of arr(body.keyEvents)) {
    const type = obj(k.type)
    const text = str(k.text)
    const kind = kindOf(str(type.type), text)
    if (!kind) continue
    const parts = arr(k.participants).map((p) => str(obj(p.athlete).displayName)).filter(Boolean)
    events.push({
      minute: str(obj(k.clock).displayValue),
      t: seconds(obj(k.clock)),
      kind,
      side: sideOfId(str(obj(k.team).id)),
      player: parts[0] ?? null,
      other: parts[1] ?? null,
      text: kind === 'period' ? str(type.text) : text,
    })
  }
  events.sort((a, b) => a.t - b.t)

  const box = arr(obj(body.boxscore).teams)
  const boxFor = (id: string) => arr(box.find((t) => str(obj(t.team).id) === id)?.statistics)
  const bh = boxFor(idFirst)
  const ba = boxFor(idSecond)
  const stats: EspnStat[] = []
  for (const [key, label] of STAT_ORDER) {
    const h = bh.find((s) => s.name === key)
    const a = ba.find((s) => s.name === key)
    if (!h && !a) continue
    const pct = key.endsWith('Pct')
    // ESPN is not consistent inside one payload: possession arrives as "65.7",
    // pass accuracy as "0.8". Anything at or below 1 is a fraction.
    const show = (s: J | undefined) => {
      if (!s) return '—'
      const v = str(s.displayValue)
      if (!pct) return v
      const x = num(v)
      return x == null ? v : `${Math.round(x <= 1 ? x * 100 : x)}%`
    }
    stats.push({ key, label, home: show(h), away: show(a), h: num(h?.displayValue), a: num(a?.displayValue) })
  }

  const commentary = arr(body.commentary)
    .map((c) => ({ minute: str(obj(c.time).displayValue), text: str(c.text) }))
    .filter((c) => c.text)
    .reverse()
    .slice(0, 60)

  let table: EspnMatch['table'] = null
  const standings = obj(body.standings)
  const groups = arr(standings.groups)
  // A league has one group. A group-stage competition has several — show the
  // one that holds these two teams, never the first one.
  const group = groups.find((g) =>
    arr(obj(g.standings).entries).some((e) => sideOfId(str(e.id)) != null)
  ) ?? groups[0]
  if (group) {
    const rows = arr(obj(group.standings).entries).map((e, i) => {
      const s = (name: string) => num(arr(e.stats).find((x) => x.name === name)?.displayValue) ?? 0
      return {
        rank: s('rank') || i + 1,
        team: str(e.team),
        gp: s('gamesPlayed'),
        w: s('wins'),
        d: s('ties'),
        l: s('losses'),
        gd: s('pointDifferential'),
        pts: s('points'),
        mark: sideOfId(str(e.id)),
      }
    })
    if (rows.length) table = { name: str(standings.header) || str(group.header) || 'Table', rows }
  }

  const info = obj(body.gameInfo)
  const venue = obj(info.venue)
  const referee = arr(info.officials).find((o) => /referee/i.test(str(obj(o.position).name)))
  const home = side(pmFirst)
  const away = side(pmSecond)

  return {
    eventId: entry.id,
    league: entry.league,
    url: `https://www.espn.com/soccer/match/_/gameId/${entry.id}`,
    state,
    clock: state === 'in' ? str(status.displayClock) || null : null,
    detail: str(stype.shortDetail || stype.detail),
    swapped,
    venue: str(venue.fullName) || null,
    city: str(obj(venue.address).city) || null,
    attendance: num(info.attendance) || null,
    referee: referee ? str(referee.displayName) : null,
    broadcasts: Array.from(
      new Set(arr(body.broadcasts).map((b) => str(obj(b.media).shortName)).filter(Boolean))
    ),
    home,
    away,
    lineupsConfirmed: home.starters.length === 11 && away.starters.length === 11,
    events,
    stats,
    commentary,
    table,
  }
}

export async function fetchEspnMatch(
  home: string,
  away: string,
  kickoff: string | null
): Promise<EspnMatch | null> {
  const hit = await findEvent(home, away, kickoff)
  if (!hit) return null
  try {
    const res = await fetch(`${BASE}/${hit.entry.league}/summary?event=${hit.entry.id}`, {
      signal: AbortSignal.timeout(10000),
      next: { revalidate: 20 },
    })
    if (!res.ok) return null
    return parse(obj(await res.json()), hit.entry, hit.swapped)
  } catch {
    return null
  }
}
