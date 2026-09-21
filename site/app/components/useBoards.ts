'use client'

/** The boards as rows, for any page that shows them.
 *
 *  The soccer page and the home page both need soccer; the home page needs
 *  every US sport as well. Loading lives here once so the two pages cannot
 *  come to disagree about what a row holds.
 *
 *  🔑 Soccer's two feeds arrive separately and that is deliberate. Polymarket's
 *     whole board is one paged Gamma sweep and answers in well under a second
 *     warm. Kalshi's is 139 rate-limited series requests — ~34 seconds cold —
 *     so waiting for it server-side would make everybody pay for prices only
 *     some fixtures have. The board renders on Polymarket and the Kalshi
 *     prices land a beat later, merged in the browser by `venueMerge`.
 *
 *     A Kalshi outage therefore costs the Kalshi prices and nothing else.
 */

import { useEffect, useMemo, useState } from 'react'
import type { ScoutFixture } from '../lib/scoutTypes'
import type { KalshiFixture } from '../lib/kalshiSoccerTypes'
import { mergeKalshi } from '../lib/venueMerge'
import { rankRows, rowFromScout, rowFromSportGame, type BoardRow } from '../lib/boardRow'
import { SPORT_KEYS, type SportBoardData, type SportKey } from '../lib/sportsMeta'

/** Kalshi's index is rebuilt about four times an hour across the deployment;
 *  asking more often than its own price clock would only re-read it. */
const KALSHI_REFRESH_MS = 60_000
/** The US boards are cached server-side for a minute. */
const SPORTS_REFRESH_MS = 60_000

export interface SoccerRows {
  rows: BoardRow[]
  /** How many fixtures Kalshi could be placed on. */
  placed: number
  loading: boolean
  error: string | null
  /** Kalshi has not answered yet. */
  kalshiPending: boolean
}

export function useSoccerRows(): SoccerRows {
  const [fixtures, setFixtures] = useState<ScoutFixture[]>([])
  const [kalshi, setKalshi] = useState<KalshiFixture[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/scout')
      .then(async (r) => {
        const body = await r.json()
        if (!r.ok || !body.ok) throw new Error(body.error ?? `HTTP ${r.status}`)
        return body
      })
      .then((body) => {
        if (!cancelled) setFixtures(body.fixtures ?? [])
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not reach Polymarket')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetch('/api/venues/soccer')
        .then((r) => r.json())
        .then((body) => {
          // A failed Kalshi read leaves whatever we already had. An empty
          // column is a claim about Kalshi; a missing one is a claim about us.
          if (!cancelled && body?.ok) setKalshi(body.fixtures ?? [])
        })
        .catch(() => {
          /* the board is never waiting on this */
        })
    load()
    const t = setInterval(load, KALSHI_REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  const { rows, placed } = useMemo(() => {
    const merged = mergeKalshi(
      fixtures.map((f) => ({ ...f })),
      kalshi ?? []
    )
    // Ranked AFTER the merge, because the rank is combined volume.
    return { rows: rankRows(merged.fixtures.map(rowFromScout)), placed: merged.placed }
  }, [fixtures, kalshi])

  return { rows, placed, loading, error, kalshiPending: kalshi === null }
}

export interface SportRows {
  rows: BoardRow[]
  loading: boolean
  error: string | null
}

/** Every US sport at once, for the home page. Each board lands on its own and
 *  a sport that fails to load costs that sport, not the page. */
export function useAllSports(): Record<SportKey, SportRows> {
  const empty = (): Record<SportKey, SportRows> => {
    const out = {} as Record<SportKey, SportRows>
    for (const k of SPORT_KEYS) out[k] = { rows: [], loading: true, error: null }
    return out
  }
  const [state, setState] = useState<Record<SportKey, SportRows>>(empty)

  useEffect(() => {
    let cancelled = false
    const loadOne = (sport: SportKey) =>
      fetch(`/api/sports/${sport}`)
        .then(async (r) => {
          const b = await r.json()
          if (!r.ok || !b.ok) throw new Error(b.error ?? `HTTP ${r.status}`)
          return b as SportBoardData
        })
        .then((b) => {
          if (cancelled) return
          const rows = rankRows(b.games.map((g) => rowFromSportGame(g, sport)))
          setState((s) => ({ ...s, [sport]: { rows, loading: false, error: null } }))
        })
        .catch((e) => {
          if (cancelled) return
          // A failed refresh keeps the rows already on screen.
          setState((s) => ({
            ...s,
            [sport]: {
              rows: s[sport].rows,
              loading: false,
              error: e instanceof Error ? e.message : 'Could not load',
            },
          }))
        })
    const loadAll = () => SPORT_KEYS.forEach(loadOne)
    loadAll()
    const t = setInterval(loadAll, SPORTS_REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  return state
}
