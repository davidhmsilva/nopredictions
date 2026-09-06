'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { AppShell } from './components/AppShell'
import type { BookGrade, ScoutFixture } from './lib/scout'

const WATCHLIST_KEY = 'np_watchlist'

// ── formatting ───────────────────────────────────────────────────────────────
//
// Prices are decimal odds everywhere on this site. A probability is what the
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

function kickoffLabel(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const today = new Date()
  const sameDay =
    d.getDate() === today.getDate() &&
    d.getMonth() === today.getMonth() &&
    d.getFullYear() === today.getFullYear()
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return sameDay ? time : `${d.toLocaleDateString([], { weekday: 'short' })} ${time}`
}

// ── book grade ───────────────────────────────────────────────────────────────

const GRADE_COPY: Record<BookGrade, { label: string; cls: string; title: string }> = {
  clean: {
    label: 'CLEAN BOOK',
    cls: 'is-good',
    title:
      'Both sides quoted, spread at or under 6pp. On 6,449 measured rows this is ' +
      'the only bucket where the ask was not systematically worse than what happened.',
  },
  wide: {
    label: 'WIDE',
    cls: 'is-warn',
    title:
      'Spread over 6pp. Measured at −4.26pp of realised value in the 6-10pp bucket — ' +
      'the price is worse than it looks before you have any view at all.',
  },
  blown: {
    label: 'NO REAL BOOK',
    cls: 'is-live',
    title:
      'Spread over 20pp — a lone order parked far from any bid, not a market. ' +
      'Measured at −38pp. The mid here means nothing.',
  },
  'one-sided': {
    label: 'ONE-SIDED',
    cls: 'is-warn',
    title: 'Only one side of the book is quoted. You can hit it, but nothing prices it.',
  },
  settled: {
    label: 'DECIDED',
    cls: '',
    title:
      'The graded market is already settled — quoted tight around nothing. ' +
      'A 0.001/0.009 book has a 0.8pp spread and no bet in it.',
  },
  unknown: {
    label: 'BOOK NOT READ',
    cls: '',
    title: 'No usable quote came back for this board.',
  },
}

function BookBadge({ book }: { book: ScoutFixture['book'] }) {
  const grade: BookGrade = book?.grade ?? 'unknown'
  const c = GRADE_COPY[grade]
  const spread = book?.spreadPp
  return (
    <span className={`np-badge ${c.cls}`} title={c.title}>
      {c.label}
      {spread != null && <span className="np-num"> {spread.toFixed(1)}pp</span>}
    </span>
  )
}

// ── fixture card ─────────────────────────────────────────────────────────────

function FixtureCard({
  f,
  watched,
  onToggleWatch,
}: {
  f: ScoutFixture
  watched: boolean
  onToggleWatch: (slug: string) => void
}) {
  return (
    <div className={`sc-card${f.live ? ' is-live' : ''}`}>
      <div className="sc-card-top">
        <span className="sc-comp">{f.competition ?? 'Football'}</span>
        <div className="sc-card-top-right">
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
                'what a goalless opening twenty minutes looks like. Polymarket\'s listed start ' +
                'has run ~30 minutes early on smaller leagues and eight hours late elsewhere, ' +
                'so this is probable rather than confirmed.'
              }
            >
              KICKED OFF?
            </span>
          ) : (
            <span className="sc-kickoff np-num">{kickoffLabel(f.kickoff)}</span>
          )}
          <button
            className={`sc-watch${watched ? ' is-on' : ''}`}
            onClick={() => onToggleWatch(f.slug)}
            aria-label={watched ? 'Remove from watchlist' : 'Add to watchlist'}
            title={watched ? 'Remove from watchlist' : 'Add to watchlist'}
          >
            {watched ? '★' : '☆'}
          </button>
        </div>
      </div>

      <Link href={`/game/${f.slug}`} className="sc-card-body">
        <div className="sc-teams">
          <span className="sc-team">{f.home}</span>
          <span className="sc-vs">v</span>
          <span className="sc-team">{f.away}</span>
        </div>

        <div className="sc-odds">
          <div className="sc-odd">
            <span className="sc-odd-key">1</span>
            <span className="sc-odd-val np-num">{odds(f.oneX2.home)}</span>
          </div>
          <div className="sc-odd">
            <span className="sc-odd-key">X</span>
            <span className="sc-odd-val np-num">{odds(f.oneX2.draw)}</span>
          </div>
          <div className="sc-odd">
            <span className="sc-odd-key">2</span>
            <span className="sc-odd-val np-num">{odds(f.oneX2.away)}</span>
          </div>
          <div className="sc-odd sc-odd-wide">
            <span className="sc-odd-key">O2.5</span>
            <span className="sc-odd-val np-num">{odds(f.over25)}</span>
          </div>
        </div>
      </Link>

      <div className="sc-card-foot">
        <BookBadge book={f.book} />
        <span className="sc-foot-meta np-num" title="Traded volume across the whole fixture board">
          {money(f.volumeUsd)}
        </span>
        <span className="sc-foot-meta" title="Markets listed across all sibling boards">
          {f.markets} mkts
        </span>
        {f.hasFirstHalf && (
          <span
            className="np-badge is-info"
            title="This board carries the 1st-half markets our empirical tables actually measure."
          >
            MEASURED
          </span>
        )}
      </div>
    </div>
  )
}

