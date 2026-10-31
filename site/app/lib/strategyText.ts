/** A pre-match agent's strategy, in words: what it backs, which games it
 *  takes, where it looks, and how it places the bet.
 *
 *  This is the step the app exists for — the user's idea turned into a rule
 *  they can read — so it is built from the SPEC, field by field, never from
 *  the model's paraphrase: what the page says will run is exactly what the
 *  runner reads. The live rule has its own (describeInplay, lib/inplaySpec).
 *
 *  Client-safe. Odds go through the caller's formatter, so every reader sees
 *  their own format.
 */

import { LEAGUES, RUN_CAP, VENUE_NO_HISTORY, isNbaMarket, isRelative, isVenueMarket, type Spec } from './backtest'
import { inplayBuyText, isInplaySpec, type InplaySpec } from './inplaySpec'

export interface StrategyText {
  /** What it backs, as one line. */
  bet: string
  /** Every condition a game has to meet. Empty means every game qualifies. */
  when: string[]
  /** Where it looks. */
  where: string
  /** How, when and at what stake it bets. */
  how: string
}

const LEAGUE_NAME = new Map<string, string>(LEAGUES.map((l) => [l.code, `${l.name} (${l.country})`]))

/** Who "the team" is, in a rule about one. */
function subject(s: Spec): string {
  return s.team ?? (s.fav_status === 'underdog' ? 'the underdog' : s.fav_status === 'favorite' ? 'the favourite' : 'the team')
}

function bet(s: Spec): string {
  if (s.side === 'team') {
    const who = subject(s)
    const line = `${who.charAt(0).toUpperCase()}${who.slice(1)} to`
    return s.market === 'nba_spread' ? `${line} cover the spread, home or away` : `${line} win, home or away`
  }
  if (s.side === 'opponent') {
    return s.market === 'nba_spread' ? `Whoever plays ${subject(s)} to cover the spread` : `Whoever plays ${subject(s)} to win`
  }
  switch (s.market) {
    case '1x2':
      return s.side === 'home' ? 'The home team to win' : s.side === 'away' ? 'The away team to win' : 'The draw'
    case 'ou25':
      return s.side === 'over' ? 'Over 2.5 goals in the match' : 'Under 2.5 goals in the match'
    case 'ou15':
      return s.side === 'over' ? 'Over 1.5 goals in the match' : 'Under 1.5 goals in the match'
    case 'ou35':
      return s.side === 'over' ? 'Over 3.5 goals in the match' : 'Under 3.5 goals in the match'
    case 'ou45':
      return s.side === 'over' ? 'Over 4.5 goals in the match' : 'Under 4.5 goals in the match'
    case 'btts':
      return s.side === 'yes' ? 'Both teams to score' : 'Both teams to score: no'
    case 'nba_ml':
      return `The ${s.side} team to win the game (moneyline)`
    case 'nba_spread':
      return `The ${s.side} team to cover the spread`
    case 'nba_total':
      return s.side === 'over' ? 'Over the total points line' : 'Under the total points line'
  }
}

/** "between a and b", "a or more", "b or fewer" — null when neither bound. */
function range(
  lo: number | null | undefined,
  hi: number | null | undefined,
  show: (v: number) => string = (v) => String(v),
  words: { more: string; less: string } = { more: 'or more', less: 'or fewer' },
): string | null {
  if (lo != null && hi != null) return lo === hi ? show(lo) : `between ${show(lo)} and ${show(hi)}`
  if (lo != null) return `${show(lo)} ${words.more}`
  if (hi != null) return `${show(hi)} ${words.less}`
  return null
}

const season = (y: number) => `${y}-${String((y + 1) % 100).padStart(2, '0')}`
const oneDp = (v: number) => v.toFixed(1)

/** Who "the team" is in a condition about the backed side. */
function backed(s: Spec): string {
  return s.side === 'home' ? 'the home team' : s.side === 'away' ? 'the away team' : 'the team'
}

type TeamPrefix = 'home' | 'away' | 'team' | 'opp'

/** One bound pair of one team's figure. Named keys are checked by Spec; these
 *  are built, so the loop below can read every prefix the same way. */
function bounds(s: Spec, prefix: TeamPrefix, figure: string): [number | null | undefined, number | null | undefined] {
  const o = s as unknown as Record<string, number | null | undefined>
  return [o[`${prefix}_${figure}_min`], o[`${prefix}_${figure}_max`]]
}

