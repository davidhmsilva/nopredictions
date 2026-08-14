'use client'

import { useCallback, useEffect, useState } from 'react'
import { Nav, MobileNav } from '../../components/Nav'
import type { Section } from '../../lib/types'
import type { GameData, MarketGroup, Outcome, PricePoint } from '../../lib/gamecenter'

const WATCHLIST_KEY = 'np_watchlist'

// The user thinks in decimal odds, so every probability on this page carries the
// price next to it.
//
// Outside SETTLED_BAND the decimal price stops being a price: an outcome quoted
// at 0.001 renders as 1000.00, which reads like a bet nobody could place rather
// than like a market that has already resolved. Those are labelled instead.
const SETTLED_BAND = 0.01

function isSettled(p: number | null | undefined): boolean {
  return p != null && (p <= SETTLED_BAND || p >= 1 - SETTLED_BAND)
}

function odds(p: number | null | undefined): string {
  if (p == null || p <= 0 || p >= 1) return '—'
  if (isSettled(p)) return p >= 0.5 ? 'settled ✓' : 'settled ✗'
  return (1 / p).toFixed(2)
}

function pct(p: number | null | undefined): string {
  return p == null ? '—' : `${(p * 100).toFixed(1)}%`
}

function money(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

// ── sparkline ────────────────────────────────────────────────────────────────

function Sparkline({ points }: { points: PricePoint[] }) {
  if (points.length < 2) return null
  const W = 320
  const H = 48
  const ps = points.map((p) => p.p)
  const lo = Math.min(...ps)
  const hi = Math.max(...ps)
  const span = hi - lo || 1
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * W
      const y = H - ((p.p - lo) / span) * H
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  const first = ps[0]
  const last = ps[ps.length - 1]
  const up = last >= first
  const movePp = (last - first) * 100

  return (
    <div className="gc-spark">
      <svg viewBox={`0 0 ${W} ${H}`} className="gc-spark-svg" preserveAspectRatio="none">
        <path d={d} fill="none" stroke={up ? 'var(--green)' : 'var(--red)'} strokeWidth="1.5" />
      </svg>
      <div className="gc-spark-meta">
        <span>{pct(first)} → {pct(last)}</span>
        <span className={up ? 'c-green' : 'c-red'}>
          {movePp > 0 ? '+' : ''}{movePp.toFixed(1)}pp / 24h
        </span>
      </div>
    </div>
  )
}

// ── live header ──────────────────────────────────────────────────────────────

function LiveHeader({ data }: { data: GameData }) {
  const live = data.live
  const kickoff = data.kickoff ? new Date(data.kickoff) : null

  return (
    <div className="gc-header">
      <div className="gc-teams">
        <span className="gc-team">{data.home}</span>
        {live ? (
          <span className="gc-score">
            {live.homeGoals}-{live.awayGoals}
            {live.minute != null && <span className="gc-minute">{live.minute}&apos;</span>}
            <span className="analysis-live-badge">LIVE</span>
          </span>
        ) : (
          <span className="analysis-vs">vs</span>
        )}
        <span className="gc-team">{data.away}</span>
      </div>

      <div className="gc-header-meta">
        {data.competition && <span>{data.competition}</span>}
        {kickoff && (
          <span>
            {kickoff.toLocaleString('en-GB', {
              weekday: 'short', day: 'numeric', month: 'short',
              hour: '2-digit', minute: '2-digit',
            })}
          </span>
        )}
        {/* Where the clock came from is not a detail. A minute taken from PM's
            listed start time runs ~30 min ahead on smaller leagues, which is
            what invalidated 73k of our own observations — so the page says
            which source it is standing on. */}
        <span className={live?.clockSource ? 'gc-verified' : 'gc-unverified'}>
          {live?.clockSource ? 'clock: api-football' : 'clock: unverified'}
        </span>
      </div>

      {live?.stats && (
        <div className="gc-stats">
          <StatRow label="xG" h={live.stats.homeXg?.toFixed(2)} a={live.stats.awayXg?.toFixed(2)} />
          <StatRow label="Shots on target" h={live.stats.homeShotsOn} a={live.stats.awayShotsOn} />
          <StatRow label="Total shots" h={live.stats.homeShotsTotal} a={live.stats.awayShotsTotal} />
          <StatRow label="Corners" h={live.stats.homeCorners} a={live.stats.awayCorners} />
          <StatRow
            label="Possession"
            h={live.stats.homePossession != null ? `${live.stats.homePossession}%` : null}
            a={live.stats.awayPossession != null ? `${live.stats.awayPossession}%` : null}
          />
          {(live.stats.homeReds > 0 || live.stats.awayReds > 0) && (
            <StatRow label="Red cards" h={live.stats.homeReds} a={live.stats.awayReds} />
          )}
        </div>
      )}
    </div>
  )
}

function StatRow({
  label, h, a,
}: {
  label: string
  h: string | number | null | undefined
  a: string | number | null | undefined
}) {
  return (
    <div className="gc-stat-row">
      <span className="gc-stat-h">{h ?? '—'}</span>
      <span className="gc-stat-label">{label}</span>
      <span className="gc-stat-a">{a ?? '—'}</span>
    </div>
  )
}

// ── markets ──────────────────────────────────────────────────────────────────

function OutcomeCell({ o }: { o: Outcome }) {
  const ask = o.book?.ask ?? null
  const bid = o.book?.bid ?? null
  const spreadPp = ask != null && bid != null ? (ask - bid) * 100 : null

  return (
    <div className="gc-outcome">
      <span className="gc-outcome-name">{o.name}</span>
      <span className="gc-outcome-price">
        {/* The ask is what you would actually pay. The Gamma mid is shown only
            when there is no book, and labelled as such. */}
        {ask != null ? (
          <>
            <b className={isSettled(ask) ? 'gc-mid' : undefined}>{odds(ask)}</b>
            <span className="gc-outcome-pct">{pct(ask)}</span>
          </>
        ) : (
          <>
            <b className="gc-mid">{odds(o.price)}</b>
            <span className="gc-outcome-pct">
              {pct(o.price)}{isSettled(o.price) ? '' : ' mid'}
            </span>
          </>
        )}
      </span>
      {spreadPp != null && (
        <span className="gc-outcome-book">
          {spreadPp.toFixed(1)}pp wide · {money(o.book?.askDepthUsd)} at ask
        </span>
      )}
    </div>
  )
}

function MarketBlock({ g }: { g: MarketGroup }) {
  return (
    <div className="gc-market">
      <div className="gc-market-head">
        <span className="gc-market-q">{g.question}</span>
        {g.volume != null && <span className="gc-market-vol">{money(g.volume)} vol</span>}
      </div>
      <div className="gc-outcomes">
        {g.outcomes.map((o, i) => (
          <OutcomeCell key={i} o={o} />
        ))}
      </div>
    </div>
  )
}

function MarketsSection({ groups }: { groups: MarketGroup[] }) {
  const [query, setQuery] = useState('')
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})

  const filtered = query.trim()
    ? groups.filter((g) => g.question.toLowerCase().includes(query.trim().toLowerCase()))
    : groups

  const byGroup = filtered.reduce<Record<string, MarketGroup[]>>((acc, g) => {
    ;(acc[g.group] ??= []).push(g)
    return acc
  }, {})

  return (
    <section className="gc-section">
      <div className="gc-section-head">
        <h3 className="scan-group-title">MARKETS ({groups.length})</h3>
        <input
          className="gc-search"
          placeholder="filter markets…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {Object.entries(byGroup).map(([name, list]) => {
        // The first group is open by default; the rest collapse, because a
        // fixture board runs to 85 markets and an open wall of them is unusable.
        const isOpen = openGroups[name] ?? name === Object.keys(byGroup)[0]
        return (
          <div key={name} className="gc-group">
            <button
              className="gc-group-head"
              onClick={() => setOpenGroups((s) => ({ ...s, [name]: !isOpen }))}
            >
              <span>{isOpen ? '▾' : '▸'} {name}</span>
              <span className="gc-group-count">{list.length}</span>
            </button>
            {isOpen && list.map((g, i) => <MarketBlock key={i} g={g} />)}
          </div>
        )
      })}

      {filtered.length === 0 && <div className="scan-no-edge">No markets match “{query}”.</div>}
    </section>
  )
}

