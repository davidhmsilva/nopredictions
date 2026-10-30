'use client'

/** Who is signed in, and what is left of their day.
 *
 *  One fetch of /api/me, shared by every component that asks in the same page
 *  load — the header and Create's counter want the same answer, and two
 *  requests for it would be two auth round trips.
 *
 *  `refresh()` exists because the counter moves: building an agent spends one,
 *  and the number on the page has to follow without a reload.
 */

import { useCallback, useEffect, useState } from 'react'

export interface Quota {
  plan: 'free' | 'pro'
  limit: number | null
  used: number
  remaining: number | null
  allowed: boolean
  reason: 'ok' | 'signed_out' | 'quota_exhausted'
}

export interface Me {
  user: { id: string; email: string | null } | null
  plan: 'free' | 'pro'
  limits: { lab: number }
  /** Agent builds left today ('lab' is the metered feature's stored name). */
  lab: Quota | null
}

/** Module-level, so a page that mounts the header and two panels pays once. */
let shared: Promise<Me> | null = null

function load(force = false): Promise<Me> {
  if (force || !shared) {
    shared = fetch('/api/me', { cache: 'no-store' })
      .then((r) => r.json())
      .catch(
        (): Me => ({
          user: null,
          plan: 'free',
          limits: { lab: 3 },
          lab: null,
        })
      )
  }
  return shared
}

export function useSession() {
  const [me, setMe] = useState<Me | null>(null)

  const refresh = useCallback(() => {
    load(true).then(setMe)
  }, [])

  useEffect(() => {
    let live = true
    load().then((m) => {
      if (live) setMe(m)
    })
    return () => {
      live = false
    }
  }, [])

  return { me, loading: me === null, refresh }
}

/** Drop the cached answer. Called after a sign-in or sign-out so the next
 *  mount asks again instead of showing the previous user. */
export function invalidateSession() {
  shared = null
}
