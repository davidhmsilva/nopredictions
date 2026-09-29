/** In-play Lab agents: the rule language, shared by the site and the runner.
 *
 *  A pre-match Lab spec (lib/backtest.ts) is a filter over finished games. An
 *  in-play spec is a TRIGGER on a live match plus an EXIT: enter the first
 *  poll where every condition holds, then either hold to the end, sell N
 *  minutes after a goal, or sell at a minute. agent/lab_inplay_runner.py
 *  evaluates exactly these fields, on the same api-football poll as the
 *  operator's pressure arms — ⚠️ change a field here and change it there.
 *
 *  Client-safe: no server imports.
 */
import { z } from 'zod'

export type InplayMarket = 'win' | 'draw' | 'next_goal'
export type InplayTeam = 'favourite' | 'underdog' | 'home' | 'away'
export type InplayScore = 'any' | 'level' | 'goalless' | 'team_ahead' | 'team_behind'
export type InplayPressure = 'none' | 'team' | 'either' | 'match'
export type InplayExit = 'hold' | 'after_goal' | 'at_minute'

export interface InplaySpec {
  kind: 'inplay'
  market: InplayMarket
  /** Whose win is bought, and whose score / pressure / kick-off price the
   *  conditions read. Required for 'win' and for every team-relative field. */
  team: InplayTeam | null
  /** That team's odds at kick-off: Polymarket's raw "Will <team> win?" price,
   *  the last read before the ball is kicked. */
  ko_odds_min: number | null
  ko_odds_max: number | null
  minute_min: number
  minute_max: number
  score: InplayScore
  pressure: InplayPressure
  pressure_level: 'pressing' | 'dominating'
  pressure_window: 'match' | 'last15'
  /** The price paid, in decimal odds. */
  odds_min: number | null
  odds_max: number | null
  exit: InplayExit
  exit_goal: 'any' | 'team' | 'opponent'
  exit_wait_min: number
  exit_minute: number | null
  leagues: string[] | null
}

/** The danger-index gates behind the two words. Per side: own index, and how
 *  far above the opponent's. 'match' reads the average of the two ends.
 *  pressing   = s18's gate, about the top quartile of readings;
 *  dominating = about the top decile (per-side p90 29, gap p90 28). */
export const PRESSURE_GATES = {
  pressing: { own: 19, gap: 20, match: 19 },
  dominating: { own: 29, gap: 28, match: 25 },
} as const

// ── what the model returns (no nullables: 0 / 'none' / [] mean "no limit") ──

export const ParsedInplaySchema = z.object({
  market: z.enum(['win', 'draw', 'next_goal']),
  team: z.enum(['favourite', 'underdog', 'home', 'away', 'none']),
  ko_odds_min: z.number(),
  ko_odds_max: z.number(),
  minute_min: z.number(),
  minute_max: z.number(),
  score: z.enum(['any', 'level', 'goalless', 'team_ahead', 'team_behind']),
  pressure: z.enum(['none', 'team', 'either', 'match']),
  pressure_level: z.enum(['pressing', 'dominating']),
  pressure_window: z.enum(['match', 'last15']),
  odds_min: z.number(),
  odds_max: z.number(),
  exit: z.enum(['hold', 'after_goal', 'at_minute']),
  exit_goal: z.enum(['any', 'team', 'opponent']),
  exit_wait_min: z.number(),
  exit_minute: z.number(),
  leagues: z.array(z.string()),
})
export type ParsedInplay = z.infer<typeof ParsedInplaySchema>

export const InplayParseSchema = z.object({
  supported: z.boolean(),
  reason: z.string(),
  suggestion: z.string(),
  caveats: z.array(z.string()),
  spec: ParsedInplaySchema.nullable(),
})

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const odds = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) && v > 1 ? Math.round(v * 100) / 100 : null)

/** Anything → a valid InplaySpec, or null with the reason it cannot run.
 *  Used on the model's output AND on what a browser posts to /api/agents:
 *  the runner never sees a field this did not check. */
