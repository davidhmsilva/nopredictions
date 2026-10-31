// Shared types + stats for the Hypothesis Tester MVP.
// The strategy spec mirrors run_backtest(spec jsonb) in db/024_backtest_mvp.sql.

import { z } from 'zod'

// Leagues actually present in bt_features (have Pinnacle closing odds).
export const LEAGUES = [
  { code: 'ENG-PR', name: 'Premier League', country: 'England', tier: 1 },
  { code: 'ENG-CH', name: 'Championship', country: 'England', tier: 2 },
  { code: 'ENG-L1', name: 'League One', country: 'England', tier: 3 },
  { code: 'ENG-L2', name: 'League Two', country: 'England', tier: 4 },
  { code: 'ENG-CON', name: 'National League', country: 'England', tier: 5 },
  { code: 'ESP-LL', name: 'La Liga', country: 'Spain', tier: 1 },
  { code: 'ESP-L2', name: 'Segunda División', country: 'Spain', tier: 2 },
  { code: 'ITA-SA', name: 'Serie A', country: 'Italy', tier: 1 },
  { code: 'ITA-SB', name: 'Serie B', country: 'Italy', tier: 2 },
  { code: 'GER-BL1', name: 'Bundesliga', country: 'Germany', tier: 1 },
  { code: 'GER-BL2', name: '2. Bundesliga', country: 'Germany', tier: 2 },
  { code: 'FRA-L1', name: 'Ligue 1', country: 'France', tier: 1 },
  { code: 'FRA-L2', name: 'Ligue 2', country: 'France', tier: 2 },
  { code: 'NED-ED', name: 'Eredivisie', country: 'Netherlands', tier: 1 },
  { code: 'POR-PL', name: 'Primeira Liga', country: 'Portugal', tier: 1 },
  { code: 'BEL-JPL', name: 'Jupiler Pro League', country: 'Belgium', tier: 1 },
  { code: 'TUR-SL', name: 'Süper Lig', country: 'Turkey', tier: 1 },
  { code: 'GRE-SL', name: 'Super League', country: 'Greece', tier: 1 },
  { code: 'SCO-PR', name: 'Scottish Premiership', country: 'Scotland', tier: 1 },
  { code: 'SCO-CH', name: 'Scottish Championship', country: 'Scotland', tier: 2 },
  { code: 'SCO-L1', name: 'Scottish League One', country: 'Scotland', tier: 3 },
  { code: 'SCO-L2', name: 'Scottish League Two', country: 'Scotland', tier: 4 },
] as const

export const LEAGUE_CODES = new Set<string>(LEAGUES.map(l => l.code))

// NBA arm (db/027_nba_odds.sql). Separate table, separate SQL function, and a
// materially different dataset — see NBA_CAVEATS.
export const NBA_SEASON_MIN = 2014
export const NBA_SEASON_MAX = 2021
export const NBA_MARKETS = ['nba_ml', 'nba_spread', 'nba_total'] as const

export const NBA_CAVEATS = [
  `NBA coverage is the ${NBA_SEASON_MIN}-15 to ${NBA_SEASON_MAX}-22 seasons only (10,006 games) — nothing since. The source archive is no longer maintained.`,
  "Entry price is sportsbookreview's consensus closing line, not Pinnacle — a slightly softer benchmark than the football tests.",
  'Spread and total prices are not archived, so those backtests assume the market-standard -110 (1.909 decimal). Moneyline uses real archived prices.',
  'Pushes (margin or total landing exactly on the line) return the stake and count as neither a win nor a loss.',
  'No opening price is archived for the NBA, so closing-line value cannot be measured on these tests.',
]

export function isNbaMarket(market: string): boolean {
  return (NBA_MARKETS as readonly string[]).includes(market)
}

// Both teams to score and the other goal lines (db/085). Pinnacle's close has no
// price for them, so they replay over what takers really paid on Polymarket: about a
// year, in its own table and SQL function. ⚠️ Mirrored in GOAL_MARKETS in
// agent/lab_strategy_runner.py.
export const VENUE_MARKETS = ['btts', 'ou15', 'ou35', 'ou45'] as const
export const VENUE_SEASON_MIN = 2025
export const VENUE_SEASON_MAX = 2026

