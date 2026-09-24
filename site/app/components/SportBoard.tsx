'use client'

/** One US sport's board — the same page the soccer board uses.
 *
 *  Every game either exchange lists, placed on ESPN's schedule, with both
 *  moneylines and the cheaper venue marked after each one's taker fee.
 *
 *  🔑 ESPN is the spine, not a venue: it is what both exchanges settle
 *     against, it knows which side is at home, and it carries the live score.
 *     A market that cannot be placed on an ESPN game with both teams matched
 *     is counted and left out, never guessed onto a game.
 *
 *  Away above home with an @, the way a US schedule prints a game.
 */

import { useEffect, useMemo, useState } from 'react'
import { BoardView, HowPricesWork } from './BoardView'
import { rankRows, rowFromSportGame, US_COLUMNS } from '../lib/boardRow'
import { timeText, useMounted } from '../lib/display'
import { SPORT_META, type SportBoardData, type SportKey } from '../lib/sportsMeta'

/** A live board moves; a pre-match one barely does. The server caches for a
 *  minute, so asking more often than that would only re-read the same board. */
const REFRESH_MS = 60_000

/** `initial` is the board as the server rendered it, so the page arrives with
 *  its games in the HTML — for search engines and for a first paint that does
 *  not wait on a request. The browser refreshes it from there. */
export function SportBoard({ sport, initial = null }: { sport: SportKey; initial?: SportBoardData | null }) {
  const meta = SPORT_META[sport]
  const mounted = useMounted()
  const [data, setData] = useState<SportBoardData | null>(initial)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetch(`/api/sports/${sport}`)
        .then(async (r) => {
          const b = await r.json()
          if (!r.ok || !b.ok) throw new Error(b.error ?? `HTTP ${r.status}`)
          return b as SportBoardData
        })
        .then((b) => {
          if (cancelled) return
          setData(b)
          setError(null)
        })
        .catch((e) => {
          // A failed refresh keeps the board already on screen.
          if (!cancelled) setError(e instanceof Error ? e.message : 'Could not reach the exchanges')
        })
    load()
    const t = setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [sport])

  const rows = useMemo(
    () => (data ? rankRows(data.games.map((g) => rowFromSportGame(g, sport))) : []),
    [data, sport]
  )

  const c = data?.counts

  const head = (
    <div className="sc-head">
      <h1 className="sc-h1">{meta.label} odds</h1>
      <p className="sc-h1-sub">
        Every {meta.label} game on Polymarket and Kalshi, side by side. When one app pays more,
        its price is outlined with its logo.
      </p>
    </div>
  )

  const foot = (
    <HowPricesWork>
      {data && c && (
        <p>
          The fine print. A price is the app&apos;s <b>ask</b>, per $1 of payout. Fees: Polymarket
          0.05 × p × (1 − p) per share, Kalshi 0.07 × p × (1 − p) per contract. Games come from
          ESPN&apos;s schedule for the next {meta.days} days; we matched Kalshi on{' '}
          <b className="np-num">{c.kalshiPlaced}</b> of <b className="np-num">{c.kalshi}</b> and
          Polymarket on <b className="np-num">{c.polymarketPlaced}</b> of{' '}
          <b className="np-num">{c.polymarket}</b>. A market we can&apos;t match to a game with
          both teams exactly is left out, never guessed. &ldquo;Traded&rdquo; adds Polymarket
          dollars to Kalshi $1 contracts — close enough to rank on. Updated{' '}
          {mounted ? timeText(new Date(data.generatedAt)) : ''}.
        </p>
      )}
    </HowPricesWork>
  )

  return (
    <BoardView
      columns={US_COLUMNS}
      rows={rows}
      loading={!data && !error}
      error={!data ? error : null}
      leagueFilter={false}
      allLabel={`All ${meta.label}`}
      allTitle={`All ${meta.label} games`}
      head={head}
      foot={foot}
      emptyLabel={`No ${meta.label} games on Polymarket or Kalshi in the next ${meta.days} days.`}
    />
  )
}
