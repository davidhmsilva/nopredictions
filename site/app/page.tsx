'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { AppShell } from './components/AppShell'
import {
  IconAll,
  IconBook,
  IconClock,
  IconLive,
  IconRuler,
  IconStar,
} from './components/icons'
import type { BookGrade, ScoutFixture } from './lib/scout'
import { useSession } from './lib/useSession'
import { useWatchlist } from './lib/useWatchlist'

// ── formatting ───────────────────────────────────────────────────────────────
//
// Prices are decimal odds everywhere on this site. A probability is what a
// model thinks; the decimal is what you pay.

function odds(p: number | null | undefined): string {
  if (p == null || p <= 0.01 || p >= 0.99) return '—'
  return (1 / p).toFixed(2)
}

function money(v: number | null | undefined): string {
  if (v == null || v <= 0) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function clock(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const mins = Math.round((d.getTime() - Date.now()) / 60000)
  if (mins < 0) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (mins < 60) return `${mins}m`
  if (mins < 24 * 60) return `${Math.floor(mins / 60)}h ${mins % 60}m`
  return d.toLocaleDateString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })
}

// ── book grade ───────────────────────────────────────────────────────────────
//
// The case for grading on book quality lives in these tooltips and nowhere
// else on the page. A trader opening this on a matchday wants the board, not
// the reasoning — but each tooltip still carries the measurement it stands on,
// so a grade is checkable without an essay above the table.

const GRADE: Record<BookGrade, { label: string; cls: string; title: string }> = {
  clean: {
    label: 'CLEAN',
    cls: 'is-good',
    title:
      'Both sides quoted, spread at or under 6pp. Across 6,449 measured rows this is the ' +
      'only bucket where the ask was not systematically worse than what happened.',
  },
  wide: {
    label: 'WIDE',
    cls: 'is-warn',
    title:
      'Spread over 6pp. Measured at −4.26pp of realised value in the 6-10pp bucket — the ' +
      'price is worse than it looks before you have any view at all.',
  },
  blown: {
    label: 'NO BOOK',
    cls: 'is-bad',
    title:
      'Spread over 20pp — a lone order parked far from any bid, not a market. Measured at ' +
      '−38pp. The mid here means nothing.',
  },
  'one-sided': {
    label: '1-SIDED',
    cls: 'is-warn',
    title: 'Only one side of the book is quoted. You can hit it, but nothing prices it.',
  },
  settled: {
    label: 'DECIDED',
    cls: 'is-dim',
    title:
      'Already settled — quoted tight around nothing. A 0.001/0.009 book has a 0.8pp spread ' +
      'and no bet in it.',
  },
  unknown: {
    label: '—',
    cls: 'is-dim',
    title: 'No usable quote came back for this board.',
  },
}

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

type Filter = 'all' | 'live' | 'soon' | 'clean' | 'measured' | 'watchlist'

