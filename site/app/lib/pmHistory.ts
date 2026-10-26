// Polymarket price history, from data-api `/v2/prices-history`.
//
// The CLOB's `/prices-history` shut down on 2026-10-30 00:00 UTC. What
// changed, handled here once for every caller:
// - one token per request (`token_id`), oldest first;
// - `end` is EXCLUSIVE (the old `endTs` was inclusive);
// - a bucket's `price` is the price AT its `timestamp`, the bucket start
//   (measured: equal to the bucket's first minute 10/10, its last 6/10), so
//   "the last point at or before kick-off" has no lookahead;
// - 1-minute points are kept >= 7 days, 5-minute >= 60, 30-minute >= 90,
//   then only 3h / 12h buckets. `as_of` returns the point's own time, and a
//   caller that needs a fresh price checks it.
// Windows here are hours to days, far under the 10,000-point page, so no
// caller pages.

export const PM_PRICES_HISTORY = 'https://data-api.polymarket.com/v2/prices-history'

export interface HistoryPoint {
  t: number
  p: number
}

export function historyUrl(
  token: string,
  opts: { start: number; end?: number; bucketS?: number } | { asOf: number }
): string {
  const q = new URLSearchParams({ token_id: token })
  if ('asOf' in opts) {
    q.set('as_of', String(Math.floor(opts.asOf)))
  } else {
    q.set('start', String(Math.floor(opts.start)))
    if (opts.end != null) q.set('end', String(Math.floor(opts.end) + 1))
    if (opts.bucketS) q.set('bucket_seconds', String(opts.bucketS))
  }
  return `${PM_PRICES_HISTORY}?${q}`
}

export function parseHistory(body: unknown): HistoryPoint[] {
  const data = (body as { data?: Array<{ timestamp?: number; price?: number }> } | null)?.data
  if (!Array.isArray(data)) return []
  return data
    .map((x) => ({ t: Number(x.timestamp), p: Number(x.price) }))
    .filter((x) => Number.isFinite(x.t) && Number.isFinite(x.p))
}