/** Never on Polymarket's board in the history, so these markets have no replay
 *  there and the runner will not trade them. ⚠️ Mirrored in NO_VENUE_HISTORY. */
export const VENUE_NO_HISTORY = ['SCO-CH', 'SCO-L1', 'SCO-L2'] as const

export const VENUE_CAVEATS = [
  "This is about a year at Polymarket's own prices, from October 2025 on, not 14 years at Pinnacle's close. A year says less, whatever the number.",
  "Each bet is at the last price a taker paid in the hour before kick-off, plus the market's own fee: none until about March 2026, then 3% and now 5% of p × (1 − p). A game with no such price is not counted.",
  'Smaller leagues joined later: League One, League Two and the National League in March 2026, Belgium and Greece in August 2026. The Scottish Championship, League One and League Two have no history, so an agent on these markets does not trade them.',
  "This replay charges the fee. An agent's paper record does not yet, on any market, so its record reads a little better than the same bets would have paid.",
]

export function isVenueMarket(market: string): boolean {
  return (VENUE_MARKETS as readonly string[]).includes(market)
}

/** Can this market be backed this way? Only the venue markets are strict, because
 *  only their function refuses: there is no team to back on a goals market, and no
 *  "yes" on a goal line. */
export function marketTakesSide(market: string, side: string): boolean {
  if (market === 'btts') return side === 'yes' || side === 'no'
  if (isVenueMarket(market)) return side === 'over' || side === 'under'
  return side !== 'yes' && side !== 'no'
}

/** What a rule can ask about a team's recent football games. Each is a filter
 *  under home_ and away_ (columns of bt_features, db/024 and db/083) and under
 *  team_ and opp_ (see flatLegs). ⚠️ Mirrored in TEAM_FIGURES in
 *  agent/lab_strategy_runner.py, which reads every one of them live. */
export const TEAM_FIGURES = [
  'rest_days',
  'form_pts5',
  'avg_tg5',
  'avg_gf5',
  'avg_ga5',
  'venue_pts5',
  'last_gd',
  'win_run',
  'unbeaten_run',
  'loss_run',
  'winless_run',
] as const

/** How far back a run is counted, on both sides (db/083): a longer one reads as this. */
export const RUN_CAP = 10

const NBA_FIGURES = ['form_w5', 'avg_tp5'] as const
const TEAM_PREFIXES = ['home', 'away', 'team', 'opp'] as const

type TeamBound = `${(typeof TEAM_PREFIXES)[number]}_${(typeof TEAM_FIGURES)[number] | (typeof NBA_FIGURES)[number]}_${'min' | 'max'}`

// The spec consumed by run_backtest(spec jsonb) / run_backtest_nba(spec jsonb),
// once flatLegs has made it flat.
export interface Spec extends Partial<Record<TeamBound, number | null>> {
  market: '1x2' | 'ou25' | (typeof VENUE_MARKETS)[number] | 'nba_ml' | 'nba_spread' | 'nba_total'
  /** 'team' and 'opponent' back the team the rule is about, or whoever plays it.
   *  'yes' and 'no' are both teams to score. */
  side: 'home' | 'draw' | 'away' | 'over' | 'under' | 'yes' | 'no' | 'team' | 'opponent'
  leagues: string[] | null
  // NBA-only
  game_type?: 'regular' | 'playoff' | null
  spread_min?: number | null
  spread_max?: number | null
  total_min?: number | null
  total_max?: number | null
  season_start?: number | null
  season_end?: number | null
  odds_min?: number | null
  odds_max?: number | null
  fav_status: 'favorite' | 'underdog' | null
  home_team: string | null
  away_team: string | null
  /** A named team at either end: the team this rule is about. */
  team?: string | null
}

/** Does this rule speak of "the team" rather than of the home or away side? */
export function isRelative(s: Pick<Spec, 'side' | 'team'>): boolean {
  return s.side === 'team' || s.side === 'opponent' || Boolean(s.team)
}

/** Can a relative rule tell which team it means, in every game? One that cannot
 *  would bet both ends of every fixture. */
