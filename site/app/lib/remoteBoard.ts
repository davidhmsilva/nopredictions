/** The boards, read from the publisher instead of swept here.
 *
 *  2026-10-07: Vercel paused the whole site (Hobby, `FAIR_USE_LIMITS_EXCEEDED`
 *  on Fluid Active CPU). The sweeps were the bill: a paged Gamma sweep, the
 *  CLOB, ESPN day by day and 139 Kalshi series, parsed again every 45-60
 *  seconds for as long as any tab stayed open. They now run once a minute on
 *  the agent server (`scripts/publish-boards.ts`), which writes one JSON file
 *  per board; the site only reads those files.
 *
 *  `BOARDS_URL` unset (local development) keeps the old in-process sweep.
 *
 *  A failed read throws, and every caller already keeps the board it last
 *  had. A stale board carries its own `generatedAt`, which the page shows.
 */

export const BOARDS_URL = process.env.BOARDS_URL?.replace(/\/+$/, '') || null

/** How long a warm instance reuses a board before asking again. The
 *  publisher writes every minute, so this costs at most half a cycle. */
export const REMOTE_TTL_MS = 30_000

export async function readRemoteBoard<T>(name: string): Promise<T> {
  if (!BOARDS_URL) throw new Error('BOARDS_URL is not set')
  const res = await fetch(`${BOARDS_URL}/${name}.json`, {
    // Shared across instances for a short while, so a cold instance and an
    // ISR render do not each go to the server. Not `no-store`: that would
    // make every page that reads a board dynamic again.
    next: { revalidate: REMOTE_TTL_MS / 1000 },
    signal: AbortSignal.timeout(8000),
  })
  if (!res.ok) throw new Error(`board ${name}: HTTP ${res.status}`)
  return (await res.json()) as T
}

/** One board behind a per-instance L1 and in-flight coalescing. */
export function remoteGetter<T>(name: string): () => Promise<T> {
  let hit: { at: number; value: T } | null = null
  let inFlight: Promise<T> | null = null
  return async () => {
    if (hit && Date.now() - hit.at < REMOTE_TTL_MS) return hit.value
    if (!inFlight) {
      inFlight = readRemoteBoard<T>(name)
        .then((value) => {
          hit = { at: Date.now(), value }
          return value
        })
        .finally(() => {
          inFlight = null
        })
    }
    try {
      return await inFlight
    } catch (e) {
      if (hit) return hit.value
      throw e
    }
  }
}
