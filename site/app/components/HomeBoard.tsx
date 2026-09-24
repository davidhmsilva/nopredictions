'use client'

/** Home: the intro, the games leading the card, the top of the board, a way
 *  to all of it, and what else the site does.
 *
 *  The board is every sport at once — soccer and the six US sports, ranked
 *  together on money traded across both apps — so the cards and the top ten
 *  are the biggest games of the day, not the biggest soccer games.
 *
 *  The list waits until the boards have answered (or a few seconds, whichever
 *  is first) before it ranks anything: ranking on whichever sport answered
 *  first and then reshuffling as the rest land reads as a broken page.
 */

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { BoardView } from './BoardView'
import { IconAgent, IconLab, IconWallet } from './icons'
import { useAllSports, useSoccerRows } from './useBoards'
import { rankRows, SOCCER_COLUMNS, type BoardRow } from '../lib/boardRow'
import { SPORT_KEYS } from '../lib/sportsMeta'
import { useSession } from '../lib/useSession'

/** How many games the home page lists before "See all games". */
const HOME_ROWS = 10
/** How long the list waits for a slow board before ranking what it has. */
const WAIT_MS = 5000

/** What the site does besides the board, shown under the home page's list. */
const MORE = [
  {
    href: '/lab',
    Icon: IconLab,
    title: 'Test a strategy',
    body: 'Write a theory in plain English and replay it over 111,475 real games against the closing price.',
    cta: 'Open the Lab',
  },
  {
    href: '/agent',
    Icon: IconAgent,
    title: 'Build an agent',
    body: 'Save a theory as an agent and it paper-trades the next games that fit. You watch the record build.',
    cta: 'See your agents',
  },
  {
    href: '/wallet',
    Icon: IconWallet,
    title: 'Read a trader',
    body: 'Paste any Polymarket wallet: every fill rebuilt into trades, and where the profit really comes from.',
    cta: 'Open the Wallet',
  },
]

/** `initial` is the top of the board as the server ranked it: the home page
 *  arrives with its games in the HTML, and the browser swaps in the full,
 *  live list once every board has answered. */
export function HomeBoard({ initial = null }: { initial?: BoardRow[] | null }) {
  const soccer = useSoccerRows()
  const sports = useAllSports()
  const { me } = useSession()
  const [waited, setWaited] = useState(false)
  const [all, setAll] = useState(false)

  const [query, setQuery] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setWaited(true), WAIT_MS)
    return () => clearTimeout(t)
  }, [])

  useEffect(() => {
    // The nav search sends a team, or a pasted link, here as ?q=. Read from the
    // URL directly rather than through useSearchParams, which would opt this
    // statically rendered page into a Suspense boundary for one string.
    const read = () => {
      try {
        setQuery(new URLSearchParams(window.location.search).get('q') ?? '')
      } catch {
        /* no query string is the normal case */
      }
    }
    const fromNav = (e: Event) => setQuery(String((e as CustomEvent).detail ?? ''))
    read()
    window.addEventListener('popstate', read)
    window.addEventListener('np-search', fromNav)
    return () => {
      window.removeEventListener('popstate', read)
      window.removeEventListener('np-search', fromNav)
    }
  }, [])

  const settled = !soccer.loading && SPORT_KEYS.every((k) => !sports[k].loading)
  const ready = settled || waited

  const live = useMemo(
    () => rankRows([soccer.rows, ...SPORT_KEYS.map((k) => sports[k].rows)].flat()),
    [soccer.rows, sports]
  )
  const rows = ready ? live : (initial ?? [])

  const head = (
    <div className="sc-head">
      <p className="sc-eyebrow">NOPREDICTIONS · Prediction-market research</p>
      <h1 className="sc-h1">See if the price is wrong — before you trade it</h1>
      <p className="sc-h1-sub">
        Polymarket and Kalshi side by side, with the cheaper exchange marked on every price. No
        tips — the numbers, and you decide.
      </p>
    </div>
  )

  const foot = (
    <>
      {ready && !all && rows.length > HOME_ROWS && (
        <div className="hm-all">
          <button className="np-btn hm-all-btn" onClick={() => setAll(true)}>
            See all {rows.length} games <span aria-hidden="true">→</span>
          </button>
        </div>
      )}

      <section className="hm-more" aria-labelledby="hm-more-h">
        <h2 id="hm-more-h" className="hm-more-h">More than a board</h2>
        <div className="hm-more-grid">
          {MORE.map((m) => (
            <Link key={m.href} href={m.href} className="hm-more-card">
              <m.Icon className="hm-more-icn" />
              <b>{m.title}</b>
              <span>{m.body}</span>
              <em>
                {m.cta} <span aria-hidden="true">→</span>
              </em>
            </Link>
          ))}
        </div>
        {/* Only once the session is known to be empty — rendering it while
            it loads would flash an invitation at people already signed in. */}
        {me && !me.user && (
          <div className="hm-join">
            <div className="hm-join-text">
              <b>Free to start. No card.</b>
              <span>
                An account gets you 3 Lab tests and 3 wallet reads a day, up to 5 agents of your
                own, and a watchlist.
              </span>
            </div>
            <Link href="/login?mode=signup&next=%2F" className="np-btn np-btn-primary">
              Create a free account <span aria-hidden="true">→</span>
            </Link>
          </div>
        )}
      </section>
    </>
  )

  return (
    <BoardView
      columns={SOCCER_COLUMNS}
      rows={rows}
      loading={!ready && rows.length === 0}
      error={null}
      leagueFilter
      allLabel="All sports"
      head={head}
      foot={foot}
      emptyLabel="No game matches that filter right now."
      pending={soccer.kalshiPending ? 'Kalshi loading…' : null}
      initialQuery={query}
      // A search shows every match, not the top ten of them.
      limit={all || query ? undefined : HOME_ROWS}
    />
  )
}