const FILTERS: {
  id: Filter
  label: string
  Icon: (p: { className?: string }) => JSX.Element
  cls?: string
}[] = [
  { id: 'all', label: 'All', Icon: IconAll },
  { id: 'live', label: 'In play', Icon: IconLive, cls: 'is-live-icn' },
  { id: 'soon', label: 'Starting soon', Icon: IconClock },
  { id: 'clean', label: 'Clean books', Icon: IconBook },
  { id: 'measured', label: 'Measured', Icon: IconRuler },
  { id: 'watchlist', label: 'Watchlist', Icon: IconStar },
]

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
  const g = GRADE[f.book?.grade ?? 'unknown']
  return (
    <tr className={f.live ? 'is-live' : f.finished ? 'is-done' : undefined}>
      <td className="sc-c-n np-num">{n}</td>
      <td className="sc-c-star">
        <button
          className={`sc-star${watched ? ' is-on' : ''}`}
          onClick={() => onToggleWatch(f.slug)}
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

      <td className="sc-c-odd np-num">{odds(f.oneX2.home)}</td>
      <td className="sc-c-odd np-num">{odds(f.oneX2.draw)}</td>
      <td className="sc-c-odd np-num">{odds(f.oneX2.away)}</td>
      <td className="sc-c-odd sc-c-o25 np-num">{odds(f.over25)}</td>

      <td className="sc-c-book">
        <span className={`sc-grade ${g.cls}`} title={g.title}>
          {g.label}
        </span>
        {f.book?.spreadPp != null && (
          <span
            className="sc-spread np-num"
            title={
              f.book.source === 'clob'
                ? `Read live from the order book on: ${f.book.market}`
                : `Polymarket's own quote, which lags the book, on: ${f.book.market}`
            }
          >
            {f.book.spreadPp.toFixed(1)}
            {f.book.source === 'gamma' && <span className="sc-stale">·</span>}
          </span>
        )}
      </td>

      <td className="sc-c-vol np-num">{money(f.volumeUsd)}</td>
      <td className="sc-c-go">
        <Link href={`/game/${f.slug}`} className="sc-go">
          Open
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
        case 'clean':
          return f.book?.grade === 'clean'
        case 'measured':
          return f.hasFirstHalf && f.hasTotals
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
              <button
                key={f.id}
                className={`sc-cat${filter === f.id ? ' is-on' : ''}`}
                onClick={() => setFilter(f.id)}
              >
                <f.Icon className={`sc-cat-icn ${f.cls ?? ''}`} />
                {f.label}
                {f.id === 'watchlist' && watchlist.length > 0 && (
                  <span className="sc-cat-n np-num">{watchlist.length}</span>
                )}
              </button>
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
        <div className="sc-head">
          <h1 className="sc-h1">All of today&apos;s football, priced</h1>
          <p className="sc-h1-sub">
            The games everyone is on, biggest first — live prices in decimal odds, and every
            other fixture Polymarket has open underneath.
          </p>
        </div>

        {/* ── the games leading the card ── */}
        {!loading && !error && headline.length > 0 && (
          <div className="sc-big">
            {headline.map((f) => {
              const g = GRADE[f.book?.grade ?? 'unknown']
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
                      <em>1</em>
                      <b className="np-num">{odds(f.oneX2.home)}</b>
                    </span>
                    <span className="sc-big-odd">
                      <em>X</em>
                      <b className="np-num">{odds(f.oneX2.draw)}</b>
                    </span>
                    <span className="sc-big-odd">
                      <em>2</em>
                      <b className="np-num">{odds(f.oneX2.away)}</b>
                    </span>
                    <span className="sc-big-odd sc-big-odd-alt">
                      <em>O2.5</em>
                      <b className="np-num">{odds(f.over25)}</b>
                    </span>
                  </div>

                  <div className="sc-big-foot">
                    <span className="np-num sc-big-vol">{money(f.volumeUsd)} traded</span>
                    <span className="np-num sc-big-mkts">{f.markets} markets</span>
                    <span className={`sc-grade ${g.cls}`} title={g.title}>
                      {g.label}
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
                  <th className="sc-c-state">Starts</th>
                  <th className="sc-c-odd" title="Home win — decimal odds at Polymarket's mid">1</th>
                  <th className="sc-c-odd" title="Draw — decimal odds at Polymarket's mid">X</th>
                  <th className="sc-c-odd" title="Away win — decimal odds at Polymarket's mid">2</th>
                  <th
                    className="sc-c-odd sc-c-o25"
                    title="Over 2.5 goals — decimal odds at Polymarket's mid"
                  >
                    O2.5
                  </th>
                  <th
                    className="sc-c-book"
                    title={
                      "The spread on this board's Over 2.5 book, in points, or the draw where " +
                      'that line is not listed. On 6,449 measured rows the ask was fair to ' +
                      'slightly cheap at 0-3pp of spread and ran −38pp past 20pp. Depth is not ' +
                      'the tell: one book quoted bid 0.55 / ask 0.99 behind $30,117 of depth and ' +
                      'traded at 0.56 two minutes later. A dot means the quote is cached, not live.'
                    }
                  >
                    Book <span className="sc-info">ⓘ</span>
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
