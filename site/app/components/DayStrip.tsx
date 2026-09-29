'use client'

// Yesterday · Today · Tomorrow — how Sofascore is navigated, and what the
// soccer board lacked: it only ever looked forward. Yesterday is its own page
// (/results/<day>, every result against the price it kicked off at); today and
// tomorrow filter the board. Days are the reader's own (lib/localDay).

import Link from 'next/link'
import { useMounted } from '../lib/display'
import { yesterdayLocal } from '../lib/localDay'

export type BoardDay = 'all' | 'today' | 'tomorrow'

export function DayStrip({
  active,
  onPick,
}: {
  /** 'yesterday' on the results page, otherwise the board's filter. Null on
   *  a results page for another day. */
  active: BoardDay | 'yesterday' | null
  /** On the board, Today / Tomorrow / Next 48h filter in place. Without it
   *  (the results page) they are links back to the board. */
  onPick?: (d: BoardDay) => void
}) {
  const mounted = useMounted()
  // Before hydration there is no reader's zone; /results picks a day itself.
  const yesterdayHref = mounted ? `/results/${yesterdayLocal()}` : '/results'

  const item = (d: BoardDay, label: string) =>
    onPick ? (
      <button
        key={d}
        className={`dy-chip${active === d ? ' is-on' : ''}`}
        onClick={() => onPick(d)}
        aria-pressed={active === d}
      >
        {label}
      </button>
    ) : (
      <Link key={d} className={`dy-chip${active === d ? ' is-on' : ''}`} href={d === 'all' ? '/soccer' : `/soccer?day=${d}`}>
        {label}
      </Link>
    )

  return (
    <nav className="dy-strip" aria-label="Day">
      <Link className={`dy-chip${active === 'yesterday' ? ' is-on' : ''}`} href={yesterdayHref}>
        Yesterday
      </Link>
      {item('today', 'Today')}
      {item('tomorrow', 'Tomorrow')}
      {item('all', 'Next 48h')}
    </nav>
  )
}