// ── filters ──────────────────────────────────────────────────────────────────

type Filter = 'all' | 'live' | 'clean' | 'measured' | 'watchlist'

const FILTERS: { id: Filter; label: string; hint: string }[] = [
  { id: 'all', label: 'All', hint: 'Every board in the next 36 hours' },
  { id: 'live', label: 'In play', hint: 'Kicked off — confirmed by the board, or by the clock alone' },
  { id: 'clean', label: 'Clean books', hint: 'Spread 6pp or tighter, both sides quoted' },
  { id: 'measured', label: 'Measured', hint: 'Carries the markets our tables measure' },
  { id: 'watchlist', label: 'Watchlist', hint: 'Fixtures you starred' },
]

// ── page ─────────────────────────────────────────────────────────────────────

export default function ScoutPage() {
  const [fixtures, setFixtures] = useState<ScoutFixture[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
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
    return fixtures.filter((f) => {
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
  }, [fixtures, filter, query, watchlist])

  const liveCount = fixtures.filter((f) => f.live).length
  const cleanCount = fixtures.filter((f) => f.book?.grade === 'clean').length

  return (
    <AppShell>
      <div className="np-wrap">
        <div className="np-page-head">
          <div className="np-eyebrow">Scout · Polymarket football</div>
          <h1 className="np-h1">Where a price actually exists right now.</h1>
          <p className="np-page-sub">
            Every football board Polymarket has open in the next 36 hours, ranked live-first
            and graded on the one thing we have measured to matter: whether there is a real
            two-sided book behind the quote.
          </p>
        </div>

        <div className="sc-stats">
          <div className="sc-stat">
            <span className="sc-stat-val np-num">{loading ? '—' : fixtures.length}</span>
            <span className="sc-stat-key">boards open</span>
          </div>
          <div className="sc-stat">
            <span className="sc-stat-val np-num">{loading ? '—' : liveCount}</span>
            <span className="sc-stat-key">in play</span>
          </div>
          <div className="sc-stat">
            <span className="sc-stat-val np-num">{loading ? '—' : cleanCount}</span>
            <span className="sc-stat-key">clean books</span>
          </div>
        </div>

        {/* The claim stays visible; the working sits behind a disclosure.
            On a 375px screen the full paragraph pushed the entire board below
            the fold, and an honesty note nobody scrolls past is not honesty. */}
        <details className="np-note is-info sc-honesty">
          <summary className="sc-honesty-head">
            <strong>This is not a tip sheet.</strong> Boards are graded on book quality,
            not on a claimed edge. <span className="sc-honesty-more">Why →</span>
          </summary>
          <div className="sc-honesty-body">
            We do not rank these by an edge against the sharp line: we measured that number
            and it came out at <span className="np-num">+0.10pp</span> with a confidence
            interval spanning zero, which is the spread and the fee, not an edge. What did
            separate, across 6,449 measured rows, is book quality — at a spread of 0-3pp the
            ask was fair to slightly cheap; past 20pp it ran <span className="np-num">−38pp</span>.
            So that is what the grade on each card is. Open a fixture for the measured lines.
          </div>
        </details>

        <div className="sc-controls">
          <div className="sc-filters">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                className={`sc-filter${filter === f.id ? ' is-on' : ''}`}
                onClick={() => setFilter(f.id)}
                title={f.hint}
              >
                {f.label}
                {f.id === 'watchlist' && watchlist.length > 0 && (
                  <span className="sc-filter-count np-num">{watchlist.length}</span>
                )}
              </button>
            ))}
          </div>
          <input
            className="np-input sc-search"
            placeholder="Search team or competition…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search fixtures"
          />
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

        <div className="sc-grid">
          {shown.map((f) => (
            <FixtureCard
              key={f.slug}
              f={f}
              watched={watchlist.includes(f.slug)}
              onToggleWatch={toggleWatch}
            />
          ))}
        </div>

        {!loading && !error && fixtures.length > 0 && (
          <p className="sc-probe-note">
            Every board is graded on its match-goals Over 2.5 book, or the draw where that
            line is not listed. The busiest boards are re-read live from the order book;
            the rest use Polymarket&apos;s own quote, which lags it. Hover a grade to see
            which, and what the number behind it was measured on.
          </p>
        )}
      </div>
    </AppShell>
  )
}