export function namesItsTeam(s: Pick<Spec, 'side' | 'team' | 'fav_status'>): boolean {
  if (s.side === 'team') return Boolean(s.team || s.fav_status)
  if (s.side === 'opponent') return Boolean(s.team)
  return true
}

/** A rule about "the team" (a named club at either end, the favourite wherever
 *  it plays) as the two plain rules it stands for: that team at home, and that
 *  team away. team_* and opp_* bounds become home_* and away_* on each.
 *
 *  Everything that runs a rule reads only plain rules, so the backtest and the
 *  live agent cannot disagree about who "the team" is, and a new condition needs
 *  no second definition. ⚠️ Mirrored in flat_legs in agent/lab_strategy_runner.py. */
export function flatLegs(s: Spec): Spec[] {
  if (!isRelative(s)) return [s]
  const ends = [
    ['home', 'away'],
    ['away', 'home'],
  ] as const
  return ends.map(([own, other]) => {
    const leg: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(s)) {
      if (k === 'team') continue
      if (k.startsWith('team_')) leg[`${own}_${k.slice(5)}`] = v
      else if (k.startsWith('opp_')) leg[`${other}_${k.slice(4)}`] = v
      else leg[k] = v
    }
    if (s.side === 'team') leg.side = own
    if (s.side === 'opponent') leg.side = other
    if (s.team) leg[`${own}_team`] = s.team
    return leg as unknown as Spec
  })
}

// Numeric range filters come back from the model as a flat list of conditions
// (structured outputs cap the number of nullable fields per schema, so the
// parse-side spec avoids per-field nullables).
export const FILTER_FIELDS: string[] = [
  'odds',
  'season',
  // NBA
  'spread',
  'total',
  ...TEAM_PREFIXES.flatMap((p) => [...TEAM_FIGURES, ...NBA_FIGURES].map((f) => `${p}_${f}`)),
]

export const ParsedSpecSchema = z.object({
  market: z.enum(['1x2', 'ou25', ...VENUE_MARKETS, 'nba_ml', 'nba_spread', 'nba_total']),
  side: z.enum(['home', 'draw', 'away', 'over', 'under', 'yes', 'no', 'team', 'opponent']),
  leagues: z.array(z.string()),          // [] = all leagues; ignored for NBA
  game_type: z.enum(['regular', 'playoff', 'any']),  // NBA only
  fav_status: z.enum(['favorite', 'underdog', 'any']),
  home_team: z.string(),                 // '' = no filter
  away_team: z.string(),                 // '' = no filter
  team: z.string(),                      // '' = no team named; either end of the fixture
  filters: z.array(
    z.object({
      field: z.enum(FILTER_FIELDS as [string, ...string[]]),
      op: z.enum(['gte', 'lte']),
      value: z.number(),
    }),
  ),
})
export type ParsedSpec = z.infer<typeof ParsedSpecSchema>

export const ParseResultSchema = z.object({
  supported: z.boolean(),
  reason: z.string(),         // '' when supported
  suggestion: z.string(),     // '' when none
  interpretation: z.string(), // '' when unsupported
  caveats: z.array(z.string()),
  spec: ParsedSpecSchema.nullable(),
})
export type ParseResult = z.infer<typeof ParseResultSchema>

// Convert the parse-side spec into the flat SQL spec.
export function toSqlSpec(p: ParsedSpec): Spec {
  const nba = isNbaMarket(p.market)
  const spec: Spec = {
    market: p.market,
    side: p.side,
    leagues: nba || p.leagues.length === 0 ? null : p.leagues,
    fav_status: p.fav_status === 'any' ? null : p.fav_status,
    home_team: p.home_team.trim() || null,
    away_team: p.away_team.trim() || null,
  }
  if (p.team.trim()) spec.team = p.team.trim()
  if (nba && p.game_type !== 'any') spec.game_type = p.game_type
  for (const f of p.filters) {
    if (f.field === 'season') {
      if (f.op === 'gte') spec.season_start = Math.round(f.value)
      else spec.season_end = Math.round(f.value)
    } else {
      const key = `${f.field}_${f.op === 'gte' ? 'min' : 'max'}` as keyof Spec
      ;(spec as unknown as Record<string, unknown>)[key] = f.value
    }
  }
  return spec
}

