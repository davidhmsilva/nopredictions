'use client'

import { useCallback, useEffect, useState } from 'react'
import { Nav, MobileNav } from '../../components/Nav'
import type { Section } from '../../lib/types'
import type {
  GameData, Headline, MarketGroup, Mover, Outcome, PricePoint, WatchCard,
} from '../../lib/gamecenter'

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

// ── header ───────────────────────────────────────────────────────────────────

function GameHeader({ data }: { data: GameData }) {
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
    </div>
  )
}

// ── what happened before you got here ────────────────────────────────────────

// A double-digit move on a fixture's main market is usually team news, and it
// is the first thing a reader needs — but it lives below the fold in MOVEMENT,
// so anything this big gets a line at the top as well.
const BIG_MOVE_PP = 10

function ContextStrip({ movers }: { movers: Mover[] }) {
  const m = movers[0]
  if (!m || Math.abs(m.movePp) < BIG_MOVE_PP) return null
  const shorter = m.movePp > 0
  return (
    <div className="gc-context">
      <span className="gc-context-tag">24H</span>
      <span>
        {m.question.replace(/\?$/, '').replace(/ on \d{4}-\d{2}-\d{2}/, '')} — {m.outcome}{' '}
        went <b>{odds(m.from)} → {odds(m.to)}</b>{' '}
        <span className={shorter ? 'c-green' : 'c-red'}>
          ({m.movePp > 0 ? '+' : ''}{m.movePp.toFixed(1)}pp)
        </span>
        . Polymarket repriced this fixture hard; whatever the news was, the board already has it.
      </span>
    </div>
  )
}

// ── the watch card ───────────────────────────────────────────────────────────

