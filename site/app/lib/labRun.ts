/** Run one Lab spec in Postgres. Server only.
 *
 *  Shared by the written-theory route (`/api/backtest`, after Claude has turned
 *  the words into a spec) and the free one (`/api/lab/quick`, where the spec
 *  comes from a fixed list). One path, so a picked test and the same test typed
 *  out cannot come back with different numbers.
 */

import { getSql } from './db'
import { sharedCache } from './sharedCache'
import {
  NBA_CAVEATS,
  computeStats,
  isNbaMarket,
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

export async function runSpec(spec: Spec): Promise<SpecResult> {
  const sql = getSql()
  const nba = isNbaMarket(spec.market)
  const rows = nba
    ? await sql`SELECT run_backtest_nba(${sql.json(spec as never)}) AS r`
    : await sql`SELECT run_backtest(${sql.json(spec as never)}) AS r`
  const raw = rows[0].r as RawBacktest

  // The list of games is a picture of the totals, not part of them: a database
  // without db/074 still returns a result, just without the list.
  let recent: RecentBet[] = []
  if (!nba && raw.n > 0) {
    try {
      const r = await sql`SELECT run_backtest_recent(${sql.json(spec as never)}, 8) AS r`
      recent = (r[0].r as RecentBet[]) ?? []
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
export const runSpecCached = sharedCache(
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
