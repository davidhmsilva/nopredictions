/** `setInterval` that stops while the tab is hidden.
 *
 *  A tab left open in the background used to poll every board once a minute,
 *  around the clock, and each poll could rebuild a board server-side. That is
 *  part of what got the site paused on 2026-10-07 (Vercel Fluid Active CPU).
 *  Hidden tabs now ask for nothing; a tab that comes back after a full period
 *  refreshes at once instead of showing an old board until the next tick.
 *
 *  Returns the cleanup, for a `useEffect`.
 */
export function pollWhileVisible(fn: () => void, everyMs: number): () => void {
  let last = Date.now()
  const run = () => {
    last = Date.now()
    fn()
  }
  const t = setInterval(() => {
    if (!document.hidden) run()
  }, everyMs)
  const onVisible = () => {
    if (!document.hidden && Date.now() - last >= everyMs) run()
  }
  document.addEventListener('visibilitychange', onVisible)
  return () => {
    clearInterval(t)
    document.removeEventListener('visibilitychange', onVisible)
  }
}