function WatchBlock({ w }: { w: WatchCard }) {
  const [why, setWhy] = useState(false)
  // Green only when the price is BELOW the measured rate — the one direction in
  // which the market being watched is the cheap side.
  const cheap = w.gapPp != null && w.gapPp < 0

  return (
    <section className={`gc-watch gc-watch-${w.kind}`}>
      <div className="gc-watch-tag">WATCH · {w.title}</div>
      {w.state && <div className="gc-watch-state">{w.state}</div>}

      {w.market && (
        <div className="gc-watch-body">
          <div className="gc-watch-market">{w.market}</div>
          <div className="gc-watch-prices">
            {/* The Polymarket box appears only when there is a comparable quote.
                On a pre-match setup the price that matters does not exist yet,
                and filling the slot with today's number invites the wrong
                subtraction against the measured rate. */}
            {w.pmProb != null && (
              <div className="gc-watch-price">
                <span className="gc-watch-price-label">Polymarket</span>
                <b className={cheap ? 'c-green' : undefined}>{odds(w.pmProb)}</b>
                <span className="gc-outcome-pct">{pct(w.pmProb)}</span>
              </div>
            )}
            {w.fairProb != null && (
              <div className="gc-watch-price">
                <span className="gc-watch-price-label">Measured</span>
                <b>{(1 / w.fairProb).toFixed(2)}</b>
                <span className="gc-outcome-pct">
                  {pct(w.fairProb)}{w.n ? ` · n=${w.n.toLocaleString()}` : ''}
                </span>
              </div>
            )}
            {w.gapPp != null && (
              <div className="gc-watch-price">
                <span className="gc-watch-price-label">Gap</span>
                <b className={cheap ? 'c-green' : 'c-red'}>
                  {w.gapPp > 0 ? '+' : ''}{w.gapPp.toFixed(1)}pp
                </b>
                <span className="gc-outcome-pct">
                  {w.feePp != null ? `fee ${w.feePp.toFixed(2)}pp` : ''}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      <p className="gc-watch-verdict">{w.verdict}</p>

      {w.caveats.length > 0 && (
        <>
          <button className="gc-why" onClick={() => setWhy((v) => !v)}>
            {why ? '▾' : '▸'} where this number comes from
          </button>
          {why && (
            <ul className="gc-notes">
              {w.caveats.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

// ── the five numbers ─────────────────────────────────────────────────────────

function Headlines({ items }: { items: Headline[] }) {
  if (!items.length) return null
  return (
    <section className="gc-section">
      <h3 className="scan-group-title">THE MARKET</h3>
      <div className="gc-headlines">
        {items.map((h, i) => (
          <div key={i} className="gc-headline">
            <span className="gc-headline-label">{h.label}</span>
            <b className={h.isMid ? 'gc-mid' : undefined}>{odds(h.prob)}</b>
            <span className="gc-outcome-pct">{pct(h.prob)}{h.isMid ? ' mid' : ''}</span>
            {h.spreadPp != null && (
              <span className="gc-outcome-book">
                {h.spreadPp.toFixed(1)}pp wide · {money(h.depthUsd)} at ask
              </span>
            )}
          </div>
        ))}
      </div>
      <p className="gc-note">
        Prices are the <b>ask</b> — what you would pay, not the mid. The mid is the number that
        made a paper strategy book +141% where the same 15 decisions returned +3.4% live.
      </p>
    </section>
  )
}

// ── movement ─────────────────────────────────────────────────────────────────

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
        {/* Decimal on both ends, because a move from 2.13 to 1.27 is the thing
            that happened; "47% → 79%" is the same fact in a unit nobody bets in. */}
        <span>{odds(first)} → {odds(last)}</span>
        <span className={up ? 'c-green' : 'c-red'}>
          {movePp > 0 ? '+' : ''}{movePp.toFixed(1)}pp / 24h
        </span>
      </div>
    </div>
  )
}

function Movement({ data }: { data: GameData }) {
  if (!data.history || data.history.points.length < 2) return null
  return (
    <section className="gc-section">
      <h3 className="scan-group-title">MOVEMENT — {data.history.label}</h3>
      <Sparkline points={data.history.points} />
      {data.movers.length > 0 && (
        <div className="gc-movers">
          {data.movers.map((m: Mover, i) => (
            <div key={i} className="gc-mover">
              <span className="gc-mover-q">{m.question} — {m.outcome}</span>
              <span className="gc-mover-move">
                {odds(m.from)} → <b>{odds(m.to)}</b>{' '}
                <span className={m.movePp > 0 ? 'c-green' : 'c-red'}>
                  ({m.movePp > 0 ? '+' : ''}{m.movePp.toFixed(1)}pp)
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// ── the full board, out of the way ───────────────────────────────────────────

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

/** The whole board, collapsed.
 *
 *  It used to open on load, and 65 markets is not context — it is the same wall
 *  of prices Polymarket already shows, with our styling on it. Anyone who wants
 *  the board is one click away; everyone else gets the fixture. */
function AllMarkets({ groups }: { groups: MarketGroup[] }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})

  const filtered = query.trim()
    ? groups.filter((g) => g.question.toLowerCase().includes(query.trim().toLowerCase()))
    : groups

  const byGroup = filtered.reduce<Record<string, MarketGroup[]>>((acc, g) => {
    ;(acc[g.group] ??= []).push(g)
    return acc
  }, {})

  if (!groups.length) return null

  return (
    <section className="gc-section">
      <button className="gc-board-toggle" onClick={() => setOpen((v) => !v)}>
        <span>{open ? '▾' : '▸'} ALL MARKETS</span>
        <span className="gc-group-count">{groups.length}</span>
      </button>

      {open && (
        <>
          <input
            className="gc-search gc-search-wide"
            placeholder="filter markets…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {Object.entries(byGroup).map(([name, list]) => {
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
          {filtered.length === 0 && (
            <div className="scan-no-edge">No markets match “{query}”.</div>
          )}
        </>
      )}
    </section>
  )
}

// ── Kalshi + caveats ─────────────────────────────────────────────────────────

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

function Caveats({ data }: { data: GameData }) {
  const [open, setOpen] = useState(false)
  return (
    <section className="gc-section">
      <button className="gc-board-toggle" onClick={() => setOpen((v) => !v)}>
        <span>{open ? '▾' : '▸'} WHAT THIS PAGE CANNOT TELL YOU</span>
      </button>
      {open && (
        <ul className="gc-notes">
          {data.notes.map((n, i) => <li key={i}>{n}</li>)}
          <li>
            No sportsbook column: our sharp benchmark is Pinnacle and Betfair, and that feed is
            out of quota. Prices here are Polymarket&apos;s and Kalshi&apos;s own.
          </li>
          <li>
            No model edge is shown anywhere. On 6 of 6 outcome groups Polymarket&apos;s price beat
            our model on Brier score, so a &quot;model says X is cheap&quot; badge would be selling
            something we measured as not working. Every fair value on this page is a counted
            frequency, not a prediction.
          </li>
          <li>
            Pre-match, Polymarket&apos;s football price is fair at the bid — the round trip costs
            1.2-2.5pp and beats every entry signal we have tested. Edge has to come from
            settlement or from in-play moves much larger than the spread.
          </li>
        </ul>
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
            <GameHeader data={data} />
            <ContextStrip movers={data.movers} />
            <WatchBlock w={data.watch} />
            <Headlines items={data.headlines} />

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

            <Movement data={data} />
            <AllMarkets groups={data.groups} />
            <KalshiSection data={data} />
            <Caveats data={data} />
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
