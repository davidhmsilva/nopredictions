'use client'

import { Fragment, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AppShell } from './components/AppShell'
import {
  IconAll,
  IconBoard,
  IconClock,
  IconDrop,
  IconInsights,
  IconLab,
  IconLive,
  IconStar,
  IconWallet,
} from './components/icons'
import type { BookGrade, ScoutFixture } from './lib/scout'
import {
  dayTimeText,
  formatName,
  priceText,
  timeText,
  useOddsFormat,
  zoneLabel,
  type OddsFormat,
} from './lib/display'
import { OddsToggle } from './components/OddsToggle'
import { useSession } from './lib/useSession'
import { useWatchlist } from './lib/useWatchlist'

// ── formatting ───────────────────────────────────────────────────────────────
//
// Prices are held as probabilities and written in the reader's own format —
// American, decimal or implied — by lib/display. A probability is what a model
// thinks; the odds are what you pay.

function odds(p: number | null | undefined, f: OddsFormat): string {
  if (p == null || p <= 0.01 || p >= 0.99) return '—'
  return priceText(p, f)
}

function money(v: number | null | undefined): string {
  if (v == null || v <= 0) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

/** Kick-off in the reader's own zone. The zone is named once, in the column
 *  head, rather than on every row. */
function clock(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const mins = Math.round((d.getTime() - Date.now()) / 60000)
  if (mins < 0) return timeText(d)
  if (mins < 60) return `${mins}m`
  if (mins < 24 * 60) return `${Math.floor(mins / 60)}h ${mins % 60}m`
  return dayTimeText(d)
}

// ── book grade ───────────────────────────────────────────────────────────────
//
// The grade no longer has a column or a badge. It was a research reading on a
// board people open to see what is on today, and the measurement it stood on
// did not go anywhere: the Game Center shows the spread and the depth of every
// market on a fixture, which says more than one word ever did.
//
// What survives here is the RANK, because "tightest book" is still a sort and
// it has to know that a decided market quoting 0.001/0.009 is not the tightest
// book on the card just because that arithmetic is 0.8pp.

// ── sorting ──────────────────────────────────────────────────────────────────

type SortKey = 'default' | 'kickoff' | 'spread' | 'markets'

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'default', label: 'Biggest markets' },
  { id: 'kickoff', label: 'Kick-off' },
  { id: 'spread', label: 'Tightest book' },
  { id: 'markets', label: 'Most markets' },
]

/** Rank within a grade, so "tightest book" cannot put a decided market quoting
 *  0.001/0.009 above a real two-sided one on the strength of its 0.8pp. */
const GRADE_RANK: Record<BookGrade, number> = {
  clean: 0, wide: 1, 'one-sided': 2, blown: 3, settled: 4, unknown: 5,
}

function sortFixtures(fs: ScoutFixture[], key: SortKey): ScoutFixture[] {
  if (key === 'default') return fs
  const out = fs.slice()
  switch (key) {
    case 'markets':
      return out.sort((a, b) => b.markets - a.markets)
    case 'kickoff':
      return out.sort(
        (a, b) => new Date(a.kickoff ?? 0).getTime() - new Date(b.kickoff ?? 0).getTime()
      )
    case 'spread':
      return out.sort((a, b) => {
        const ga = GRADE_RANK[a.book?.grade ?? 'unknown']
        const gb = GRADE_RANK[b.book?.grade ?? 'unknown']
        if (ga !== gb) return ga - gb
        return (a.book?.spreadPp ?? 99) - (b.book?.spreadPp ?? 99)
      })
  }
}

// ── filters ──────────────────────────────────────────────────────────────────

type Filter = 'all' | 'live' | 'soon' | 'watchlist'

const FILTERS: {
  id: Filter
  label: string
  Icon: (p: { className?: string }) => JSX.Element
  cls?: string
}[] = [
  { id: 'all', label: 'All', Icon: IconAll },
  { id: 'live', label: 'In play', Icon: IconLive, cls: 'is-live-icn' },
  { id: 'soon', label: 'Starting soon', Icon: IconClock },
  { id: 'watchlist', label: 'Watchlist', Icon: IconStar },
]

/** The two links that share this row with the filters.
 *
 *  ⚠️ They are the odd ones out and are drawn that way on purpose. Every chip
 *     to their left narrows the list below; these leave the page. A link that
 *     looks exactly like a filter is a link people click by accident, so they
 *     get their own class, a divider, an arrow, and never the selected state.
 *
 *  They sit after "Starting soon" because that is where "Clean books" and
 *  "Measured" used to be — both filters the board no longer needs, since the
 *  grade is a column on every row anyway. */
