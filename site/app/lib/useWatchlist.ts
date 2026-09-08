'use client'

/** The starred fixtures, in this browser and — on Pro — everywhere.
 *
 *  The local list is the source of truth for what the page draws, and it works
 *  with no account at all. Pro adds a mirror in Postgres so the list follows
 *  you to a phone; the two are merged by UNION on load, never by one
 *  overwriting the other, because "I starred it on my laptop and my phone
 *  deleted it" is the failure this feature exists to prevent.
 *
 *  ⚠️ Supabase is imported DYNAMICALLY and only for a Pro session. A static
 *     import here would put the client back into the board's bundle — measured
 *     at +70kB of First Load JS for every visitor, to serve the few who have
 *     the feature. The board must not pay for Pro.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

const KEY = 'np_watchlist'

function readLocal(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    // A blocked or empty store is not an error — the page works without it.
    return []
  }
}

function writeLocal(slugs: string[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(slugs))
  } catch {
    /* per-viewer convenience only */
  }
}

export interface WatchlistApi {
  slugs: string[]
  toggle: (slug: string, meta?: { home?: string; away?: string }) => void
  /** True once a Pro session's remote list has been merged in. */
  synced: boolean
}

export function useWatchlist(isPro: boolean): WatchlistApi {
  const [slugs, setSlugs] = useState<string[]>([])
  const [synced, setSynced] = useState(false)
  // Merging twice would be harmless but pointless; this stops a re-render of
  // the parent from re-running the round trip.
  const merged = useRef(false)

  useEffect(() => {
    setSlugs(readLocal())
  }, [])

  useEffect(() => {
    if (!isPro || merged.current) return
    merged.current = true

    let cancelled = false
    ;(async () => {
      try {
        const { supabaseBrowser } = await import('./supabaseBrowser')
        const supabase = supabaseBrowser()
        const { data, error } = await supabase.from('watchlist').select('slug')
        if (error || cancelled) return

        const remote = (data ?? []).map((r: { slug: string }) => r.slug)
        const local = readLocal()
        const union = Array.from(new Set([...remote, ...local]))

        // Anything starred locally before signing in is pushed up, so the
        // first Pro session adopts the list rather than replacing it.
        const missing = local.filter((s) => !remote.includes(s))
        if (missing.length) {
          const {
            data: { user },
          } = await supabase.auth.getUser()
          if (user) {
            await supabase
              .from('watchlist')
              .upsert(missing.map((slug) => ({ user_id: user.id, slug })), {
                onConflict: 'user_id,slug',
              })
          }
        }

        if (!cancelled) {
          writeLocal(union)
          setSlugs(union)
          setSynced(true)
        }
      } catch {
        // A failed sync leaves the local list exactly as it was. The star
        // still works; it just does not travel yet.
      }
    })()

    return () => {
      cancelled = true
    }
  }, [isPro])

  const toggle = useCallback(
    (slug: string, meta?: { home?: string; away?: string }) => {
      setSlugs((prev) => {
        const on = prev.includes(slug)
        const next = on ? prev.filter((s) => s !== slug) : [...prev, slug]
        writeLocal(next)

        if (isPro) {
          // Fire and forget: the local list already moved, and a star that
          // waits on a round trip feels broken. A failed write means the row
          // is missing on another device, which the next merge repairs.
          void (async () => {
            try {
              const { supabaseBrowser } = await import('./supabaseBrowser')
              const supabase = supabaseBrowser()
              const {
                data: { user },
              } = await supabase.auth.getUser()
              if (!user) return
              if (on) {
                await supabase.from('watchlist').delete().eq('user_id', user.id).eq('slug', slug)
              } else {
                await supabase.from('watchlist').upsert(
                  { user_id: user.id, slug, home: meta?.home ?? null, away: meta?.away ?? null },
                  { onConflict: 'user_id,slug' }
                )
              }
            } catch {
              /* see above */
            }
          })()
        }
        return next
      })
    },
    [isPro]
  )

  return { slugs, toggle, synced }
}
