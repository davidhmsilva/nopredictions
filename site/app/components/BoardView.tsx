'use client'

/** The board. One table for football and for every US sport.
 *
 *  🔑 Both exchanges on every row, and the CHEAPER ONE MARKED. That is the
 *     reason the page exists: a price is only a price if you know what the
 *     alternative was. The mark is made net of each venue's taker fee —
 *     Polymarket 0.05·p·(1−p), Kalshi 0.07·p·(1−p), 40% more — which does not
 *     change who wins a price by a cent or more, but roughly halves what the
 *     win is worth and decides a tie near even money. lib/venues carries the
 *     search that settled that.
 *
 *  🔑 It ranks on COMBINED volume. A fixture with millions through it has a
 *     real two-sided book by construction, which is the honest proxy for
 *     "hot", and summing the venues means a game Kalshi carries and
 *     Polymarket barely does still finds its place.
 *
 *  ⚠️ A venue can only win a price if it has a real book. A lone sell order
 *     at 0.99 behind an empty bid side is the cheapest quote on the card by
 *     arithmetic and is not a market — `venues.gradeOf` is the gate.
 */

import { Fragment, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AppShell } from './AppShell'
import { OddsToggle } from './OddsToggle'
import { SportBar } from './SportBar'
import { IconAll, IconClock, IconLive, IconStar, IconVenues } from './icons'
import {
  dayTimeText,
  formatName,
  priceText,
  timeText,
  useOddsFormat,
  zoneLabel,
  type OddsFormat,
} from '../lib/display'
import { volumeByVenue, type BoardColumn, type BoardRow } from '../lib/boardRow'
import {
  GRADE_LABEL,
  VENUE_NAME,
  gradeOf,
  netCost,
  type BestPick,
  type Venue,
  type VenueBook,
} from '../lib/venues'
import { useSession } from '../lib/useSession'
import { useWatchlist } from '../lib/useWatchlist'

// ── writing numbers ──────────────────────────────────────────────────────────

export function odds(p: number | null | undefined, f: OddsFormat): string {
  if (p == null || p <= 0.01 || p >= 0.99) return '—'
  return priceText(p, f)
}

