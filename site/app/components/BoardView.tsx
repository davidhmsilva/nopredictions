'use client'

/** The board. One page layout for soccer and for every US sport.
 *
 *    the page's heading
 *    the biggest games, as cards        ← what someone opening the page is after
 *    every game, as a table             ← filters, sort and search live here
 *    how the prices are read            ← folded away, one tap to open
 *
 *  🔑 Both exchanges on every row, and the CHEAPER ONE MARKED with its logo.
 *     The mark is made net of each venue's taker fee — Polymarket 0.05·p·(1−p),
 *     Kalshi 0.07·p·(1−p), 40% more — which does not change who wins a price
 *     by a cent or more, but roughly halves what the win is worth and decides
 *     a tie near even money. lib/venues carries the search that settled that.
 *
 *  🔑 It ranks on COMBINED volume. A game with millions through it has a real
 *     two-sided book by construction, which is the honest proxy for "hot".
 *
 *  ⚠️ A venue can only win a price if it has a real book. A lone sell order
 *     at 0.99 behind an empty bid side is the cheapest quote on the card by
 *     arithmetic and is not a market — `venues.gradeOf` is the gate.
 *
 *  The look is deliberately colourless. Book grades used to colour the venue
 *  names green or amber, and the winning price green; to a reader who does
 *  not know what a book grade is that was noise. The two app logos are the
 *  only colour on a board, and live games keep their red dot.
 */

import { Fragment, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AppShell } from './AppShell'
import { OddsToggle } from './OddsToggle'
import { SportBar } from './SportBar'
import { GameCard } from './GameCard'
import { VenueLogo } from './VenueLogo'
import { LiveState, money, odds, pickTitle } from './boardParts'
import { IconAll, IconClock, IconDrop, IconInsights, IconLive, IconStar, IconVenues } from './icons'
import { formatName, useOddsFormat, zoneLabel, type OddsFormat } from '../lib/display'
import { hasPrice, volumeByVenue, type BoardColumn, type BoardRow } from '../lib/boardRow'
import { VENUE_NAME, type Venue } from '../lib/venues'
import { useSession } from '../lib/useSession'
import { useWatchlist } from '../lib/useWatchlist'

// ── one price, across both exchanges ─────────────────────────────────────────

function PriceCell({ row, col, f }: { row: BoardRow; col: BoardColumn; f: OddsFormat }) {
  const pick = row.best[col.key]
  const cls = `sc-c-odd np-num${col.divider ? ' sc-c-o25' : ''}`
  if (!pick || pick.ask == null) {
    return <td className={`${cls} is-empty`}>—</td>
  }
  return (
    <td
      className={`${cls}${pick.venue ? ' is-best' : ''}`}
      title={pickTitle(pick, row.venues, col.key, f)}
    >
      <span className="sc-odd">
        {pick.venue && <VenueLogo venue={pick.venue} size={13} title="" />}
        {odds(pick.ask, f)}
      </span>
    </td>
  )
}

