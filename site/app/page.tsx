'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { AppShell } from './components/AppShell'
import type { BookGrade, ScoutFixture } from './lib/scout'

const WATCHLIST_KEY = 'np_watchlist'

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

type SortKey = 'default' | 'volume' | 'kickoff' | 'spread' | 'markets'

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'default', label: 'In play first' },
  { id: 'volume', label: 'Volume' },
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
    case 'volume':
      return out.sort((a, b) => b.volumeUsd - a.volumeUsd)
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

type Filter = 'all' | 'live' | 'clean' | 'measured' | 'watchlist'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'live', label: 'In play' },
  { id: 'clean', label: 'Clean books' },
  { id: 'measured', label: 'Measured' },
  { id: 'watchlist', label: 'Watchlist' },
]

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
        {f.finished ? (
          <span className="np-badge">FT</span>
        ) : f.liveSource === 'board' ? (
          <span
            className="np-badge is-live"
            title="A market on this board has resolved, so the match has certainly started."
          >
            ● LIVE
          </span>
        ) : f.liveSource === 'clock' ? (
          <span
            className="np-badge is-warn"
            title={
              'The listed kick-off has passed and nothing on the board has resolved yet — ' +
              "what a goalless opening twenty minutes looks like. Polymarket's listed start " +
              'has run ~30 min early on smaller leagues and eight hours late elsewhere, so ' +
              'this is probable rather than confirmed.'
            }
          >
            KICKED OFF?
          </span>
        ) : (
          <span className="sc-in np-num">{clock(f.kickoff)}</span>
        )}
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
  const [watchlist, setWatchlist] = useState<string[]>([])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(WATCHLIST_KEY)
      if (raw) setWatchlist(JSON.parse(raw))
    } catch {
      /* a blocked or empty store is not an error — the page works without it */
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

  function toggleWatch(slug: string) {
    setWatchlist((prev) => {
      const next = prev.includes(slug) ? prev.filter((s) => s !== slug) : [...prev, slug]
      try {
        localStorage.setItem(WATCHLIST_KEY, JSON.stringify(next))
      } catch {
        /* per-viewer convenience only */
      }
      return next
    })
  }

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = fixtures.filter((f) => {
      if (q && !`${f.home} ${f.away} ${f.competition ?? ''}`.toLowerCase().includes(q)) return false
      switch (filter) {
        case 'live':
          return f.live
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
  }, [fixtures, filter, sort, query, watchlist])

  const stats = useMemo(() => {
    const live = fixtures.filter((f) => f.live).length
    const clean = fixtures.filter((f) => f.book?.grade === 'clean').length
    const vol = fixtures.reduce((s, f) => s + f.volumeUsd, 0)
    const liq = fixtures.reduce((s, f) => s + f.liquidityUsd, 0)
    const mkts = fixtures.reduce((s, f) => s + f.markets, 0)
    const comps = new Set(fixtures.map((f) => f.competition).filter(Boolean)).size
    return { live, clean, vol, liq, mkts, comps }
  }, [fixtures])

  const dash = loading ? '—' : null

  return (
    <AppShell>
      {/* ── the tape ── */}
      <div className="sc-tape">
        <div className="sc-tape-inner">
          <span className="sc-tape-cell">
            <b className="np-num">{dash ?? fixtures.length}</b> BOARDS
          </span>
          <span className="sc-tape-cell">
            <b className="np-num">{dash ?? stats.mkts.toLocaleString('en-US')}</b> MARKETS
          </span>
          <span className="sc-tape-cell">
            <b className="np-num">{dash ?? stats.comps}</b> COMPETITIONS
          </span>
          <span className="sc-tape-cell is-live">
            <b className="np-num">{dash ?? stats.live}</b> IN PLAY
          </span>
          <span className="sc-tape-cell is-good">
            <b className="np-num">{dash ?? stats.clean}</b> CLEAN BOOKS
          </span>
          <span className="sc-tape-cell">
            VOLUME <b className="np-num">{dash ?? money(stats.vol)}</b>
          </span>
          <span className="sc-tape-cell">
            LIQUIDITY <b className="np-num">{dash ?? money(stats.liq)}</b>
          </span>
        </div>
      </div>

      <div className="np-wrap">
        <div className="sc-head">
          <h1 className="sc-h1">Football boards</h1>
          <p className="sc-h1-sub">
            Every Polymarket football board open in the next 36 hours, in play first.
          </p>
        </div>

        {/* ── controls ── */}
        <div className="sc-bar">
          <div className="sc-chips">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                className={`sc-chip${filter === f.id ? ' is-on' : ''}`}
                onClick={() => setFilter(f.id)}
              >
                {f.label}
                {f.id === 'watchlist' && watchlist.length > 0 && (
                  <span className="sc-chip-n np-num">{watchlist.length}</span>
                )}
              </button>
            ))}
          </div>

          <div className="sc-bar-right">
            <label className="sc-sort">
              <span className="sc-sort-key">Sort</span>
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as SortKey)}
                aria-label="Sort fixtures"
              >
                {SORTS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <input
              className="sc-search"
              placeholder="Search…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search fixtures"
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
              ? 'Nothing starred yet. Tap the ☆ on any fixture to keep it here.'
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
