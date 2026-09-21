'use client'

/** The soccer board at /soccer — the same page every US sport gets: the
 *  biggest games as cards, then every game in a table.
 *
 *  Loading (Polymarket first, Kalshi merged in a beat later) lives in
 *  `useSoccerRows`, shared with the home page.
 */

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { BoardView, HowPricesWork } from './BoardView'
import { IconDrop, IconInsights } from './icons'
import { SOCCER_COLUMNS } from '../lib/boardRow'
import { useSoccerRows } from './useBoards'

export function SoccerBoard() {
  const { rows, placed, loading, error, kalshiPending } = useSoccerRows()
  const [query, setQuery] = useState('')

  useEffect(() => {
    // The nav search sends a team here as ?q=. Read from the URL directly
    // rather than through useSearchParams, which would opt this statically
    // rendered page into a Suspense boundary for one string.
    try {
      const q = new URLSearchParams(window.location.search).get('q')
      if (q) setQuery(q)
    } catch {
      /* no query string is the normal case */
    }
  }, [])

  const head = (
    <div className="sc-head">
      <h1 className="sc-h1">Soccer odds</h1>
      <p className="sc-h1-sub">
        Every soccer game on Polymarket and Kalshi, biggest first. When one app pays more, its
        price is outlined with its logo. Tap any game for the full match page.
      </p>
    </div>
  )

  const foot = (
    <HowPricesWork>
      <p>
        The fine print. A price is the app&apos;s <b>ask</b>, per $1 of payout. Fees: Polymarket
        0.05 × p × (1 − p) per share, Kalshi 0.07 × p × (1 − p) per contract — the difference
        never flips a price that is a cent or more better, but it roughly halves what that cent is
        worth, and where the two print the same price near even money it makes Polymarket the
        cheaper one. Each price is judged on its own market, not the game&apos;s.{' '}
        {kalshiPending ? (
          <>Kalshi&apos;s prices are still loading.</>
        ) : (
          <>
            Kalshi lists <b className="np-num">{placed}</b> of these{' '}
            <b className="np-num">{rows.length}</b> games; a market we can&apos;t match to a game
            with both teams and the day agreeing is left out, never guessed.
          </>
        )}{' '}
        &ldquo;Traded&rdquo; adds Polymarket dollars to Kalshi $1 contracts — close enough to rank
        on.
      </p>
    </HowPricesWork>
  )

  const links = (
    <>
      <Link
        href="/dropping-odds"
        className="sc-cat is-link"
        title="Prices that moved most in the last 24 hours"
      >
        <IconDrop className="sc-cat-icn" />
        Dropping odds
      </Link>
      <Link
        href="/insights"
        className="sc-cat is-link"
        title="What our research found — including the results that went the wrong way"
      >
        <IconInsights className="sc-cat-icn" />
        Insights
      </Link>
    </>
  )

  return (
    <BoardView
      columns={SOCCER_COLUMNS}
      rows={rows}
      loading={loading}
      error={error}
      joiner="v"
      leagueFilter
      allLabel="All leagues"
      allTitle="All soccer games"
      head={head}
      foot={foot}
      emptyLabel="No games match that right now."
      pending={kalshiPending ? 'Loading Kalshi prices…' : null}
      links={links}
      initialQuery={query}
    />
  )
}