/** Which apps list this game — each logo opens the game there. */
function VenueLinks({ r }: { r: BoardRow }) {
  const vol = volumeByVenue(r)
  return (
    <span className="sc-venues">
      {(['polymarket', 'kalshi'] as Venue[]).map((v) => {
        const b = r.venues.find((x) => x.venue === v)
        if (!b) return null
        return (
          <a
            key={v}
            className="sc-venue"
            href={b.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            title={`Open on ${VENUE_NAME[v]}${vol[v] != null ? ` · ${money(vol[v])} traded there` : ''}`}
          >
            <VenueLogo venue={v} size={14} title="" />
          </a>
        )
      })}
    </span>
  )
}

// ── one row ──────────────────────────────────────────────────────────────────

function Row({
  r,
  columns,
  showCompetition,
  reports,
  watched,
  onToggleWatch,
}: {
  r: BoardRow
  columns: BoardColumn[]
  showCompetition: boolean
  /** Whether the board has a page of its own to send anyone to. Soccer does;
   *  the US sports do not yet, and a column of em-dashes is worse than none. */
  reports: boolean
  watched: boolean
  onToggleWatch: (key: string) => void
}) {
  const router = useRouter()
  const f = useOddsFormat()
  const open = () => {
    if (r.href) router.push(r.href)
  }
  return (
    <tr
      className={`${r.href ? 'is-clickable' : ''}${r.live ? ' is-live' : r.finished ? ' is-done' : ''}`}
      onClick={open}
    >
      <td className="sc-c-star">
        <button
          className={`sc-star${watched ? ' is-on' : ''}`}
          onClick={(e) => {
            // Starring is not opening.
            e.stopPropagation()
            onToggleWatch(r.key)
          }}
          aria-label={watched ? 'Remove from watchlist' : 'Add to watchlist'}
        >
          {watched ? '★' : '☆'}
        </button>
      </td>

      <td className="sc-c-fixture">
        <FixtureCell r={r} showCompetition={showCompetition} />
      </td>

      <td className="sc-c-state">
        <LiveState r={r} />
      </td>

      {columns.map((c) => (
        <PriceCell key={c.key} row={r} col={c} f={f} />
      ))}

      <td className="sc-c-vol np-num" title="Traded on Polymarket and Kalshi together">
        {money(r.volume)}
      </td>
      {reports && (
        <td className="sc-c-go">
          {r.href && (
            <Link href={r.href} className="sc-go" onClick={(e) => e.stopPropagation()}>
              <span className="sc-go-text">Details</span> <span aria-hidden="true">→</span>
            </Link>
          )}
        </td>
      )}
    </tr>
  )
}

function FixtureCell({ r, showCompetition }: { r: BoardRow; showCompetition: boolean }) {
  // "v" between a soccer home and away, "@" before a US home side — per row,
  // because the home page mixes every sport in one list.
  const joiner = r.sport === 'soccer' ? 'v' : '@'
  const inner = (
    <span className="sc-teams">
      {r.left} <span className="sc-v">{joiner}</span> {r.right}
      {r.score && (
        <b className="np-num sc-row-score">
          {r.score.left}–{r.score.right}
        </b>
      )}
    </span>
  )
  return (
    <>
      {r.href ? (
        <Link href={r.href} className="sc-fixture">
          {inner}
        </Link>
      ) : (
        <span className="sc-fixture">{inner}</span>
      )}
      <span className="sc-meta">
        {showCompetition && r.competition && <span className="sc-comp">{r.competition}</span>}
        {r.markets != null && <span className="sc-mkts np-num">{r.markets} markets</span>}
        <VenueLinks r={r} />
      </span>
    </>
  )
}

// ── filters and sorting ──────────────────────────────────────────────────────

type Filter = 'all' | 'live' | 'soon' | 'both' | 'watchlist'

const FILTERS: {
  id: Filter
  label: string
  Icon: (p: { className?: string }) => JSX.Element
  cls?: string
  title?: string
}[] = [
  { id: 'all', label: 'All', Icon: IconAll },
  { id: 'live', label: 'Live', Icon: IconLive, cls: 'is-live-icn' },
  { id: 'soon', label: 'Starting soon', Icon: IconClock },
  {
    id: 'both',
    label: 'On both apps',
    Icon: IconVenues,
    title: 'Games Polymarket and Kalshi both offer — where there is a better price to find',
  },
  { id: 'watchlist', label: 'Watchlist', Icon: IconStar },
]

export type SortKey = 'default' | 'kickoff' | 'saving'

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'default', label: 'Most popular' },
  { id: 'kickoff', label: 'Start time' },
  { id: 'saving', label: 'Biggest price gap' },
]

/** The best saving on offer anywhere on this row, in probability points. What
 *  "biggest price gap" sorts on; zero unless both venues quote the same
 *  outcome with real books. */
export function topSaving(r: BoardRow): number {
  let best = 0
  for (const pick of Object.values(r.best)) {
    if (pick?.savingPp != null && pick.savingPp > best) best = pick.savingPp
  }
  return best
}