// Raw payload returned by run_backtest()
export interface RawBacktest {
  n: number
  wins: number
  pushes?: number       // NBA spread/total only: landed exactly on the line
  pnl: number
  pnl_sq: number
  avg_odds: number | null
  n_open: number
  pnl_open: number | null
  clv_avg: number | null
  first_match: string | null
  last_match: string | null
  seasons: { season: number; n: number; wins: number; pnl: number }[]
  monthly: { month: string; n: number; pnl: number }[]
  /** Venue markets only: the same bets at the book's mid. A ceiling, never a result:
   *  nobody buys at the mid. */
  pnl_mid?: number
}

/** The result of a rule's legs as one result. They are different bets on different
 *  fixtures, so everything adds up; the averages are weighted by what they
 *  averaged over. A plain rule has one leg and comes back as it went in. */
export function mergeBacktests(raws: RawBacktest[]): RawBacktest {
  if (raws.length === 1) return raws[0]
  const sum = (f: (r: RawBacktest) => number | null | undefined) => raws.reduce((t, r) => t + Number(f(r) ?? 0), 0)
  const n = sum((r) => r.n)
  const nOpen = sum((r) => r.n_open)
  const edge = (xs: (string | null)[], pick: (a: string, b: string) => string) =>
    xs.filter((x): x is string => x != null).reduce<string | null>((a, b) => (a == null ? b : pick(a, b)), null)

  const seasons = new Map<number, RawBacktest['seasons'][number]>()
  for (const row of raws.flatMap((r) => r.seasons)) {
    const had = seasons.get(row.season)
    seasons.set(row.season, had ? { ...had, n: had.n + row.n, wins: had.wins + row.wins, pnl: had.pnl + row.pnl } : row)
  }
  const monthly = new Map<string, RawBacktest['monthly'][number]>()
  for (const row of raws.flatMap((r) => r.monthly)) {
    const had = monthly.get(row.month)
    monthly.set(row.month, had ? { ...had, n: had.n + row.n, pnl: had.pnl + row.pnl } : row)
  }

  return {
    n,
    wins: sum((r) => r.wins),
    pushes: sum((r) => r.pushes),
    pnl: sum((r) => r.pnl),
    pnl_sq: sum((r) => r.pnl_sq),
    avg_odds: n ? sum((r) => (r.avg_odds ?? 0) * r.n) / n : null,
    n_open: nOpen,
    pnl_open: nOpen ? sum((r) => r.pnl_open) : null,
    clv_avg: nOpen ? sum((r) => (r.clv_avg ?? 0) * r.n_open) / nOpen : null,
    pnl_mid: raws.some((r) => r.pnl_mid != null) ? sum((r) => r.pnl_mid) : undefined,
    first_match: edge(raws.map((r) => r.first_match), (a, b) => (new Date(b) < new Date(a) ? b : a)),
    last_match: edge(raws.map((r) => r.last_match), (a, b) => (new Date(b) > new Date(a) ? b : a)),
    seasons: Array.from(seasons.values()).sort((a, b) => a.season - b.season),
    monthly: Array.from(monthly.values()).sort((a, b) => a.month.localeCompare(b.month)),
  }
}

function normCdf(x: number): number {
  // Abramowitz & Stegun 7.1.26 via erf
  const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2)
  const erf =
    1 -
    (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t *
      Math.exp((-x * x) / 2)
  return x >= 0 ? 0.5 * (1 + erf) : 0.5 * (1 - erf)
}

export interface BacktestStats {
  n: number
  wins: number
  pushes: number
  hitRatePct: number
  avgOdds: number | null
  pnl: number
  yieldPct: number
  ci95Pct: number
  pValue: number | null
  clvPct: number | null
  yieldOpenPct: number | null
  nOpen: number
  /** Venue markets: the return at the mid, a ceiling. Null elsewhere. */
  midYieldPct: number | null
  maxDrawdown: number
  firstMatch: string | null
  lastMatch: string | null
}