// ── Kalshi ───────────────────────────────────────────────────────────────────

function KalshiSection({ data }: { data: GameData }) {
  if (!data.kalshi) return null
  const k = data.kalshi
  return (
    <section className="gc-section">
      <h3 className="scan-group-title">ALSO ON KALSHI</h3>
      <div className="gc-kalshi">
        {k.sides.map((s, i) => (
          <div key={i} className="gc-kalshi-side">
            <span className="gc-outcome-name">{s.name}</span>
            <span className="gc-outcome-price">
              <b>{odds(s.ask)}</b>
              <span className="gc-outcome-pct">{pct(s.ask)}</span>
            </span>
          </div>
        ))}
      </div>
      {k.bestNetPp != null && (
        <p className="gc-note">
          Best price difference across the two venues, after both taker fees:{' '}
          <b className={k.bestNetPp > 0 ? 'c-green' : 'c-red'}>
            {k.bestNetPp > 0 ? '+' : ''}{k.bestNetPp.toFixed(2)}pp
          </b>
          . Kalshi&apos;s taker fee is 40% higher than Polymarket&apos;s, and across 57
          fixtures quoted on both venues we found <b>zero</b> net arbitrages — the gross
          ceiling is one tick against a ~3pp fee bar. This is a price comparison, not a trade.
        </p>
      )}
    </section>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function GamePage({ params }: { params: { slug: string } }) {
  const [data, setData] = useState<GameData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [watched, setWatched] = useState(false)

  const slug = params.slug

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/game?slug=${encodeURIComponent(slug)}`)
      const body = await res.json()
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`)
      setData(body)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => {
    load()
  }, [load])

  // Refresh while the match is live. 30s matches how long the live feed is
  // cached server-side, so a faster poll would only re-serve the same numbers.
  useEffect(() => {
    if (!data?.live) return
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [data?.live, load])

  useEffect(() => {
    try {
      const list = JSON.parse(localStorage.getItem(WATCHLIST_KEY) || '[]') as string[]
      setWatched(list.includes(slug))
    } catch {
      /* a corrupt watchlist is not worth a broken page */
    }
  }, [slug])

  function toggleWatch() {
    try {
      const list = JSON.parse(localStorage.getItem(WATCHLIST_KEY) || '[]') as string[]
      const next = list.includes(slug) ? list.filter((s) => s !== slug) : [...list, slug]
      localStorage.setItem(WATCHLIST_KEY, JSON.stringify(next))
      setWatched(next.includes(slug))
    } catch {
      /* ignore */
    }
  }

  const navigateHome = (s: Section) => {
    window.location.href = s === 'home' ? '/' : `/?section=${s}`
  }

  return (
    <div className="scanner-page">
      <Nav section="home" setSection={navigateHome} />

      <main className="scanner-main">
        {loading && <div className="gc-loading"><span className="scan-spinner" /> loading fixture…</div>}
        {error && <div className="scan-error">Error: {error}</div>}

        {data && (
          <>
            <LiveHeader data={data} />

            <div className="gc-actions">
              <button className="gc-action" onClick={toggleWatch}>
                {watched ? '★ In watchlist' : '☆ Add to watchlist'}
              </button>
              <a className="gc-action" href={data.pmUrl} target="_blank" rel="noopener noreferrer">
                Open on Polymarket ↗
              </a>
              {data.kalshi && (
                <a className="gc-action" href={data.kalshi.url} target="_blank" rel="noopener noreferrer">
                  Open on Kalshi ↗
                </a>
              )}
            </div>

            {data.history && data.history.points.length > 1 && (
              <section className="gc-section">
                <h3 className="scan-group-title">PRICE — {data.history.label}</h3>
                <Sparkline points={data.history.points} />
              </section>
            )}

            <MarketsSection groups={data.groups} />
            <KalshiSection data={data} />

            {data.notes.length > 0 && (
              <section className="gc-section">
                <h3 className="scan-group-title">WHAT THIS PAGE CANNOT TELL YOU</h3>
                <ul className="gc-notes">
                  {data.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                  <li>
                    No sportsbook column: our sharp benchmark is Pinnacle and Betfair, and that
                    feed is out of quota. Prices here are Polymarket&apos;s and Kalshi&apos;s own.
                  </li>
                  <li>
                    No model edge is shown. On 6 of 6 outcome groups Polymarket&apos;s price beat
                    our model on Brier score, so a &quot;model says X is cheap&quot; badge would
                    be selling something we measured as not working.
                  </li>
                </ul>
              </section>
            )}
          </>
        )}
      </main>

      <footer className="scanner-footer">
        <span>NOPREDICTIONS</span>
        <span style={{ color: 'var(--grey)' }}>No predictions. Just edges.</span>
      </footer>

      <MobileNav section="home" setSection={navigateHome} />
    </div>
  )
}
