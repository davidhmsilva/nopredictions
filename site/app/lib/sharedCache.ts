/** `unstable_cache` inside Next; a plain in-memory TTL memo outside it.
 *
 *  The board sweeps (scout, the six US boards, Kalshi's football) run in two
 *  places: inside the site for local development, and in the board publisher
 *  on the agent server (`scripts/publish-boards.ts`), which is plain Node. Next's
 *  Data Cache only exists inside a Next request, so calling `unstable_cache`
 *  from the publisher throws. `NP_PUBLISHER=1` selects the memo instead, with
 *  the same `revalidate`, so the Kalshi index is still swept every 15 minutes
 *  and not on every cycle.
 */

import { unstable_cache } from 'next/cache'

export const IN_PUBLISHER = process.env.NP_PUBLISHER === '1'

export function sharedCache<A extends unknown[], R>(
  fn: (...args: A) => Promise<R>,
  key: string[],
  opts: { revalidate: number; tags?: string[] }
): (...args: A) => Promise<R> {
  if (!IN_PUBLISHER) return unstable_cache(fn, key, opts)
  const memo = new Map<string, { at: number; value: Promise<R> }>()
  return (...args: A) => {
    const k = JSON.stringify(args)
    const hit = memo.get(k)
    if (hit && Date.now() - hit.at < opts.revalidate * 1000) return hit.value
    const value = fn(...args)
    memo.set(k, { at: Date.now(), value })
    // A failed build is not remembered: the next cycle tries again.
    value.catch(() => {
      if (memo.get(k)?.value === value) memo.delete(k)
    })
    return value
  }
}