export function computeStats(raw: RawBacktest): BacktestStats {
  const n = raw.n
  const mean = n > 0 ? raw.pnl / n : 0
  const variance = n > 1 ? Math.max(raw.pnl_sq / n - mean * mean, 0) : 0
  const se = n > 1 ? Math.sqrt(variance / n) : 0
  const z = se > 0 ? mean / se : 0
  const pValue = n > 1 && se > 0 ? 2 * (1 - normCdf(Math.abs(z))) : null

  let peak = 0
  let cum = 0
  let maxDD = 0
  for (const m of raw.monthly) {
    cum += m.pnl
    if (cum > peak) peak = cum
    if (peak - cum > maxDD) maxDD = peak - cum
  }

  const pushes = raw.pushes ?? 0
  const decided = n - pushes
  return {
    n,
    wins: raw.wins,
    pushes,
    // pushes return the stake, so they belong in neither half of the hit rate
    hitRatePct: decided > 0 ? (raw.wins / decided) * 100 : 0,
    avgOdds: raw.avg_odds,
    pnl: raw.pnl,
    yieldPct: mean * 100,
    ci95Pct: 1.96 * se * 100,
    pValue,
    clvPct: raw.clv_avg != null ? raw.clv_avg * 100 : null,
    yieldOpenPct: raw.n_open > 0 && raw.pnl_open != null ? (raw.pnl_open / raw.n_open) * 100 : null,
    nOpen: raw.n_open,
    midYieldPct: n > 0 && raw.pnl_mid != null ? (raw.pnl_mid / n) * 100 : null,
    maxDrawdown: maxDD,
    firstMatch: raw.first_match,
    lastMatch: raw.last_match,
  }
}

export type VerdictCode =
  | 'NO_MATCHES'
  | 'INSUFFICIENT_SAMPLE'
  | 'EDGE_FOUND'
  | 'SIGNIFICANTLY_NEGATIVE'
  | 'NO_EDGE'

export function verdict(s: BacktestStats, market?: string): { code: VerdictCode; label: string; detail: string } {
  const venue = market != null && isVenueMarket(market)
  if (s.n === 0) {
    return {
      code: 'NO_MATCHES',
      label: 'NO MATCHING SELECTIONS',
      detail: 'No historical matches satisfy these filters. Loosen the conditions and try again.',
    }
  }
  if (s.n < 200) {
    return {
      code: 'INSUFFICIENT_SAMPLE',
      label: venue ? 'A RECORD, NOT A RESULT' : 'INSUFFICIENT SAMPLE',
      detail: `Only ${s.n} selections — we require at least 200 before drawing any conclusion. Numbers below are descriptive only.`,
    }
  }
  if (s.pValue != null && s.pValue < 0.05 && s.yieldPct > 0) {
    return {
      code: 'EDGE_FOUND',
      label: 'EDGE FOUND — HISTORICALLY PROFITABLE',
      detail: venue
        ? "Positive yield at Polymarket's own prices over about a year, statistically significant at p < 0.05. Caution: one year and a single test can still be luck or selection bias — verify out of sample before trusting it."
        : 'Positive yield against the Pinnacle closing line, statistically significant at p < 0.05. Caution: a single test can still be luck or selection bias — verify out of sample before trusting it.',
    }
  }
  if (s.pValue != null && s.pValue < 0.05 && s.yieldPct < 0) {
    return {
      code: 'SIGNIFICANTLY_NEGATIVE',
      label: 'NO EDGE — SIGNIFICANTLY UNPROFITABLE',
      detail: venue
        ? 'This angle loses money at a statistically significant rate over about a year. Note: betting the opposite is not automatically profitable — the spread and the fee cut both ways.'
        : 'This angle loses money at a statistically significant rate. Note: betting the opposite is not automatically profitable — the bookmaker margin cuts both ways.',
    }
  }
  return {
    code: 'NO_EDGE',
    label: 'NO EDGE — HYPOTHESIS REJECTED',
    detail: venue
      ? 'Over about a year at these prices, the result is indistinguishable from what the spread and the fee cost.'
      : 'The result is indistinguishable from the bookmaker margin. The market already prices this in.',
  }
}
