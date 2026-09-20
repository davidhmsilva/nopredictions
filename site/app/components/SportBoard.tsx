'use client'

/** One US sport's board — the same table the football board uses.
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
import { BoardView } from './BoardView'
import { rankRows, rowFromSportGame, US_COLUMNS } from '../lib/boardRow'
import { timeText } from '../lib/display'
import { SPORT_META, type SportBoardData, type SportKey } from '../lib/sportsMeta'

/** A live board moves; a pre-match one barely does. The server caches for a
 *  minute, so asking more often than that would only re-read the same board. */
const REFRESH_MS = 60_000

export function SportBoard({ sport }: { sport: SportKey }) {
  const meta = SPORT_META[sport]
  const [data, setData] = useState<SportBoardData | null>(null)
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
    () => (data ? rankRows(data.games.map((g) => rowFromSportGame(g, meta.label))) : []),
    [data, meta.label]
  )

  const c = data?.counts

  const head = (
    <div className="sc-head">
      <p className="sc-eyebrow">NOPREDICTIONS · {meta.label} on the prediction markets</p>
      <h1 className="sc-h1">{meta.label}: the better price, on two exchanges</h1>
      <p className="sc-h1-sub">
        Every {meta.label} game either exchange lists in the next {meta.days} days, placed on
        ESPN&apos;s schedule. Both moneylines at the ask, the book behind each, and the cheaper
        venue marked — after each one&apos;s taker fee, not before it. No tips: the numbers, and
        you decide.
      </p>
    </div>
  )

  const foot = data && c && (
    <p className="sp-foot">
      Prices are each exchange&apos;s <b>ask</b> — what buying that side costs right now — and the
      board marks the cheaper of the two <b>after each venue&apos;s taker fee</b>: Polymarket
      0.05 × p × (1 − p) per share, Kalshi 0.07 × p × (1 − p) per contract, 40% more. A venue can
      only be marked cheaper where its book is real; Kalshi&apos;s placeholder books land outside
      that by construction. Placed on ESPN&apos;s schedule: Kalshi{' '}
      <b className="np-num">{c.kalshiPlaced}</b> of <b className="np-num">{c.kalshi}</b> games,
      Polymarket <b className="np-num">{c.polymarketPlaced}</b> of{' '}
      <b className="np-num">{c.polymarket}</b>. A market we cannot put on a game with both teams
      matched exactly is left out, never guessed. Volume adds Polymarket&apos;s dollars traded to
      Kalshi&apos;s $1 contracts — close enough to rank on, which is all it is used for. Updated{' '}
      {timeText(new Date(data.generatedAt))}.
    </p>
  )

  return (
    <BoardView
      columns={US_COLUMNS}
      rows={rows}
      loading={!data && !error}
      error={!data ? error : null}
      joiner="@"
      competitionChips={false}
      allLabel={`All ${meta.label}`}
      head={head}
      foot={foot || undefined}
      emptyLabel={`Neither Kalshi nor Polymarket lists any ${meta.label} game in the next ${meta.days} days.`}
    />
  )
}
