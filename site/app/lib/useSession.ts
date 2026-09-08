'use client'

/** Who is signed in, and what is left of their day.
 *
 *  One fetch of /api/me, shared by every component that asks in the same page
 *  load — the header, the Lab's counter and the Wallet's counter all want the
 *  same answer, and three requests for it would be three auth round trips.
 *
 *  `refresh()` exists because the counters move: a Lab run spends one, and the
 *  number in the header has to follow without a page reload.
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
  limits: { lab: number; wallet: number }
  lab: Quota | null
  wallet: Quota | null
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
          limits: { lab: 3, wallet: 3 },
          lab: null,
          wallet: null,
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
