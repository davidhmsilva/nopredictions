/** The Wallet tab's fixed vocabulary. Client-safe: the index page draws from
 *  it and the API decides from it which reads are free. */

/** Traders we have already written up. Free to open, for anyone: each is a
 *  different way of trading to compare against. Notes in plain words — the
 *  research labels (maker share, merge exits) live in the reports. */
export const STUDIED: { addr: string; label: string; note: string }[] = [
  { addr: '0xec5723df1ef786d95b05b2941c89b45dcb560fa7', label: 'GSX-', note: 'Buys right after the final whistle' },
  { addr: '0x2005d16a84ceefa912d4e380cd32e7ff827875ea', label: 'RN1', note: 'Posts orders on football, all day' },
  { addr: '0x204f72f35326db932158cba6adff0b9a1da95e14', label: 'swisstony', note: 'Huge volume across sports' },
  { addr: '0x2c335066fe58fe9237c3d3dc7b275c2a034a0563', label: '0x2c33…', note: 'Almost never takes a price' },
  { addr: '0xf0318c32136c2db7fec88b84869aee6a1106c80c', label: 'BreakTheBank', note: 'Big profit, no proven edge' },
]

export const STUDIED_SET = new Set(STUDIED.map((s) => s.addr))

export type LeaderPeriod = 'week' | 'month' | 'all'

export const LEADER_PERIODS: { id: LeaderPeriod; label: string; api: string }[] = [
  { id: 'week', label: 'This week', api: 'WEEK' },
  { id: 'month', label: 'This month', api: 'MONTH' },
  { id: 'all', label: 'All time', api: 'ALL' },
]

/** How many leaders per period the page shows — and therefore how many are free. */
export const LEADERS_SHOWN = 12

export interface Leader {
  rank: number
  address: string
  name: string
  image: string
  /** Polymarket's own figures: profit is GROSS of fees (see lib/wallet). */
  profit: number
  volume: number
}

export interface ProfileHit {
  address: string
  name: string
  image: string
}

/** Polymarket gives an account with no username a generated one —
 *  "0xBc43…D3-1765231687816". Shown as the short address instead. */
export function displayName(name: string, address: string): string {
  if (!name || /^0x[0-9a-f]{40}(-\d+)?$/i.test(name)) return `${address.slice(0, 6)}…${address.slice(-4)}`
  return name
}

/** $2.66M · $940K · $812 */
export function shortMoney(x: number): string {
  const a = Math.abs(x)
  const sign = x < 0 ? '−' : ''
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(a >= 1e7 ? 1 : 2)}M`
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}K`
  return `${sign}$${a.toFixed(0)}`
}
