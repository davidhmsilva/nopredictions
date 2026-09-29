// Shared types + stats for the Hypothesis Tester MVP.
// The strategy spec mirrors run_backtest(spec jsonb) in db/024_backtest_mvp.sql.

import { z } from 'zod'

// Leagues in bt_lab_matches (db/062): a Pinnacle close, or Betfair's where
// Football-Data no longer carries Pinnacle (partly from 2025-10, wholly 2026).
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

// Flat spec consumed by run_backtest(spec jsonb) / run_backtest_nba(spec jsonb).
export interface Spec {
  market: '1x2' | 'ou25' | 'nba_ml' | 'nba_spread' | 'nba_total'
  side: 'home' | 'draw' | 'away' | 'over' | 'under'
  leagues: string[] | null
  // NBA-only
  game_type?: 'regular' | 'playoff' | null
  spread_min?: number | null
  spread_max?: number | null
  total_min?: number | null
  total_max?: number | null
  home_form_w5_min?: number | null
  home_form_w5_max?: number | null
  away_form_w5_min?: number | null
  away_form_w5_max?: number | null
  home_avg_tp5_min?: number | null
  home_avg_tp5_max?: number | null
  away_avg_tp5_min?: number | null
  away_avg_tp5_max?: number | null
  season_start?: number | null
  season_end?: number | null
  odds_min?: number | null
  odds_max?: number | null
  fav_status: 'favorite' | 'underdog' | null
  home_team: string | null
  away_team: string | null
  home_rest_days_min?: number | null
  home_rest_days_max?: number | null
  away_rest_days_min?: number | null
  away_rest_days_max?: number | null
  home_form_pts5_min?: number | null
  home_form_pts5_max?: number | null
  away_form_pts5_min?: number | null
  away_form_pts5_max?: number | null
  home_avg_tg5_min?: number | null
  home_avg_tg5_max?: number | null
  away_avg_tg5_min?: number | null
  away_avg_tg5_max?: number | null
}

// Numeric range filters come back from the model as a flat list of conditions
// (structured outputs cap the number of nullable fields per schema, so the
// parse-side spec avoids per-field nullables).
export const FILTER_FIELDS = [
  'odds',
  'season',
  'home_rest_days',
  'away_rest_days',
  // football
  'home_form_pts5',
  'away_form_pts5',
  'home_avg_tg5',
  'away_avg_tg5',
  // NBA
  'spread',
  'total',
  'home_form_w5',
  'away_form_w5',
  'home_avg_tp5',
  'away_avg_tp5',
] as const

