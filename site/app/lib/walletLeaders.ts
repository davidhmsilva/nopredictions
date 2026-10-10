/** Polymarket's sports leaderboard and profile search. Server only.
 *
 *  Both are public, keyless endpoints, verified 2026-10-10:
 *    data-api  /v1/leaderboard?category=SPORTS&timePeriod=WEEK|MONTH|ALL&orderBy=PNL
 *    gamma     /public-search?q=<name>&search_profiles=true
 *  There is no football-only category (`SOCCER` is refused), so "sports" it is.
 *
 *  ⚠️ The leaderboard's `pnl` is GROSS of fees and leaves rebates out — the
 *     same figure the wallet analyser reconciles against. The page labels it as
 *     Polymarket's number, never as ours.
 */

import { sharedCache } from './sharedCache'
import {
  LEADERS_SHOWN,
  LEADER_PERIODS,
  STUDIED_SET,
  type Leader,
  type LeaderPeriod,
  type ProfileHit,
} from './walletTerms'

const DATA_API = 'https://data-api.polymarket.com'
const GAMMA_API = 'https://gamma-api.polymarket.com'
const ADDRESS = /^0x[0-9a-f]{40}$/

async function getJson(url: string, revalidate: number): Promise<unknown> {
  const r = await fetch(url, { signal: AbortSignal.timeout(10_000), next: { revalidate } })
  if (!r.ok) throw new Error(`HTTP ${r.status} from ${new URL(url).host}`)
  return r.json()
}

async function fetchLeaders(period: LeaderPeriod): Promise<Leader[]> {
  const api = LEADER_PERIODS.find((p) => p.id === period)?.api ?? 'MONTH'
  const rows = (await getJson(
    `${DATA_API}/v1/leaderboard?category=SPORTS&timePeriod=${api}&orderBy=PNL&limit=30`,
    3600,
  )) as { rank: string; proxyWallet: string; userName?: string; profileImage?: string; pnl: number; vol: number }[]
  if (!Array.isArray(rows)) return []
  return rows
    // A row with no volume is a reward or a transfer, not a trader.
    .filter((r) => ADDRESS.test(String(r.proxyWallet).toLowerCase()) && Number(r.vol) > 0)
    .slice(0, LEADERS_SHOWN)
    .map((r, i) => ({
      rank: i + 1,
      address: r.proxyWallet.toLowerCase(),
      name: r.userName ?? '',
      image: r.profileImage ?? '',
      profit: Number(r.pnl) || 0,
      volume: Number(r.vol) || 0,
    }))
}

export const leaders = sharedCache(fetchLeaders, ['wallet-leaders-v1'], { revalidate: 3600 })

/** Free to read without an account: the traders we wrote up, and whoever the
 *  page is showing on a leaderboard right now. A leaderboard that cannot be
 *  read makes nobody free rather than everybody. */
export async function isFreeWallet(address: string): Promise<boolean> {
  const a = address.toLowerCase()
  if (STUDIED_SET.has(a)) return true
  const lists = await Promise.all(LEADER_PERIODS.map((p) => leaders(p.id).catch(() => [] as Leader[])))
  return lists.some((l) => l.some((x) => x.address === a))
}

async function fetchProfiles(q: string): Promise<ProfileHit[]> {
  const d = (await getJson(
    `${GAMMA_API}/public-search?q=${encodeURIComponent(q)}&search_profiles=true&limit_per_type=8`,
    3600,
  )) as { profiles?: { name?: string; pseudonym?: string; proxyWallet?: string; profileImage?: string }[] }
  return (d.profiles ?? [])
    .filter((p) => p.proxyWallet && ADDRESS.test(p.proxyWallet.toLowerCase()))
    .slice(0, 8)
    .map((p) => ({
      address: p.proxyWallet!.toLowerCase(),
      name: p.name || p.pseudonym || '',
      image: p.profileImage ?? '',
    }))
}

export const searchProfiles = sharedCache(fetchProfiles, ['wallet-search-v1'], { revalidate: 3600 })
