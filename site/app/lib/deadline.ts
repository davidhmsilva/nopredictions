/** A server render never waits long for a board.
 *
 *  Warm, every board answers from the shared cache in well under a second.
 *  Cold, one sweep can take 7-12s (measured on production: a cold home page
 *  at 12.7s, the same page warm at 0.26s). Past the deadline the page is
 *  rendered without that board — the browser loads it, as it always did —
 *  and the sweep keeps running, so it warms the cache for the next visitor. */
export function within<T>(p: Promise<T>, ms: number): Promise<T | null> {
  return Promise.race([
    p.catch(() => null),
    new Promise<null>((resolve) => setTimeout(() => resolve(null), ms)),
  ])
}
