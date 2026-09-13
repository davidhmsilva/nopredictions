// Formatting shared by the Game Center's panels. Prices follow the reader's
// odds format (lib/display), with the probability beside, never instead.

import { dateText, oddsText, priceText, type OddsFormat } from '../../lib/display'

export const SETTLED_BAND = 0.01

export function odds(p: number | null | undefined, f: OddsFormat): string {
  if (p == null || p <= 0 || p >= 1) return '—'
  if (p <= SETTLED_BAND) return 'settled ✗'
  if (p >= 1 - SETTLED_BAND) return 'settled ✓'
  return priceText(p, f)
}

/** Decimal odds held as a number — our closing prices — in the reader's format. */
export function oddsDec(dec: number | null | undefined, f: OddsFormat): string {
  return oddsText(dec, f)
}

export function pct(p: number | null | undefined, digits = 0): string {
  return p == null ? '—' : `${(p * 100).toFixed(digits)}%`
}

export function money(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}k`
  return `$${v.toFixed(0)}`
}

export function signed(pp: number | null | undefined, digits = 1): string {
  return pp == null ? '—' : `${pp > 0 ? '+' : ''}${pp.toFixed(digits)}pp`
}

export interface Count {
  k: number
  n: number
}

export const rateOf = (c: Count): number | null => (c.n ? c.k / c.n : null)
export const frac = (c: Count): string => (c.n ? `${c.k}/${c.n}` : '—')

/** "1 in 83" — how a chance reads to someone who is not a statistician. */
export function oneIn(chance: number): string {
  if (!(chance > 0)) return '—'
  const n = Math.round(1 / chance)
  if (n >= 10_000) return `1 in ${Math.round(n / 1000)}k`
  if (n >= 1_000) return `1 in ${(n / 1000).toFixed(1)}k`
  return `1 in ${Math.max(2, n)}`
}

export { shortTeam as shortName } from '../../lib/teamname'

/** Sep 13 in the US, 13 Sep elsewhere — see lib/display on why the zone decides. */
export function dayMonth(iso: string): string {
  return dateText(new Date(iso))
}

export function dayMonthYear(iso: string): string {
  return dateText(new Date(iso), true)
}