function sortRows(rows: BoardRow[], key: SortKey): BoardRow[] {
  if (key === 'default') return rows
  const out = rows.slice()
  switch (key) {
    case 'saving':
      return out.sort((a, b) => topSaving(b) - topSaving(a))
    case 'kickoff':
      return out.sort(
        (a, b) => new Date(a.kickoff ?? 0).getTime() - new Date(b.kickoff ?? 0).getTime()
      )
  }
}

/** "Starting soon" is the next two hours. */
const SOON_MS = 2 * 3600_000

/** How many competitions get their own chip before the rest go behind "More".
 *  A Saturday soccer card runs to nearly 60 of them. */
const COMPS_SHOWN = 9

// ── the page ─────────────────────────────────────────────────────────────────

export interface BoardViewProps {
  columns: BoardColumn[]
  rows: BoardRow[]
  loading: boolean
  error: string | null
  /** A league picker, and the league named on each row and card. Off where
   *  every row is the same competition — one option narrows nothing. */
  leagueFilter: boolean
  /** The picker's "everything" option: "All leagues". */
  allLabel: string
  /** The heading over the table: "All NFL games". None on the home page. */
  allTitle?: string
  /** Rendered above the cards: the page's heading. */
  head: React.ReactNode
  /** Rendered under the table. */
  foot?: React.ReactNode
  emptyLabel: string
  /** Kalshi is still arriving. The prices are there, just not compared yet. */
  pending?: string | null
  /** Seeded from ?q= by the nav search. */
  initialQuery?: string
  /** How many of the biggest games lead the page as cards. */
  featured?: number
  /** Show only the first N rows. The home page leads with the top ten; the
   *  count still names the whole list, because "Top 10 of 97" and "10 of 97"
   *  are different claims. */
  limit?: number
}

