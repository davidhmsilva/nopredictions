'use client'

/** What the signed-in viewer already has to do with the Marketplace — their
 *  own published agents and the ones they copied — fetched once per page
 *  load and shared by every card on it. Signed out, the answer is empty and
 *  costs no database read (the route returns before it asks). */

import { useEffect, useState } from 'react'
import type { Relation } from '../components/CopyButton'

export interface MarketplaceMe {
  mine: number[]
  copies: Record<number, number>
}

const EMPTY: MarketplaceMe = { mine: [], copies: {} }
let shared: Promise<MarketplaceMe> | null = null

function load(): Promise<MarketplaceMe> {
  if (!shared) {
    shared = fetch('/api/marketplace/me', { cache: 'no-store' })
      .then((r) => r.json())
      .then((d) => (d?.ok ? { mine: d.mine ?? [], copies: d.copies ?? {} } : EMPTY))
      .catch(() => EMPTY)
  }
  return shared
}

export function useMarketplaceMe(): MarketplaceMe | null {
  const [me, setMe] = useState<MarketplaceMe | null>(null)
  useEffect(() => {
    let live = true
    load().then((m) => live && setMe(m))
    return () => {
      live = false
    }
  }, [])
  return me
}

export function relationOf(me: MarketplaceMe | null, id: number): Relation {
  if (!me) return { kind: 'none' }
  if (me.mine.includes(id)) return { kind: 'mine' }
  const copyId = me.copies[id]
  return copyId != null ? { kind: 'copied', copyId } : { kind: 'none' }
}