export const ParsedSpecSchema = z.object({
  market: z.enum(['1x2', 'ou25', 'nba_ml', 'nba_spread', 'nba_total']),
  side: z.enum(['home', 'draw', 'away', 'over', 'under']),
  leagues: z.array(z.string()),          // [] = all leagues; ignored for NBA
  game_type: z.enum(['regular', 'playoff', 'any']),  // NBA only
  fav_status: z.enum(['favorite', 'underdog', 'any']),
  home_team: z.string(),                 // '' = no filter
  away_team: z.string(),                 // '' = no filter
  filters: z.array(
    z.object({
      field: z.enum(FILTER_FIELDS),
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

// One venue's arm of run_backtest() (db/060): the same selections, priced at
// Polymarket or Kalshi. `n`/`pnl` are at a price a taker could have paid,
// `*_mid` at the closing mid, and `pin_*` are those SAME games at Pinnacle's
// close -- the venues only go back to 2024-08, so the Pinnacle yield over the
// whole test is not the thing to compare against.
export interface RawVenue {
  listed: number
  n: number
  wins: number
  pnl: number
  pnl_sq: number
  avg_odds: number | null
  pin_pnl: number | null
  pin_avg_odds: number | null
  n_mid: number
  wins_mid: number
  pnl_mid: number
  pnl_mid_sq: number
  avg_odds_mid: number | null
  pin_pnl_mid: number | null
  pin_avg_odds_mid: number | null
  agree: number
  compared: number
  dropped: number
  first_match: string | null
  last_match: string | null
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
  // Selections priced at the Betfair Exchange close (net of 5% commission)
  // because the match has no Pinnacle close. Football only.
  n_betfair?: number
  first_betfair?: string | null
  seasons: { season: number; n: number; wins: number; pnl: number }[]
  monthly: { month: string; n: number; pnl: number }[]
  venues?: Record<string, RawVenue>
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
  maxDrawdown: number
  firstMatch: string | null
  lastMatch: string | null
  nBetfair: number
  firstBetfair: string | null
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
    maxDrawdown: maxDD,
    firstMatch: raw.first_match,
    lastMatch: raw.last_match,
    nBetfair: raw.n_betfair ?? 0,
    firstBetfair: raw.first_betfair ?? null,
  }
}

// A yield with its interval, from a sum and a sum of squares.
function yieldOf(n: number, pnl: number, pnlSq: number) {
  const mean = n > 0 ? pnl / n : 0
  const variance = n > 1 ? Math.max(pnlSq / n - mean * mean, 0) : 0
  const se = n > 1 ? Math.sqrt(variance / n) : 0
  return {
    yieldPct: mean * 100,
    ci95Pct: 1.96 * se * 100,
    pValue: n > 1 && se > 0 ? 2 * (1 - normCdf(Math.abs(mean / se))) : null,
  }
}

export interface VenueArm {
  n: number
  wins: number
  avgOdds: number | null
  yieldPct: number
  ci95Pct: number
  pValue: number | null
  // the same games at Pinnacle's close
  pinYieldPct: number | null
  pinAvgOdds: number | null
}

export interface VenueStats {
  venue: 'polymarket' | 'kalshi'
  name: string
  listed: number
  // what a taker paid: Kalshi's ask at the close, Polymarket's last taker buy
  paid: VenueArm | null
  // the closing mid: an upper bound, since no taker gets it
  mid: VenueArm | null
  agreePct: number | null
  compared: number
  dropped: number
  firstMatch: string | null
  lastMatch: string | null
}

const VENUE_NAMES: Record<string, string> = { polymarket: 'Polymarket', kalshi: 'Kalshi' }

function arm(n: number, wins: number, pnl: number, pnlSq: number, avgOdds: number | null,
             pinPnl: number | null, pinAvgOdds: number | null): VenueArm | null {
  if (n <= 0) return null
  return {
    n,
    wins,
    avgOdds,
    ...yieldOf(n, pnl, pnlSq),
    pinYieldPct: pinPnl != null ? (pinPnl / n) * 100 : null,
    pinAvgOdds,
  }
}

export function computeVenueStats(raw: RawBacktest): VenueStats[] {
  const out: VenueStats[] = []
  for (const key of ['polymarket', 'kalshi'] as const) {
    const v = raw.venues?.[key]
    if (!v || v.listed <= 0) continue
    out.push({
      venue: key,
      name: VENUE_NAMES[key],
      listed: v.listed,
      paid: arm(v.n, v.wins, v.pnl, v.pnl_sq, v.avg_odds, v.pin_pnl, v.pin_avg_odds),
      mid: arm(v.n_mid, v.wins_mid, v.pnl_mid, v.pnl_mid_sq, v.avg_odds_mid, v.pin_pnl_mid,
               v.pin_avg_odds_mid),
      agreePct: v.compared > 0 ? (v.agree / v.compared) * 100 : null,
      compared: v.compared,
      dropped: v.dropped,
      firstMatch: v.first_match,
      lastMatch: v.last_match,
    })
  }
  return out
}

export type VerdictCode =
  | 'NO_MATCHES'
  | 'INSUFFICIENT_SAMPLE'
  | 'EDGE_FOUND'
  | 'SIGNIFICANTLY_NEGATIVE'
  | 'NO_EDGE'

export function verdict(s: BacktestStats): { code: VerdictCode; label: string; detail: string } {
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
      label: 'INSUFFICIENT SAMPLE',
      detail: `Only ${s.n} selections — we require at least 200 before drawing any conclusion. Numbers below are descriptive only.`,
    }
  }
  if (s.pValue != null && s.pValue < 0.05 && s.yieldPct > 0) {
    return {
      code: 'EDGE_FOUND',
      label: 'EDGE FOUND — HISTORICALLY PROFITABLE',
      detail:
        'Positive yield against the sharp closing line, statistically significant at p < 0.05. Caution: a single test can still be luck or selection bias — verify out of sample before trusting it.',
    }
  }
  if (s.pValue != null && s.pValue < 0.05 && s.yieldPct < 0) {
    return {
      code: 'SIGNIFICANTLY_NEGATIVE',
      label: 'NO EDGE — SIGNIFICANTLY UNPROFITABLE',
      detail:
        'This angle loses money at a statistically significant rate. Note: betting the opposite is not automatically profitable — the bookmaker margin cuts both ways.',
    }
  }
  return {
    code: 'NO_EDGE',
    label: 'NO EDGE — HYPOTHESIS REJECTED',
    detail:
      'The result is indistinguishable from the bookmaker margin. The market already prices this in.',
  }
}