export function money(v: number | null | undefined): string {
  if (v == null || v <= 0) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

const cents = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}¢`)

/** Kick-off in the reader's own zone. The zone is named once, in the column
 *  head, rather than on every row. */
export function clock(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const mins = Math.round((d.getTime() - Date.now()) / 60000)
  if (mins < 0) return timeText(d)
  if (mins < 60) return `${mins}m`
  if (mins < 24 * 60) return `${Math.floor(mins / 60)}h ${mins % 60}m`
  return dayTimeText(d)
}

// ── what the clock is doing, and how confidently we know it ──────────────────

export function LiveState({ r }: { r: BoardRow }) {
  if (r.finished) return <span className="np-badge">{r.detail || 'FT'}</span>

  if (r.live) {
    // ESPN's own status string, where we have one, beats anything we could
    // assemble: "Q4 0:06" and "Top 7th" are what the sport calls the moment.
    if (r.detail && r.minute == null) {
      return (
        <span className="np-badge is-live" title="ESPN's live status for this game.">
          ● {r.detail}
        </span>
      )
    }
    if (r.liveSource === 'pm' || r.liveSource === 'feed') {
      return (
        <span
          className="np-badge is-live"
          title={
            r.liveSource === 'pm'
              ? r.phase === 'HT'
                ? "Half time, per Polymarket's own live data. There is no minute at the break."
                : "Polymarket's own live data for this event — the same clock its page shows."
              : 'Live feed. Polymarket has not tagged this fixture as live.'
          }
        >
          {r.phase === 'HT' ? (
            'HT'
          ) : (
            <>
              ● {r.minute != null ? <span className="np-num">{r.minute}&apos;</span> : 'LIVE'}
              {r.phase === 'ET' || r.phase === 'PEN' ? ` ${r.phase}` : ''}
            </>
          )}
        </span>
      )
    }
    if (r.liveSource === 'board') {
      return (
        <span
          className="np-badge is-live"
          title="A market on this board has resolved, so the match has certainly started. No live clock for this competition."
        >
          ● LIVE
        </span>
      )
    }
    return (
      <span
        className="np-badge is-warn"
        title={
          'The listed kick-off has passed, but no live feed covers this fixture and ' +
          "nothing on its board has resolved. Polymarket's listed start has run ~30 min " +
          'early on smaller leagues and eight hours late elsewhere, so this is probable ' +
          'rather than confirmed.'
        }
      >
        KICKED OFF?
      </span>
    )
  }
  return <span className="sc-in np-num">{clock(r.kickoff)}</span>
}

// ── one price, across both exchanges ─────────────────────────────────────────

function pickTitle(pick: BestPick, venues: VenueBook[], key: BoardColumn['key'], f: OddsFormat): string {
  const lines = venues.map((b) => {
    const q = b.quotes[key]
    if (!q || q.ask == null) return `${VENUE_NAME[b.venue]}: not quoted`
    const net = netCost(q.ask, b.venue)
    // ⚠️ Graded on THIS leg, the same gate `bestFor` uses. Reading the
    //    fixture's 1X2 grade here instead made the tooltip contradict the
    //    highlight above it — "no real book" beside "cheaper by 5.4pp".
    const grade = gradeOf([q])
    return (
      `${VENUE_NAME[b.venue]} ${priceText(q.ask, f)} — ask ${cents(q.ask)}, ` +
      `${cents(net)} with its taker fee` +
      (grade === 'none'
        ? ' (one-sided or over 10¢ wide, so it cannot win this price)'
        : grade === 'wide'
          ? ` (${Math.round((q.spread ?? 0) * 100)}¢ wide)`
          : '')
    )
  })
  if (pick.venue && pick.savingPp != null) {
    lines.push(
      `Cheaper at ${VENUE_NAME[pick.venue]} by ${pick.savingPp.toFixed(1)}pp after each venue's fee.`
    )
  } else if (pick.quoted === 1) {
    lines.push('Only one exchange quotes this, so there is nothing to be better than.')
  } else if (pick.quoted > 1) {
    lines.push('Level: the two are inside half a cent of each other after fees.')
  }
  return lines.join('\n')
}

function PriceCell({
  row,
  col,
  f,
}: {
  row: BoardRow
  col: BoardColumn
  f: OddsFormat
}) {
  const pick = row.best[col.key]
  const cls = `sc-c-odd np-num${col.divider ? ' sc-c-o25' : ''}`
  if (!pick || pick.ask == null) {
    return <td className={cls}>—</td>
  }
  const won = pick.venue != null
  return (
    <td
      className={`${cls}${won ? ' is-best' : ''}`}
      title={pickTitle(pick, row.venues, col.key, f)}
    >
      {odds(pick.ask, f)}
      {won && <span className="sc-best-v" aria-hidden="true">{pick.venue === 'kalshi' ? 'K' : 'P'}</span>}
    </td>
  )
}

