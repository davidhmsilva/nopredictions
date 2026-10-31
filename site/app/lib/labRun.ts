/** Backtest one agent's strategy in Postgres. Server only.
 *
 *  Shared by the written-idea route (`/api/backtest`, after Claude has turned
 *  the words into a spec) and the free one (`/api/lab/quick`, where the spec
 *  comes from a fixed list). One path, so a picked strategy and the same one
 *  typed out cannot come back with different numbers.
 */

import { unstable_cache } from 'next/cache'
import { getSql } from './db'
import {
  NBA_CAVEATS,
  computeStats,
  flatLegs,
  isNbaMarket,
  mergeBacktests,
  verdict,
  type BacktestStats,
  type RawBacktest,
  type Spec,
} from './backtest'
import type { RecentBet } from './labQuick'

export interface SpecResult {
  stats: BacktestStats
  verdict: ReturnType<typeof verdict>
  seasons: RawBacktest['seasons']
  monthly: RawBacktest['monthly']
  recent: RecentBet[]
  /** The notes that belong to the dataset rather than to one theory. */
  datasetCaveats: string[]
}

/** A rule's backtest, one leg at a time: Postgres only reads plain rules. */
export async function rawBacktest(spec: Spec): Promise<RawBacktest> {
  const sql = getSql()
  const raws: RawBacktest[] = []
  for (const leg of flatLegs(spec)) {
    const rows = isNbaMarket(leg.market)
      ? await sql`SELECT run_backtest_nba(${sql.json(leg as never)}) AS r`
      : await sql`SELECT run_backtest(${sql.json(leg as never)}) AS r`
    raws.push(rows[0].r as RawBacktest)
  }
  return mergeBacktests(raws)
}

export async function runSpec(spec: Spec): Promise<SpecResult> {
  const sql = getSql()
  const nba = isNbaMarket(spec.market)
  const raw = await rawBacktest(spec)

  // The list of games is a picture of the totals, not part of them: a database
  // without db/074 still returns a result, just without the list.
  let recent: RecentBet[] = []
  if (!nba && raw.n > 0) {
    try {
      for (const leg of flatLegs(spec)) {
        const r = await sql`SELECT run_backtest_recent(${sql.json(leg as never)}, 8) AS r`
        recent.push(...((r[0].r as RecentBet[]) ?? []))
      }
      recent = recent.sort((a, b) => b.kickoff.localeCompare(a.kickoff)).slice(0, 8)
    } catch (err) {
      console.error('recent bets', err)
    }
  }

  const stats = computeStats(raw)
  return {
    stats,
    verdict: verdict(stats),
    seasons: raw.seasons,
    monthly: raw.monthly,
    recent,
    datasetCaveats: [
      ...(nba
        ? NBA_CAVEATS
        : ['Entry price = Pinnacle closing odds, flat 1u stakes. Beating the close is the hardest version of this test.']),
      'Rest days and form only count league games in our dataset — cups are not included.',
    ],
  }
}

/** The free tests repeat — six examples and a finite picker — and the data under
 *  them changes when Stage A adds a season, not by the minute. A day is plenty. */
export const runSpecCached = unstable_cache(
  (key: string) => runSpec(JSON.parse(key) as Spec),
  ['lab-quick-v1'],
  { revalidate: 24 * 3600 },
)

/** Stable key: the same spec written with its keys in another order is the same test. */
export function specKey(spec: Spec): string {
  const o = spec as unknown as Record<string, unknown>
  const sorted: Record<string, unknown> = {}
  for (const k of Object.keys(o).sort()) if (o[k] !== undefined) sorted[k] = o[k]
  return JSON.stringify(sorted)
}
