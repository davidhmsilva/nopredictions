/** The Lab's free half: the bet picker and the popular tests.
 *
 *  Client-safe — the page draws the picker from these lists, and the server
 *  turns the same choices into a spec. Neither path calls a model, so neither
 *  spends a Lab use: the quota exists because a written theory costs a Claude
 *  call, and a picked one costs one cached SQL query.
 *
 *  🔑 The server never runs a spec the browser sent. It receives an example id
 *     or three picker choices and builds the spec itself, so the set of things
 *     that can run for free is finite (5 sides × 23 leagues × 6 price bands, plus
 *     the examples) and every one of them caches.
 */

import { LEAGUES, type Spec } from './backtest'

export type QuickSide = 'home' | 'draw' | 'away' | 'over' | 'under'

export const QUICK_SIDES: { id: QuickSide; label: string }[] = [
  { id: 'home', label: 'Home win' },
  { id: 'draw', label: 'Draw' },
  { id: 'away', label: 'Away win' },
  { id: 'over', label: 'Over 2.5 goals' },
  { id: 'under', label: 'Under 2.5 goals' },
]

/** Decimal bounds. `lo` inclusive, `hi` inclusive, as run_backtest reads them. */
export const ODDS_BANDS: { id: string; lo: number | null; hi: number | null }[] = [
  { id: 'any', lo: null, hi: null },
  { id: 'u150', lo: null, hi: 1.5 },
  { id: '150-200', lo: 1.5, hi: 2.0 },
  { id: '200-300', lo: 2.0, hi: 3.0 },
  { id: '300-500', lo: 3.0, hi: 5.0 },
  { id: 'o500', lo: 5.0, hi: null },
]

export const ALL_LEAGUES = 'all'

export interface Picked {
  side: QuickSide
  league: string
  odds: string
}

export const DEFAULT_PICK: Picked = { side: 'draw', league: 'ITA-SB', odds: 'any' }

export interface Featured {
  id: string
  /** What the chip says, and the result's title. */
  title: string
  spec: Spec
}

/** Chosen to show the range of answers, not to flatter the product: one that
 *  made money and probably should not be trusted, one that made money and
 *  the price agreed, one that loses heavily, and three that are the margin. */
export const FEATURED: Featured[] = [
  {
    id: 'draws-serie-b',
    title: 'Draws in Serie B',
    spec: base('1x2', 'draw', ['ITA-SB']),
  },
  {
    id: 'heavy-home-favourites',
    title: 'Home favourites shorter than 1.30',
    spec: { ...base('1x2', 'home', null), odds_max: 1.3 },
  },
  {
    id: 'pl-home-favourites',
    title: 'Premier League home favourites under 1.50',
    spec: { ...base('1x2', 'home', ['ENG-PR']), odds_max: 1.5, fav_status: 'favorite' },
  },
  {
    id: 'away-longshots',
    title: 'Away teams at 8.00 or longer',
    spec: { ...base('1x2', 'away', null), odds_min: 8 },
  },
  {
    id: 'overs-high-scoring',
    title: 'Over 2.5 when both teams have been scoring',
    spec: { ...base('ou25', 'over', null), home_avg_tg5_min: 3, away_avg_tg5_min: 3 },
  },
  {
    id: 'la-liga-bad-form',
    title: 'La Liga teams in bad form, at home',
    spec: { ...base('1x2', 'home', ['ESP-LL']), home_form_pts5_max: 4 },
  },
]

function base(market: Spec['market'], side: Spec['side'], leagues: string[] | null): Spec {
  return { market, side, leagues, fav_status: null, home_team: null, away_team: null }
}

export function featuredById(id: string): Featured | null {
  return FEATURED.find((f) => f.id === id) ?? null
}

export function leagueName(code: string): string {
  if (code === ALL_LEAGUES) return 'every league'
  return LEAGUES.find((l) => l.code === code)?.name ?? code
}

/** The picker's three choices → a spec and a title, or null if any choice is
 *  not on the lists. Null is a refusal, never a guess. */
export function pickedSpec(p: Picked): { spec: Spec; title: string } | null {
  const side = QUICK_SIDES.find((s) => s.id === p.side)
  const band = ODDS_BANDS.find((b) => b.id === p.odds)
  const leagueOk = p.league === ALL_LEAGUES || LEAGUES.some((l) => l.code === p.league)
  if (!side || !band || !leagueOk) return null
  const spec: Spec = base(
    side.id === 'over' || side.id === 'under' ? 'ou25' : '1x2',
    side.id,
    p.league === ALL_LEAGUES ? null : [p.league],
  )
  if (band.lo != null) spec.odds_min = band.lo
  if (band.hi != null) spec.odds_max = band.hi
  const where = p.league === ALL_LEAGUES ? 'every league' : leagueName(p.league)
  const price = band.id === 'any' ? '' : ` · ${bandText(band, (d) => d.toFixed(2))}`
  return { spec, title: `${side.label} · ${where}${price}` }
}

/** "shorter than 1.50", "1.50 – 2.00", "5.00 or longer" — in whichever odds
 *  format the caller formats with. Written as shorter/longer rather than
 *  under/over so it stays true when the reader's format is a percentage, which
 *  runs the other way. */
export function bandText(
  b: { lo: number | null; hi: number | null },
  fmt: (dec: number) => string,
): string {
  if (b.lo == null && b.hi == null) return 'Any odds'
  if (b.lo == null) return `Shorter than ${fmt(b.hi!)}`
  if (b.hi == null) return `${fmt(b.lo)} or longer`
  return `${fmt(b.lo)} – ${fmt(b.hi)}`
}

/** One bet the test would have made — `run_backtest_recent()` (db/074). */
export interface RecentBet {
  kickoff: string
  league: string
  home: string
  away: string
  home_score: number | null
  away_score: number | null
  odds: number
  won: boolean
  pnl: number
}