export function cleanInplay(
  input: unknown,
  leagueCodes: Set<string>,
): { spec: InplaySpec | null; error: string | null } {
  if (!input || typeof input !== 'object') return { spec: null, error: 'Not an in-play rule.' }
  const s = input as Record<string, unknown>
  const pick = <T extends string>(v: unknown, allowed: readonly T[], dflt: T): T =>
    allowed.includes(v as T) ? (v as T) : dflt

  const market = pick(s.market, ['win', 'draw', 'next_goal'] as const, 'win')
  const teamRaw = s.team === 'none' ? null : s.team
  const team = (['favourite', 'underdog', 'home', 'away'] as const).includes(teamRaw as InplayTeam)
    ? (teamRaw as InplayTeam)
    : null
  const spec: InplaySpec = {
    kind: 'inplay',
    market,
    team,
    ko_odds_min: odds(s.ko_odds_min),
    ko_odds_max: odds(s.ko_odds_max),
    minute_min: clamp(Math.round(Number(s.minute_min) || 5), 1, 90),
    minute_max: clamp(Math.round(Number(s.minute_max) || 85), 1, 90),
    score: pick(s.score, ['any', 'level', 'goalless', 'team_ahead', 'team_behind'] as const, 'any'),
    pressure: pick(s.pressure, ['none', 'team', 'either', 'match'] as const, 'none'),
    pressure_level: pick(s.pressure_level, ['pressing', 'dominating'] as const, 'pressing'),
    pressure_window: pick(s.pressure_window, ['match', 'last15'] as const, 'match'),
    odds_min: odds(s.odds_min),
    odds_max: odds(s.odds_max),
    exit: pick(s.exit, ['hold', 'after_goal', 'at_minute'] as const, 'hold'),
    exit_goal: pick(s.exit_goal, ['any', 'team', 'opponent'] as const, 'any'),
    exit_wait_min: clamp(Math.round(Number(s.exit_wait_min) || 5), 1, 15),
    exit_minute: null,
    leagues: null,
  }
  if (spec.exit === 'at_minute') {
    const m = Math.round(Number(s.exit_minute))
    if (!Number.isFinite(m) || m < 2 || m > 90) return { spec: null, error: 'Sell at which minute? It needs one between 2 and 90.' }
    spec.exit_minute = m
  }
  if (Array.isArray(s.leagues)) {
    const ls = s.leagues.filter((c): c is string => typeof c === 'string' && leagueCodes.has(c))
    spec.leagues = ls.length ? ls : null
  }
  if (spec.minute_min > spec.minute_max) return { spec: null, error: 'The entry window ends before it starts.' }
  if (spec.ko_odds_min && spec.ko_odds_max && spec.ko_odds_min > spec.ko_odds_max)
    return { spec: null, error: 'The kick-off odds band is upside down.' }
  if (spec.odds_min && spec.odds_max && spec.odds_min > spec.odds_max)
    return { spec: null, error: 'The price band is upside down.' }

  const needsTeam =
    market === 'win' ||
    spec.score === 'team_ahead' ||
    spec.score === 'team_behind' ||
    spec.pressure === 'team' ||
    (spec.exit === 'after_goal' && spec.exit_goal !== 'any') ||
    spec.ko_odds_min != null ||
    spec.ko_odds_max != null
  if (needsTeam && !team) return { spec: null, error: 'This rule needs to say which team: the favourite, the underdog, home or away.' }
  if (spec.exit === 'at_minute' && spec.exit_minute! <= spec.minute_min)
    return { spec: null, error: 'The sell minute comes before the agent could have bought.' }
  return { spec, error: null }
}

export function isInplaySpec(x: unknown): x is InplaySpec {
  return Boolean(x && typeof x === 'object' && (x as { kind?: unknown }).kind === 'inplay')
}

const TEAM_TEXT: Record<InplayTeam, string> = {
  favourite: 'the favourite',
  underdog: 'the underdog',
  home: 'the home side',
  away: 'the away side',
}

function band(lo: number | null, hi: number | null): string {
  if (lo && hi) return `${lo.toFixed(2)}–${hi.toFixed(2)}`
  if (lo) return `${lo.toFixed(2)} or longer`
  if (hi) return `${hi.toFixed(2)} or shorter`
  return ''
}

/** The rule in plain English, built from the spec itself — so what the page
 *  says will run is exactly what will run, not the model's paraphrase. */
export function describeInplay(s: InplaySpec): { entry: string[]; exit: string; summary: string } {
  const who = s.team ? TEAM_TEXT[s.team] : null
  const buy =
    s.market === 'win' ? `${who} to win` : s.market === 'draw' ? 'the draw' : 'one more goal in the match'
  const entry: string[] = []
  if (s.team && (s.ko_odds_min || s.ko_odds_max))
    entry.push(`${who} was ${band(s.ko_odds_min, s.ko_odds_max)} to win at kick-off`)
  else if (s.team === 'favourite' || s.team === 'underdog') entry.push(`${who} as priced at kick-off`)
  entry.push(`between minute ${s.minute_min} and ${s.minute_max}`)
  const scoreText: Record<InplayScore, string | null> = {
    any: null,
    level: 'the score is level',
    goalless: 'it is still 0-0',
    team_ahead: `${who} is ahead`,
    team_behind: `${who} is behind`,
  }
  if (scoreText[s.score]) entry.push(scoreText[s.score]!)
  if (s.pressure !== 'none') {
    const g = PRESSURE_GATES[s.pressure_level]
    const span = s.pressure_window === 'match' ? 'over the whole match so far' : 'over the last 15 minutes'
    if (s.pressure === 'match') entry.push(`the game is open — combined pressure ${g.match}+ ${span}`)
    else
      entry.push(
        `${s.pressure === 'team' ? who : 'either side'} is ${s.pressure_level} — pressure ${g.own}+ and ${g.gap}+ above the other side, ${span}`,
      )
  }
  if (s.odds_min || s.odds_max) entry.push(`the price is ${band(s.odds_min, s.odds_max)}`)
  if (s.leagues) entry.push(`in ${s.leagues.join(', ')}`)

  const goal = s.exit_goal === 'any' ? 'any goal' : s.exit_goal === 'team' ? `a goal by ${who}` : `a goal against ${who}`
  const exit =
    s.exit === 'hold'
      ? 'Held to the final whistle and settled by the result.'
      : s.exit === 'at_minute'
        ? `Sold at the bid at minute ${s.exit_minute}; if the market settles first, it settles.`
        : `Sold at the bid ${s.exit_wait_min} minute${s.exit_wait_min === 1 ? '' : 's'} after ${goal} (a goal that does not stand cancels the clock). With no such goal, held to the end.`
  return { entry, exit, summary: `Buy ${buy} when ${entry.join(', ')}. ${exit}` }
}
