/** A server render never waits long for the database.
 *
 *  The home page and the Marketplace read published agents while they render.
 *  Usually that is well under a second, but the database has hours where a
 *  one-row read takes a minute (measured 2026-10-30, 16:00-18:00 UTC). Past the
 *  deadline the page goes out without them and the browser asks instead, so a
 *  slow database costs a list and never the page. Null means "no answer in
 *  time, or it failed"; it never means "there are none". */
export function within<T>(p: Promise<T>, ms: number): Promise<T | null> {
  return Promise.race([
    p.catch((err) => {
      console.error('server read failed', err)
      return null
    }),
    new Promise<null>((resolve) => setTimeout(() => resolve(null), ms)),
  ])
}
