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

import { LEAGUES, isNbaMarket, type Spec } from './backtest'
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

function bet(s: Spec): string {
  switch (s.market) {
    case '1x2':
      return s.side === 'home' ? 'The home team to win' : s.side === 'away' ? 'The away team to win' : 'The draw'
    case 'ou25':
      return s.side === 'over' ? 'Over 2.5 goals in the match' : 'Under 2.5 goals in the match'
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

export function describeSpec(s: Spec, odds: (decimal: number) => string): StrategyText {
  const nba = isNbaMarket(s.market)
  const when: string[] = []

  if (s.fav_status && (s.side === 'home' || s.side === 'away')) {
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

  const rest = (who: string, lo?: number | null, hi?: number | null) => {
    const r = range(lo, hi)
    if (r) when.push(`${who} had ${r} days of rest`)
  }
  rest('The home team', s.home_rest_days_min, s.home_rest_days_max)
  rest('The away team', s.away_rest_days_min, s.away_rest_days_max)

  if (!nba) {
    const form = (who: string, lo?: number | null, hi?: number | null) => {
      const r = range(lo, hi)
      if (r) when.push(`${who} took ${r} points from its last 5 league games`)
    }
    form('The home team', s.home_form_pts5_min, s.home_form_pts5_max)
    form('The away team', s.away_form_pts5_min, s.away_form_pts5_max)
    const goals = (who: string, lo?: number | null, hi?: number | null) => {
      const r = range(lo, hi, oneDp)
      if (r) when.push(`${who}'s last 5 games averaged ${r} goals`)
    }
    goals('The home team', s.home_avg_tg5_min, s.home_avg_tg5_max)
    goals('The away team', s.away_avg_tg5_min, s.away_avg_tg5_max)
  } else {
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
    const wins = (who: string, lo?: number | null, hi?: number | null) => {
      const r = range(lo, hi)
      if (r) when.push(`${who} won ${r} of its last 5 games`)
    }
    wins('The home team', s.home_form_w5_min, s.home_form_w5_max)
    wins('The away team', s.away_form_w5_min, s.away_form_w5_max)
    const pts = (who: string, lo?: number | null, hi?: number | null) => {
      const r = range(lo, hi, oneDp, { more: 'or more', less: 'or less' })
      if (r) when.push(`${who}'s last 5 games averaged ${r} total points`)
    }
    pts('The home team', s.home_avg_tp5_min, s.home_avg_tp5_max)
    pts('The away team', s.away_avg_tp5_min, s.away_avg_tp5_max)
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