/** Which exchanges list this row, and what their books look like. */
function VenueTags({ r }: { r: BoardRow }) {
  const vol = volumeByVenue(r)
  return (
    <span className="sc-venues">
      {(['polymarket', 'kalshi'] as Venue[]).map((v) => {
        const b = r.venues.find((x) => x.venue === v)
        if (!b) {
          return (
            <span key={v} className="sc-venue is-missing" title={`${VENUE_NAME[v]} does not list this game.`}>
              {VENUE_NAME[v]} —
            </span>
          )
        }
        const money_ = money(vol[v])
        return (
          <a
            key={v}
            className={`sc-venue is-${b.grade}`}
            href={b.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            title={
              `${VENUE_NAME[v]}: ${GRADE_LABEL[b.grade]} on the match-result ladder` +
              (vol[v] != null
                ? `, ${money_} traded (${v === 'kalshi' ? '$1 contracts' : 'dollars'})`
                : '') +
              '. Opens on the exchange.'
            }
          >
            {VENUE_NAME[v]}
            <em>{money_}</em>
          </a>
        )
      })}
    </span>
  )
}

// ── one row ──────────────────────────────────────────────────────────────────

function Row({
  r,
  n,
  columns,
  joiner,
  reports,
  watched,
  onToggleWatch,
}: {
  r: BoardRow
  n: number
  columns: BoardColumn[]
  joiner: string
  /** Whether the board has a page of its own to send anyone to. Football does;
   *  the US sports do not yet, and a column of em-dashes is worse than no
   *  column at all. */
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
      <td className="sc-c-n np-num">{n}</td>
      <td className="sc-c-star">
        <button
          className={`sc-star${watched ? ' is-on' : ''}`}
          onClick={(e) => {
            // Starring is not opening. Without this the row handler fires too
            // and the click both stars the game and leaves the page.
            e.stopPropagation()
            onToggleWatch(r.key)
          }}
          aria-label={watched ? 'Remove from watchlist' : 'Add to watchlist'}
        >
          {watched ? '★' : '☆'}
        </button>
      </td>

      <td className="sc-c-fixture">
        <FixtureCell r={r} joiner={joiner} />
      </td>

      <td className="sc-c-state">
        <LiveState r={r} />
      </td>

      {columns.map((c) => (
        <PriceCell key={c.key} row={r} col={c} f={f} />
      ))}

      <td className="sc-c-vol np-num" title="Traded across both exchanges — Polymarket in dollars, Kalshi in $1 contracts">
        {money(r.volume)}
      </td>
      {reports && (
        <td className="sc-c-go">
          {r.href && (
            <Link href={r.href} className="sc-go" onClick={(e) => e.stopPropagation()}>
              View report <span aria-hidden="true">→</span>
            </Link>
          )}
        </td>
      )}
    </tr>
  )
}

function FixtureCell({ r, joiner }: { r: BoardRow; joiner: string }) {
  const inner = (
    <>
      <span className="sc-teams">
        {r.leftLogo && <img className="sp-logo" src={r.leftLogo} alt="" loading="lazy" />}
        {r.left} <span className="sc-v">{joiner}</span>{' '}
        {r.rightLogo && <img className="sp-logo" src={r.rightLogo} alt="" loading="lazy" />}
        {r.right}
        {r.score && (
          <b className="np-num sc-row-score">
            {r.score.left}–{r.score.right}
          </b>
        )}
      </span>
      <span className="sc-meta">
        {r.competition && <span className="sc-comp">{r.competition}</span>}
        {r.markets != null && <span className="sc-mkts np-num">{r.markets} markets</span>}
        <VenueTags r={r} />
      </span>
    </>
  )
  return r.href ? (
    <Link href={r.href} className="sc-fixture">
      {inner}
    </Link>
  ) : (
    <span className="sc-fixture">{inner}</span>
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
  { id: 'live', label: 'In play', Icon: IconLive, cls: 'is-live-icn' },
  { id: 'soon', label: 'Starting soon', Icon: IconClock },
  {
    id: 'both',
    label: 'Both exchanges',
    Icon: IconVenues,
    title: 'Games Kalshi and Polymarket both list — the only ones where a cheaper venue exists to find',
  },
  { id: 'watchlist', label: 'Watchlist', Icon: IconStar },
]

export type SortKey = 'default' | 'kickoff' | 'saving' | 'volume'

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'default', label: 'Biggest markets' },
  { id: 'kickoff', label: 'Starting first' },
  { id: 'saving', label: 'Biggest saving' },
  { id: 'volume', label: 'Most traded' },
]

/** The best saving on offer anywhere on this row, in probability points. What
 *  "biggest saving" sorts on, and it is zero unless both venues quote the same
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
    case 'volume':
      return out.sort((a, b) => b.volume - a.volume)
    case 'saving':
      return out.sort((a, b) => topSaving(b) - topSaving(a))
    case 'kickoff':
      return out.sort(
        (a, b) => new Date(a.kickoff ?? 0).getTime() - new Date(b.kickoff ?? 0).getTime()
      )
  }
}

/** "Starting soon" is the next two hours. Long enough to cover a build-up,
 *  short enough that the list is still a list. */