/** Its last game, in the words a bettor uses for it. */
function lastGame(who: string, lo: number | null | undefined, hi: number | null | undefined): string | null {
  if (lo == null && hi == null) return null
  if (lo === 0 && hi === 0) return `${who} drew its last game`
  if (lo === 0 && hi == null) return `${who} did not lose its last game`
  if (lo == null && hi === 0) return `${who} did not win its last game`
  if (lo === 1 && hi == null) return `${who} won its last game`
  if (lo != null && lo > 1 && hi == null) return `${who} won its last game by ${lo} or more goals`
  if (lo == null && hi === -1) return `${who} lost its last game`
  if (lo == null && hi != null && hi < -1) return `${who} lost its last game by ${-hi} or more goals`
  return `${who}'s goal difference in its last game was ${range(lo, hi, (v) => (v > 0 ? `+${v}` : String(v)), { more: 'or better', less: 'or worse' })}`
}

/** A run, in the same words. `words` is how to say it: 3 → "won 3 or more in a row". */
function run(
  who: string,
  lo: number | null | undefined,
  hi: number | null | undefined,
  words: { many: (n: string) => string; last: string; notLast: string },
): string | null {
  if (lo == null && hi == null) return null
  if (lo === 1 && hi == null) return `${who} ${words.last}`
  if (lo == null && hi === 0) return `${who} ${words.notLast}`
  const r = range(lo, hi, (v) => (v >= RUN_CAP ? `${RUN_CAP}` : String(v)))
  return `${who} ${words.many(r as string)}`
}

const RUNS = {
  win_run: { many: (n: string) => `has won ${n} in a row`, last: 'won its last game', notLast: 'did not win its last game' },
  unbeaten_run: { many: (n: string) => `is unbeaten in ${n}`, last: 'did not lose its last game', notLast: 'lost its last game' },
  loss_run: { many: (n: string) => `has lost ${n} in a row`, last: 'lost its last game', notLast: 'did not lose its last game' },
  winless_run: { many: (n: string) => `has gone ${n} without a win`, last: 'did not win its last game', notLast: 'won its last game' },
} as const

/** Which end of the ground "its last 5" means, for each way of naming a team. */
const VENUE_WORDS: Record<TeamPrefix, string> = {
  home: 'home games',
  away: 'away games',
  team: 'games at this end (home or away, as it is today)',
  opp: 'games at this end (home or away, as it is today)',
}

/** Everything a football rule asks about one team's recent games. */
function teamLines(s: Spec, prefix: TeamPrefix, who: string): string[] {
  const out: (string | null)[] = []
  const said = (figure: string, line: (r: string) => string, show?: (v: number) => string) => {
    const [lo, hi] = bounds(s, prefix, figure)
    const r = range(lo, hi, show)
    if (r) out.push(line(r))
  }
  said('rest_days', (r) => `${who} had ${r} days of rest`)
  out.push(lastGame(who, ...bounds(s, prefix, 'last_gd')))
  for (const [figure, words] of Object.entries(RUNS)) out.push(run(who, ...bounds(s, prefix, figure), words))
  said('form_pts5', (r) => `${who} took ${r} points from its last 5 league games`)
  said('venue_pts5', (r) => `${who} took ${r} points from its last 5 ${VENUE_WORDS[prefix]}`)
  said('avg_tg5', (r) => `${who}'s last 5 games averaged ${r} goals`, oneDp)
  said('avg_gf5', (r) => `${who} scored ${r} goals a game over its last 5`, oneDp)
  said('avg_ga5', (r) => `${who} conceded ${r} goals a game over its last 5`, oneDp)
  // The same condition twice is one line: a win_run of 1 and a last_gd of +1 say the same.
  return Array.from(new Set(out.filter((l): l is string => l != null)))
}

const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)