const LINKS: { href: string; label: string; Icon: (p: { className?: string }) => JSX.Element; title: string }[] = [
  {
    href: '/dropping-odds',
    label: 'Dropping odds',
    Icon: IconDrop,
    title: 'Where the market moved in the last 24 hours — pre-match, and only on books with real money through them',
  },
  {
    href: '/insights',
    label: 'Insights',
    Icon: IconInsights,
    title: 'What we measured, and what it said — including the results that went the wrong way',
  },
]

const LINKS_AFTER: Filter = 'soon'

/** "Starting soon" is the next two hours. Long enough to cover a build-up,
 *  short enough that the list is still a list. */
const SOON_MS = 2 * 3600_000

/** How many competitions get their own chip before the rest go behind "More".
 *  A Saturday card runs to nearly 60 of them. */
const COMPS_SHOWN = 9

/** What the fixture's clock is doing, and how confidently we know it.
 *
 *  Three different claims, and the badge says which. `KICKED OFF?` used to be
 *  the answer for every started match with nothing yet resolved on its board —
 *  which is most of a goalless first half — and it reads as a shrug. Since the
 *  live feed landed it is what is left over, not the usual case. */
function LiveState({ f }: { f: ScoutFixture }) {
  if (f.finished) return <span className="np-badge">FT</span>

  // Polymarket's own reading and ESPN's look identical on the card, because to
  // a reader they are the same claim: it is in play and this is the minute.
  // Which one said so lives in the tooltip, where it belongs.
  if (f.liveSource === 'pm' || f.liveSource === 'feed') {
    return (
      <span
        className="np-badge is-live"
        title={
          f.liveSource === 'pm'
            ? f.phase === 'HT'
              ? "Half time, per Polymarket's own live data. There is no minute at the break."
              : "Polymarket's own live data for this event — the same clock its page shows."
            : 'ESPN live feed. Polymarket has not tagged this fixture as live.'
        }
      >
        {f.phase === 'HT' ? (
          'HT'
        ) : (
          <>
            ● {f.minute != null ? <span className="np-num">{f.minute}&apos;</span> : 'LIVE'}
            {f.phase === 'ET' || f.phase === 'PEN' ? ` ${f.phase}` : ''}
          </>
        )}
      </span>
    )
  }
  if (f.liveSource === 'board') {
    return (
      <span
        className="np-badge is-live"
        title="A market on this board has resolved, so the match has certainly started. No live clock for this competition."
      >
        ● LIVE
      </span>
    )
  }
  if (f.liveSource === 'clock') {
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
  return <span className="sc-in np-num">{clock(f.kickoff)}</span>
}

// ── one row ──────────────────────────────────────────────────────────────────

function Row({
  f,
  n,
  watched,
  onToggleWatch,
}: {
  f: ScoutFixture
  n: number
  watched: boolean
  onToggleWatch: (slug: string) => void
}) {
  const router = useRouter()
  const oddsFmt = useOddsFormat()
  return (
    /* The whole row navigates, not just the two links inside it. Those links
       stay, and they are what makes this reachable from a keyboard — the row
       handler is a convenience for a pointer, never the only way through. */
    <tr
      className={`is-clickable${f.live ? ' is-live' : f.finished ? ' is-done' : ''}`}
      onClick={() => router.push(`/game/${f.slug}`)}
    >
      <td className="sc-c-n np-num">{n}</td>
      <td className="sc-c-star">
        <button
          className={`sc-star${watched ? ' is-on' : ''}`}
          onClick={(e) => {
            // Starring is not opening. Without this the row handler fires too
            // and the click both stars the fixture and leaves the page.
            e.stopPropagation()
            onToggleWatch(f.slug)
          }}
          aria-label={watched ? 'Remove from watchlist' : 'Add to watchlist'}
        >
          {watched ? '★' : '☆'}
        </button>
      </td>

      <td className="sc-c-fixture">
        <Link href={`/game/${f.slug}`} className="sc-fixture">
          <span className="sc-teams">
            {f.home} <span className="sc-v">v</span> {f.away}
          </span>
          <span className="sc-meta">
            <span className="sc-comp">{f.competition ?? 'Football'}</span>
            <span className="sc-mkts np-num">{f.markets} markets</span>
          </span>
        </Link>
      </td>

      <td className="sc-c-state">
        <LiveState f={f} />
      </td>

      <td className="sc-c-odd np-num">{odds(f.oneX2.home, oddsFmt)}</td>
      <td className="sc-c-odd np-num">{odds(f.oneX2.draw, oddsFmt)}</td>
      <td className="sc-c-odd np-num">{odds(f.oneX2.away, oddsFmt)}</td>
      <td className="sc-c-odd sc-c-o25 np-num">{odds(f.over25, oddsFmt)}</td>

      <td className="sc-c-vol np-num">{money(f.volumeUsd)}</td>
      <td className="sc-c-go">
        <Link href={`/game/${f.slug}`} className="sc-go">
          View report <span aria-hidden="true">→</span>
        </Link>
      </td>
    </tr>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function ScoutPage() {
  const [fixtures, setFixtures] = useState<ScoutFixture[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [sort, setSort] = useState<SortKey>('default')
  const [query, setQuery] = useState('')
  const [comp, setComp] = useState<string | null>(null)
  const [allComps, setAllComps] = useState(false)
  const { me } = useSession()
  const oddsFmt = useOddsFormat()
  // Named once, in the column head. Computed on the client: the server runs in
  // UTC and has no reader to ask.
  const zone = useMemo(() => zoneLabel(), [])
  // Local for everyone, mirrored to Postgres for Pro. The Supabase client is
  // only loaded when there is a Pro session to load it for.
  const { slugs: watchlist, toggle: toggleWatch, synced } = useWatchlist(me?.plan === 'pro')

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


  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = fixtures.filter((f) => {
      if (q && !`${f.home} ${f.away} ${f.competition ?? ''}`.toLowerCase().includes(q)) return false
      if (comp && f.competition !== comp) return false
      switch (filter) {
        case 'live':
          return f.live
        case 'soon': {
          if (f.live || f.finished || !f.kickoff) return false
          const dt = new Date(f.kickoff).getTime() - Date.now()
          return dt > 0 && dt <= SOON_MS
        }
        case 'watchlist':
          return watchlist.includes(f.slug)
        default:
          return true
      }
    })
    return sortFixtures(filtered, sort)
  }, [fixtures, filter, sort, query, comp, watchlist])

  /** The competition chips, ordered by how much of today's card each one is.
   *  Derived from the board rather than from a hand-kept list, so a cup week
   *  or a new league shows up on its own. */
  const competitions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const f of fixtures) {
      if (!f.competition) continue
      counts.set(f.competition, (counts.get(f.competition) ?? 0) + 1)
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([name, n]) => ({ name, n }))
  }, [fixtures])

  /** The games leading the page. Same board the table renders, taken from the
   *  top of it — nothing here is a separate feed that could disagree with the
   *  rows underneath. */
  const headline = useMemo(
    () => fixtures.filter((f) => !f.finished).slice(0, 4),
    [fixtures]
  )

  return (
    <AppShell>
      {/* ── category bar: state on the left, competition on the right ── */}
      <div className="sc-cats">
        <div className="sc-cats-inner">
          <div className="sc-cat-group">
            {FILTERS.map((f) => (
              <Fragment key={f.id}>
                <button
                  className={`sc-cat${filter === f.id ? ' is-on' : ''}`}
                  onClick={() => setFilter(f.id)}
                >
                  <f.Icon className={`sc-cat-icn ${f.cls ?? ''}`} />
                  {f.label}
                  {f.id === 'watchlist' && watchlist.length > 0 && (
                    <span className="sc-cat-n np-num">{watchlist.length}</span>
                  )}
                </button>
                {f.id === LINKS_AFTER &&
                  LINKS.map((l) => (
                    <Link key={l.href} href={l.href} className="sc-cat is-link" title={l.title}>
                      <l.Icon className="sc-cat-icn" />
                      {l.label}
                      <span className="sc-cat-go" aria-hidden="true">→</span>
                    </Link>
                  ))}
              </Fragment>
            ))}
          </div>

          {competitions.length > 0 && (
            <>
              <span className="sc-cat-div" aria-hidden="true" />
              <div className="sc-cat-group">
                <button
                  className={`sc-cat${comp === null ? ' is-on' : ''}`}
                  onClick={() => setComp(null)}
                >
                  All football
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
        {/* The intro says what the site is FOR. "All of today's football,
            priced" described the list below it and left a first-time visitor
            to work out the rest. Agents are not named here until the private
            agents ship — a line promising them would point at the old page. */}
        <div className="sc-head">
          <p className="sc-eyebrow">NOPREDICTIONS · Prediction-market research</p>
          <h1 className="sc-h1">See if the price is wrong — before you trade it</h1>
          <p className="sc-h1-sub">
            Today&apos;s Polymarket football, biggest markets first. Open a game to check its price
            against how matches priced the same way actually ended, both teams&apos; form against the
            closing line, and the book you would be trading into — plus the line-ups and a
            plain-English brief. No tips: the numbers, and you decide.
          </p>
          <nav className="sc-does" aria-label="What you can do here">
            <a href="#games">
              <IconBoard />
              <div>
                <b>Open a game</b>
                <span>Every fixture below has its own page</span>
              </div>
            </a>
            <Link href="/lab">
              <IconLab />
              <div>
                <b>Test a theory</b>
                <span>Backtest your idea on 111,475 real games</span>
              </div>
            </Link>
            <Link href="/wallet">
              <IconWallet />
              <div>
                <b>Read a trader</b>
                <span>Any Polymarket wallet&apos;s full record</span>
              </div>
            </Link>
          </nav>
        </div>
        <div id="games" />

        {/* ── the games leading the card ── */}
        {!loading && !error && headline.length > 0 && (
          <div className="sc-big">
            {headline.map((f) => {
              return (
                <Link
                  key={f.slug}
                  href={`/game/${f.slug}`}
                  className={`sc-big-card${f.live ? ' is-live' : ''}`}
                >
                  <div className="sc-big-top">
                    <span className="sc-big-comp">{f.competition ?? 'Football'}</span>
                    <LiveState f={f} />
                  </div>

                  <div className="sc-big-teams">
                    <span>{f.home}</span>
                    <span className="sc-big-v">
                      {f.score && f.live ? (
                        <b className="np-num sc-big-score">
                          {f.score.home}–{f.score.away}
                        </b>
                      ) : (
                        'v'
                      )}
                    </span>
                    <span>{f.away}</span>
                  </div>

                  <div className="sc-big-odds">
                    <span className="sc-big-odd">
                      <em>Home</em>
                      <b className="np-num">{odds(f.oneX2.home, oddsFmt)}</b>
                    </span>
                    <span className="sc-big-odd">
                      <em>Draw</em>
                      <b className="np-num">{odds(f.oneX2.draw, oddsFmt)}</b>
                    </span>
                    <span className="sc-big-odd">
                      <em>Away</em>
                      <b className="np-num">{odds(f.oneX2.away, oddsFmt)}</b>
                    </span>
                    <span className="sc-big-odd sc-big-odd-alt">
                      <em>O2.5</em>
                      <b className="np-num">{odds(f.over25, oddsFmt)}</b>
                    </span>
                  </div>

                  <div className="sc-big-foot">
                    <span className="np-num sc-big-vol">{money(f.volumeUsd)} traded</span>
                    <span className="np-num sc-big-mkts">{f.markets} markets</span>
                    {/* This slot held the book grade. A grade is a research
                        reading; what someone looking at a card needs to know is
                        that the card goes somewhere. The measurement is not
                        lost — the Game Center shows the spread and the depth
                        per market, which is more than a one-word grade said. */}
                    <span className="sc-report">
                      View report <span aria-hidden="true">→</span>
                    </span>
                  </div>
                </Link>
              )
            })}
          </div>
        )}

        {/* ── controls ── */}
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
              {loading ? '' : `${shown.length} of ${fixtures.length}`}
            </span>
          </div>

          <div className="sc-bar-right">
            {/* Below 1100px the nav has no room for it, so it lives here. */}
            <OddsToggle className="np-odds-bar" />
            <label className="sc-sort">
              <span className="sc-sort-key">Sort</span>
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as SortKey)}
                aria-label="Sort fixtures"
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
              aria-label="Filter fixtures"
            />
          </div>
        </div>

        {loading && <div className="np-empty">Reading the boards…</div>}

        {error && (
          <div className="np-note sc-error">
            <strong>Could not load the board.</strong> {error}
          </div>
        )}

        {!loading && !error && shown.length === 0 && (
          <div className="np-empty">
            {filter === 'watchlist'
              ? synced
                ? 'Nothing starred yet. Tap the ☆ on any fixture — your list follows you to any device you sign in on.'
                : 'Nothing starred yet. Tap the ☆ on any fixture to keep it here, in this browser.'
              : 'No board matches that filter right now.'}
          </div>
        )}

        {shown.length > 0 && (
          <div className="sc-table-wrap">
            <table className="sc-table">
              <thead>
                <tr>
                  <th className="sc-c-n">#</th>
                  <th className="sc-c-star" />
                  <th className="sc-c-fixture">Fixture</th>
                  <th className="sc-c-state" title={`Kick-off, in your time zone (${zone})`}>
                    Starts{zone && <span className="sc-th-zone">{zone}</span>}
                  </th>
                  <th className="sc-c-odd" title={`Home win — ${formatName(oddsFmt)} at Polymarket's mid`}>Home</th>
                  <th className="sc-c-odd" title={`Draw — ${formatName(oddsFmt)} at Polymarket's mid`}>Draw</th>
                  <th className="sc-c-odd" title={`Away win — ${formatName(oddsFmt)} at Polymarket's mid`}>Away</th>
                  <th
                    className="sc-c-odd sc-c-o25"
                    title={`Over 2.5 goals — ${formatName(oddsFmt)} at Polymarket's mid`}
                  >
                    O2.5
                  </th>
                  <th className="sc-c-vol" title="Traded volume across every market on this fixture">
                    Volume
                  </th>
                  <th className="sc-c-go" />
                </tr>
              </thead>
              <tbody>
                {shown.map((f, i) => (
                  <Row
                    key={f.slug}
                    f={f}
                    n={i + 1}
                    watched={watchlist.includes(f.slug)}
                    onToggleWatch={toggleWatch}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  )
}