const SOON_MS = 2 * 3600_000

/** How many competitions get their own chip before the rest go behind "More".
 *  A Saturday football card runs to nearly 60 of them. */
const COMPS_SHOWN = 9

// ── the page ─────────────────────────────────────────────────────────────────

export interface BoardViewProps {
  columns: BoardColumn[]
  rows: BoardRow[]
  loading: boolean
  error: string | null
  /** Written between the two sides: "v" on football, "@" on a US sport. */
  joiner: string
  /** Chips for the competitions on the card. Off where every row is the same
   *  competition — a single chip narrows nothing. */
  competitionChips: boolean
  /** The label for the "everything" chip: "All football", "All games". */
  allLabel: string
  /** Rendered above the table: the heading, the intro, whatever the page wants. */
  head: React.ReactNode
  /** Rendered under the table. The venue counts and the fee note live here. */
  foot?: React.ReactNode
  emptyLabel: string
  /** Kalshi is still arriving. The column is there, just not filled yet. */
  pending?: string | null
  /** Extra chips beside the filters — the football board's two links out. */
  links?: React.ReactNode
  /** Seeded from ?q= by the nav search. */
  initialQuery?: string
  /** Show only the first N rows. The home page leads with the top ten and
   *  sends the rest to /soccer; the count still names the whole list, because
   *  "Top 10 of 97" and "10 of 97" are different claims. */
  limit?: number
}

export function BoardView({
  columns,
  rows,
  loading,
  error,
  joiner,
  competitionChips,
  allLabel,
  head,
  foot,
  emptyLabel,
  pending,
  links,
  initialQuery = '',
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

  const listed = limit != null ? shown.slice(0, limit) : shown

  /** Ordered by how much of today's card each competition is. Derived from the
   *  board rather than a hand-kept list, so a cup week shows up on its own. */
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

  return (
    <AppShell>
      <SportBar />
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
                {x.id === 'both' && links}
              </Fragment>
            ))}
          </div>

          {competitionChips && competitions.length > 1 && (
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
                aria-label="Sort the board"
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
              placeholder="Filter this list…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Filter the board"
            />
          </div>
        </div>

        {loading && <div className="np-empty">Reading the exchanges…</div>}

        {error && (
          <div className="np-note sc-error">
            <strong>Could not load the board.</strong> {error}
          </div>
        )}

        {!loading && !error && shown.length === 0 && (
          <div className="np-empty">
            {filter === 'watchlist'
              ? synced
                ? 'Nothing starred yet. Tap the ☆ on any row — your list follows you to any device you sign in on.'
                : 'Nothing starred yet. Tap the ☆ on any row to keep it here, in this browser.'
              : filter === 'both'
                ? 'No game on this board is listed by both exchanges right now.'
                : emptyLabel}
          </div>
        )}

        {shown.length > 0 && (
          <div className="sc-table-wrap">
            <table className="sc-table">
              <thead>
                <tr>
                  <th className="sc-c-n">#</th>
                  <th className="sc-c-star" />
                  <th className="sc-c-fixture">Game</th>
                  <th className="sc-c-state" title={`Start, in your time zone (${zone})`}>
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
                  <th className="sc-c-vol" title="Traded across both exchanges">
                    Volume
                  </th>
                  {reports && <th className="sc-c-go" />}
                </tr>
              </thead>
              <tbody>
                {listed.map((r, i) => (
                  <Row
                    key={r.key}
                    r={r}
                    n={i + 1}
                    columns={columns}
                    joiner={joiner}
                    reports={reports}
                    watched={watchlist.includes(r.key)}
                    onToggleWatch={toggleWatch}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {foot}
      </div>
    </AppShell>
  )
}