export function describeSpec(s: Spec, odds: (decimal: number) => string): StrategyText {
  const nba = isNbaMarket(s.market)
  const when: string[] = []
  const relative = isRelative(s)
  const team = cap(subject(s))

  if (relative) {
    // "The draw" and "over 2.5" do not name a team; the rule still does.
    if (s.team && s.side !== 'team' && s.side !== 'opponent') when.push(`${s.team} is playing, home or away`)
    if (s.team && s.fav_status) when.push(`${s.team} is the ${s.fav_status === 'favorite' ? 'favourite' : 'underdog'}`)
  } else if (s.fav_status && (s.side === 'home' || s.side === 'away')) {
    when.push(`${backed(s).replace(/^t/, 'T')} is the ${s.fav_status === 'favorite' ? 'favourite' : 'underdog'}`)
  }
  if (s.home_team) when.push(`Home team: ${s.home_team}`)
  if (s.away_team) when.push(`Away team: ${s.away_team}`)
  // Team lists: Create never writes them, but the runner obeys them and some of
  // the operator's agents have them, so an agent copied from the Marketplace must
  // not read as broader than it is.
  const lists = s as unknown as Record<string, string[] | undefined>
  for (const [side, key] of [['Home', 'home'], ['Away', 'away']] as const) {
    const only = lists[`${key}_team_in`]
    const not = lists[`${key}_team_not_in`]
    if (only?.length) when.push(`${side} team is one of: ${only.join(', ')}`)
    if (not?.length) when.push(`${side} team is not: ${not.join(', ')}`)
  }

  const o = range(s.odds_min, s.odds_max, odds, { more: 'or longer', less: 'or shorter' })
  if (o) when.push(`The odds are ${o}`)

  if (!nba) {
    when.push(...teamLines(s, 'home', 'The home team'))
    when.push(...teamLines(s, 'away', 'The away team'))
    when.push(...teamLines(s, 'team', team))
    when.push(...teamLines(s, 'opp', 'Its opponent'))
  } else {
    const rest = (who: string, lo?: number | null, hi?: number | null) => {
      const r = range(lo, hi)
      if (r) when.push(`${who} had ${r} days of rest`)
    }
    const people: [TeamPrefix, string][] = [['home', 'The home team'], ['away', 'The away team'], ['team', team], ['opp', 'Its opponent']]
    for (const [prefix, who] of people) rest(who, ...bounds(s, prefix, 'rest_days'))
    if (s.game_type === 'regular') when.push('Regular season games only')
    if (s.game_type === 'playoff') when.push('Play-off games only')
    // The home team's spread is negative when it is favoured.
    if (s.spread_max != null && s.spread_max < 0 && s.spread_min == null) {
      when.push(`The home team is favoured by ${-s.spread_max} points or more`)
    } else if (s.spread_min != null && s.spread_min > 0 && s.spread_max == null) {
      when.push(`The home team is an underdog by ${s.spread_min} points or more`)
    } else {
      const r = range(s.spread_min, s.spread_max, (v) => (v > 0 ? `+${v}` : String(v)), { more: 'or higher', less: 'or lower' })
      if (r) when.push(`The home team's spread is ${r}`)
    }
    const total = range(s.total_min, s.total_max, String, { more: 'or more', less: 'or less' })
    if (total) when.push(`The points total is ${total}`)
    for (const [prefix, who] of people) {
      const [wl, wh] = bounds(s, prefix, 'form_w5')
      const w = range(wl, wh)
      if (w) when.push(`${who} won ${w} of its last 5 games`)
      const [pl, ph] = bounds(s, prefix, 'avg_tp5')
      const p = range(pl, ph, oneDp, { more: 'or more', less: 'or less' })
      if (p) when.push(`${who}'s last 5 games averaged ${p} total points`)
    }
  }

  if (s.season_start != null || s.season_end != null) {
    const r =
      s.season_start != null && s.season_end != null
        ? `${season(s.season_start)} to ${season(s.season_end)}`
        : s.season_start != null
          ? `${season(s.season_start)} onwards`
          : `up to ${season(s.season_end as number)}`
    when.push(`Seasons ${r}`)
  }

  const where = nba
    ? 'The NBA'
    : s.leagues?.length
      ? s.leagues.map((c) => LEAGUE_NAME.get(c) ?? c).join(', ')
      : isVenueMarket(s.market)
        ? `Every league with Polymarket history — ${LEAGUES.length - VENUE_NO_HISTORY.length} European competitions`
        : `Every league we cover — ${LEAGUES.length} European competitions`

  const how = nba
    ? 'Backtest only for now: there are no NBA games for an agent to trade on this rule yet.'
    : 'One paper bet of 1 unit per game, on Polymarket, in the 45 minutes before kick-off, at the price on offer.'

  return { bet: bet(s), when, where, how }
}

const SHORT_LEAGUE = new Map<string, string>(LEAGUES.map((l) => [l.code, l.name]))

/** The strategy in one line, for a card: what it backs and where — or, for a
 *  live rule, what it buys during the match. */
export function strategyLine(spec: Spec | InplaySpec | null, odds: (decimal: number) => string): string {
  if (!spec) return 'A hand-written agent'
  if (isInplaySpec(spec)) {
    const b = inplayBuyText(spec)
    return `${b.charAt(0).toUpperCase()}${b.slice(1)}, during the match`
  }
  const t = describeSpec(spec, odds)
  const where = isNbaMarket(spec.market)
    ? 'NBA'
    : spec.leagues?.length
      ? spec.leagues.length > 2
        ? `${spec.leagues.length} leagues`
        : spec.leagues.map((c) => SHORT_LEAGUE.get(c) ?? c).join(' · ')
      : 'every league'
  const more = t.when.length ? ` · ${t.when.length} condition${t.when.length === 1 ? '' : 's'}` : ''
  return `${t.bet} · ${where}${more}`
}