export function BoardView({
  columns,
  rows,
  loading,
  error,
  leagueFilter,
  allLabel,
  allTitle,
  head,
  foot,
  emptyLabel,
  pending,
  initialQuery = '',
  featured = 4,
  limit,
}: BoardViewProps) {
  const [filter, setFilter] = useState<Filter>('all')
  const [sort, setSort] = useState<SortKey>('default')
  const [query, setQuery] = useState(initialQuery)
  const [comp, setComp] = useState<string | null>(null)
  const [allComps, setAllComps] = useState(false)
  const { me } = useSession()
  const oddsFmt = useOddsFormat()
  // Named once, in the column head. Computed on the client: the server runs in
  // UTC and has no reader to ask.
  const zone = useMemo(() => zoneLabel(), [])
  const { slugs: watchlist, toggle: toggleWatch, synced } = useWatchlist(me?.plan === 'pro')

  // The nav search lands on the board with ?q=; the page reads it after mount,
  // so it arrives as a prop change rather than as an initial value.
  useEffect(() => {
    if (initialQuery) setQuery(initialQuery)
  }, [initialQuery])

  const bothCount = useMemo(() => rows.filter((r) => r.venues.length > 1).length, [rows])
  const reports = useMemo(() => rows.some((r) => r.href != null), [rows])
  const top = useMemo(
    () => rows.filter((r) => !r.finished && hasPrice(r)).slice(0, featured),
    [rows, featured]
  )

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = rows.filter((r) => {
      if (q && !`${r.left} ${r.right} ${r.competition ?? ''}`.toLowerCase().includes(q)) return false
      if (comp && r.competition !== comp) return false
      switch (filter) {
        case 'live':
          return r.live
        case 'soon': {
          if (r.live || r.finished || !r.kickoff) return false
          const dt = new Date(r.kickoff).getTime() - Date.now()
          return dt > 0 && dt <= SOON_MS
        }
        case 'both':
          return r.venues.length > 1
        case 'watchlist':
          return watchlist.includes(r.key)
        default:
          return true
      }
    })
    return sortRows(filtered, sort)
  }, [rows, filter, sort, query, comp, watchlist])

  /** Ordered by how much of today's card each competition is. */
  const competitions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const r of rows) {
      if (!r.competition) continue
      counts.set(r.competition, (counts.get(r.competition) ?? 0) + 1)
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([name, n]) => ({ name, n }))
  }, [rows])

  const listed = limit != null ? shown.slice(0, limit) : shown

  return (
    <AppShell>
      <SportBar />
      {/* The menu under the sports: filters, Dropping odds and Insights, then
          the leagues. Full width and scrollable, so a phone reaches all of it. */}
      <div className="sc-cats">
        <div className="sc-cats-inner">
          <div className="sc-cat-group">
            {FILTERS.map((x) => (
              <Fragment key={x.id}>
                <button
                  className={`sc-cat${filter === x.id ? ' is-on' : ''}`}
                  onClick={() => setFilter(x.id)}
                  title={x.title}
                >
                  <x.Icon className={`sc-cat-icn ${x.cls ?? ''}`} />
                  {x.label}
                  {x.id === 'watchlist' && watchlist.length > 0 && (
                    <span className="sc-cat-n np-num">{watchlist.length}</span>
                  )}
                  {x.id === 'both' && bothCount > 0 && (
                    <span className="sc-cat-n np-num">{bothCount}</span>
                  )}
                </button>
                {x.id === 'both' && <MenuLinks />}
              </Fragment>
            ))}
          </div>

          {leagueFilter && competitions.length > 1 && (
            <>
              <span className="sc-cat-div" aria-hidden="true" />
              <div className="sc-cat-group">
                <button
                  className={`sc-cat${comp === null ? ' is-on' : ''}`}
                  onClick={() => setComp(null)}
                >
                  {allLabel}
                </button>
                {(allComps ? competitions : competitions.slice(0, COMPS_SHOWN)).map((c) => (
                  <button
                    key={c.name}
                    className={`sc-cat${comp === c.name ? ' is-on' : ''}`}
                    onClick={() => setComp(comp === c.name ? null : c.name)}
                  >
                    {c.name}
                    <span className="sc-cat-n np-num">{c.n}</span>
                  </button>
                ))}
                {competitions.length > COMPS_SHOWN && (
                  <button className="sc-cat sc-cat-more" onClick={() => setAllComps(!allComps)}>
                    {allComps ? 'Less' : `More (${competitions.length - COMPS_SHOWN})`}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="np-wrap">
        {head}

        {!error && featured > 0 && top.length > 0 && (
          <div className="gm-grid gm-grid-4 gm-grid-feature">
            {top.map((r) => (
              <GameCard key={`${r.sport}:${r.key}`} r={r} showLeague={leagueFilter} />
            ))}
          </div>
        )}

        <section className={allTitle ? 'bd-all' : 'bd-all is-bare'}>
          {allTitle && (
            <h2 className="bd-h2">
              {allTitle}
              {!loading && <span className="bd-h2-n np-num">{rows.length}</span>}
            </h2>
          )}

          <div className="sc-bar">
            <div className="sc-bar-left">
              {(comp || query || filter !== 'all') && (
                <button
                  className="sc-clear"
                  onClick={() => {
                    setFilter('all')
                    setComp(null)
                    setQuery('')
                  }}
                >
                  Clear filters
                </button>
              )}
              <span className="sc-count np-num">
                {loading
                  ? ''
                  : limit != null && shown.length > limit
                    ? `Top ${limit} of ${shown.length}`
                    : `${shown.length} of ${rows.length}`}
              </span>
              {pending && <span className="sc-pending">{pending}</span>}
            </div>

            <div className="sc-bar-right">
              {/* Below 1100px the nav has no room for it, so it lives here. */}
              <OddsToggle className="np-odds-bar" />
              <label className="sc-sort">
                <span className="sc-sort-key">Sort</span>
                <select
                  value={sort}
                  onChange={(e) => setSort(e.target.value as SortKey)}
                  aria-label="Sort the games"
                >
                  {SORTS.map((so) => (
                    <option key={so.id} value={so.id}>
                      {so.label}
                    </option>
                  ))}
                </select>
              </label>
              <input
                className="sc-search"
                placeholder="Search teams…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search the games"
              />
            </div>
          </div>

          {loading && <div className="np-empty">Loading the latest odds…</div>}

          {error && (
            <div className="np-note sc-error">
              <strong>We couldn&apos;t load the odds just now.</strong> Try again in a minute.{' '}
              <span className="sc-error-detail">({error})</span>
            </div>
          )}

          {!loading && !error && shown.length === 0 && (
            <div className="np-empty">
              {filter === 'watchlist'
                ? synced
                  ? 'Nothing saved yet. Tap the ☆ on any game — your list follows you to any device you sign in on.'
                  : 'Nothing saved yet. Tap the ☆ on any game to keep it here.'
                : filter === 'both'
                  ? 'No game here is on both apps right now.'
                  : emptyLabel}
            </div>
          )}

          {listed.length > 0 && (
            <div className="sc-table-wrap">
              <table className="sc-table">
                <thead>
                  <tr>
                    <th className="sc-c-star" />
                    <th className="sc-c-fixture">Game</th>
                    <th className="sc-c-state" title={`Start time, in your time zone (${zone})`}>
                      Starts{zone && <span className="sc-th-zone">{zone}</span>}
                    </th>
                    {columns.map((c) => (
                      <th
                        key={c.key}
                        className={`sc-c-odd${c.divider ? ' sc-c-o25' : ''}`}
                        title={`${c.title} · ${formatName(oddsFmt)}`}
                      >
                        {c.label}
                      </th>
                    ))}
                    <th className="sc-c-vol" title="Traded on Polymarket and Kalshi together">
                      Traded
                    </th>
                    {reports && <th className="sc-c-go" />}
                  </tr>
                </thead>
                <tbody>
                  {listed.map((r) => (
                    <Row
                      key={`${r.sport}:${r.key}`}
                      r={r}
                      columns={columns}
                      showCompetition={leagueFilter}
                      reports={reports}
                      watched={watchlist.includes(r.key)}
                      onToggleWatch={toggleWatch}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {foot}
      </div>
    </AppShell>
  )
}

/** Dropping odds and Insights, in the menu of every board. */
function MenuLinks() {
  return (
    <>
      <Link
        href="/dropping-odds"
        className="sc-cat is-link"
        title="Where the market moved in the last 24 hours — pre-match, and only on books with real money through them"
      >
        <IconDrop className="sc-cat-icn" />
        Dropping odds
        <span className="sc-cat-go" aria-hidden="true">→</span>
      </Link>
      <Link
        href="/insights"
        className="sc-cat is-link"
        title="What we measured, and what it said — including the results that went the wrong way"
      >
        <IconInsights className="sc-cat-icn" />
        Insights
        <span className="sc-cat-go" aria-hidden="true">→</span>
      </Link>
    </>
  )
}

/** The fold under every board: how a price is read, in plain words first and
 *  the arithmetic after. Closed by default — it is there for whoever asks. */
export function HowPricesWork({ children }: { children?: React.ReactNode }) {
  return (
    <details className="bd-how">
      <summary>How we pick the better price</summary>
      <div className="bd-how-body">
        <ol className="bd-how-steps">
          <li>
            <b>We read both apps live.</b> Each price is what buying that side costs on Polymarket
            and on Kalshi right now.
          </li>
          <li>
            <b>We take out the fees.</b> Each app charges a small fee on every trade — Kalshi&apos;s
            is higher — so we compare what you actually pay, not the headline number.
          </li>
          <li>
            <b>We mark the one that pays more</b> with its logo and an outline. Within half a cent
            it&apos;s a tie, and a price with nobody on the other side of it never counts.
          </li>
        </ol>
        {children && <div className="bd-how-fine">{children}</div>}
      </div>
    </details>
  )
}
