'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppShell } from '../../components/AppShell'
import type {
  GameData,
  Headline,
  LiveStats,
  MarketGroup,
  PricePoint,
} from '../../lib/gamecenter'
import { tradeable, type Look, type Pulse } from '../../lib/looks'
import { useSession } from '../../lib/useSession'
import { useWatchlist } from '../../lib/useWatchlist'

// The user thinks in decimal odds, so every probability on this page carries the
// price. Outside this band the decimal stops describing a bet anyone would
// place — an outcome at 0.001 renders as 1000.00 — so those are labelled.
const SETTLED_BAND = 0.01

function odds(p: number | null | undefined): string {
  if (p == null || p <= 0 || p >= 1) return '—'
  if (p <= SETTLED_BAND) return 'settled ✗'
  if (p >= 1 - SETTLED_BAND) return 'settled ✓'
  return (1 / p).toFixed(2)
}

function pct(p: number | null | undefined): string {
  return p == null ? '—' : `${(p * 100).toFixed(0)}%`
}

function money(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}k`
  return `$${v.toFixed(0)}`
}

function signed(pp: number | null | undefined): string {
  return pp == null ? '—' : `${pp > 0 ? '+' : ''}${pp.toFixed(1)}pp`
}

// ── the match, big ───────────────────────────────────────────────────────────
//
// Everything a companion needs in the three seconds you look away from the
// game: who is playing, what the score is, what minute it is.

function MatchHero({ data }: { data: GameData }) {
  const live = data.live
  const board = data.board
  const kickoff = data.kickoff ? new Date(data.kickoff) : null

  // The live feed wins when it is there; the board carries the score when it is
  // not, which is most of the time on smaller competitions.
  const score = live
    ? { h: live.homeGoals, a: live.awayGoals, minute: live.minute }
    : board.homeGoals != null && board.awayGoals != null
      ? { h: board.homeGoals, a: board.awayGoals, minute: null }
      : null
  const inPlay = !!live || board.phase === 'live'
  const done = board.phase === 'finished'

  return (
    <header className={`gc-hero${inPlay && !done ? ' is-live' : ''}`}>
      <div className="gc-hero-top">
        <span className="gc-hero-comp">{data.competition ?? 'Football'}</span>
        {done ? (
          <span className="np-badge">FULL TIME</span>
        ) : inPlay ? (
          <span className="np-badge is-live">
            ● LIVE
            {score?.minute != null && <span className="gc-hero-min">{score.minute}&apos;</span>}
          </span>
        ) : kickoff ? (
          <span className="gc-hero-ko np-num">
            {kickoff.toLocaleString([], {
              weekday: 'short', hour: '2-digit', minute: '2-digit',
            })}
          </span>
        ) : null}
      </div>

      <div className="gc-hero-teams">
        <span className="gc-hero-team">{data.home}</span>
        {score ? (
          <span className="gc-hero-score np-num">
            {score.h}<i>–</i>{score.a}
          </span>
        ) : (
          <span className="gc-hero-v">v</span>
        )}
        <span className="gc-hero-team gc-hero-team-away">{data.away}</span>
      </div>

      {/* When there is no clock, say so rather than showing a minute we do not
          have. Polymarket's listed start ran half an hour early on some leagues
          and eight hours late on others, so it is never used as a substitute. */}
      {inPlay && !done && score?.minute == null && (
        <div className="gc-hero-noclock" title={board.evidence ?? undefined}>
          in play · no verified clock for this competition
        </div>
      )}
    </header>
  )
}

// ── the prices, as tiles ─────────────────────────────────────────────────────

function OddsTiles({ headlines }: { headlines: Headline[] }) {
  if (headlines.length === 0) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">The main markets</h2>
      <div className="gc-tiles">
        {headlines.map((h) => (
          <div key={h.label} className={`gc-tile${h.isMid ? ' is-mid' : ''}`}>
            <span className="gc-tile-k">{h.label}</span>
            <span className="gc-tile-v np-num">{h.odds ? h.odds.toFixed(2) : '—'}</span>
            <span className="gc-tile-sub">
              {h.isMid ? (
                <span className="gc-tile-warn" title="No order book — this is a Gamma mid, a number rather than a price you can pay.">
                  no book
                </span>
              ) : (
                <>
                  <span className="np-num">{pct(h.prob)}</span>
                  {h.spreadPp != null && (
                    <span className="np-num gc-tile-spread"> · {h.spreadPp.toFixed(1)}pp</span>
                  )}
                </>
              )}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

// ── the chart ────────────────────────────────────────────────────────────────

const SERIES_COLOUR = ['#22c55e', '#8b94a3', '#3b82f6', '#f59e0b', '#a855f7']

/** Decimal odds on a log axis.
 *
 *  Probability would give a bounded, easier axis, but this site quotes decimals
 *  everywhere and switching units inside one page is how a reader misreads a
 *  chart. Log spacing is what makes 1.20 and 12.00 share an axis at all; a
 *  linear one flattens every short price into the same line.
 *
 *  A rising line is a price DRIFTING — the outcome getting less likely. */
function PriceChart({
  series,
  headlines,
}: {
  series: Array<{ label: string; points: PricePoint[] }>
  headlines: Headline[]
}) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())

  const shown = series.filter((s) => !hidden.has(s.label))

  const geom = useMemo(() => {
    const W = 900
    const H = 240
    const PAD_L = 48
    const PAD_R = 8
    const PAD_T = 10
    const PAD_B = 20

    // Only points inside the tradeable band: an outcome at 0.004 renders as
    // 250.00 and would own the whole axis on its own.
    const pts = shown.flatMap((s) => s.points.filter((p) => p.p > 0.02 && p.p < 0.98))
    if (pts.length < 2) return null

    const ts = pts.map((p) => p.t)
    const t0 = Math.min(...ts)
    const t1 = Math.max(...ts)
    const odds = pts.map((p) => 1 / p.p)
    const lo = Math.max(1.01, Math.min(...odds) * 0.93)
    const hi = Math.min(60, Math.max(...odds) * 1.07)

    const x = (t: number) => PAD_L + ((t - t0) / (t1 - t0 || 1)) * (W - PAD_L - PAD_R)
    const y = (o: number) =>
      PAD_T +
      (1 - (Math.log(o) - Math.log(lo)) / (Math.log(hi) - Math.log(lo) || 1)) * (H - PAD_T - PAD_B)

    const lines = shown.map((s, i) => {
      const p = s.points
        .filter((q) => q.p > 0.02 && q.p < 0.98)
        .map((q, j) => `${j === 0 ? 'M' : 'L'}${x(q.t).toFixed(1)},${y(1 / q.p).toFixed(1)}`)
        .join(' ')
      const last = s.points[s.points.length - 1]
      return {
        label: s.label,
        d: p,
        colour: SERIES_COLOUR[series.findIndex((z) => z.label === s.label) % SERIES_COLOUR.length],
        last: last && last.p > 0.01 && last.p < 0.99 ? 1 / last.p : null,
      }
    })

    // Four gridlines at round-ish odds inside the range.
    const ticks: { v: number; y: number }[] = []
    for (const v of [1.1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7, 10, 15, 25, 40]) {
      if (v >= lo && v <= hi) ticks.push({ v, y: y(v) })
    }

    return { W, H, PAD_L, lines, ticks, t0, t1 }
  }, [shown, series])

  if (series.length === 0) return null

  const hours = geom ? Math.round((geom.t1 - geom.t0) / 3600) : 0

  return (
    <section className="gc-section">
      <h2 className="gc-h2">
        Price · last {hours > 0 ? `${hours}h` : '24h'}
      </h2>

      {geom ? (
        <div className="gc-chart">
          <svg viewBox={`0 0 ${geom.W} ${geom.H}`} className="gc-chart-svg" role="img"
               aria-label="Decimal odds over time for this fixture's main markets">
            {geom.ticks.map((t) => (
              <g key={t.v}>
                <line x1={geom.PAD_L} y1={t.y} x2={geom.W - 8} y2={t.y} className="gc-chart-grid" />
                <text x={6} y={t.y + 5} className="gc-chart-tick">{t.v.toFixed(t.v < 10 ? 2 : 0)}</text>
              </g>
            ))}
            {geom.lines.map((l) => (
              <path key={l.label} d={l.d} fill="none" stroke={l.colour} strokeWidth={1.8}
                    strokeLinejoin="round" strokeLinecap="round" />
            ))}
          </svg>
        </div>
      ) : (
        <p className="gc-quiet">Not enough price history yet to draw this fixture.</p>
      )}

      <div className="gc-legend">
        {series.map((s, i) => {
          const off = hidden.has(s.label)
          // The same number the tile shows: the ask you could pay. The LINE is
          // the 5-minute history, which lags it and is a different thing.
          const o = headlines.find((h) => h.label === s.label)?.odds ?? null
          return (
            <button
              key={s.label}
              className={`gc-legend-item${off ? ' is-off' : ''}`}
              onClick={() =>
                setHidden((prev) => {
                  const next = new Set(prev)
                  if (next.has(s.label)) next.delete(s.label)
                  else next.add(s.label)
                  return next
                })
              }
            >
              <span className="gc-legend-dot" style={{ background: SERIES_COLOUR[i % SERIES_COLOUR.length] }} />
              {s.label}
              {o && <b className="np-num">{o.toFixed(2)}</b>}
            </button>
          )
        })}
      </div>
      <p className="gc-chart-note">
        A line going up is the price drifting — that outcome getting longer. The figure beside
        each market is the price you could pay right now; the line is Polymarket&apos;s own
        5-minute history, so it lags. Click a market to hide it.
      </p>
    </section>
  )
}

// ── what the game looks like ─────────────────────────────────────────────────

function Momentum({ s, home, away }: { s: LiveStats; home: string; away: string }) {
  const rows: { label: string; h: number; a: number; fmt?: (v: number) => string }[] = [
    { label: 'Possession', h: s.homePossession ?? 0, a: s.awayPossession ?? 0, fmt: (v) => `${v}%` },
    { label: 'Shots', h: s.homeShotsTotal, a: s.awayShotsTotal },
    { label: 'On target', h: s.homeShotsOn, a: s.awayShotsOn },
    { label: 'Corners', h: s.homeCorners, a: s.awayCorners },
  ]
  if (s.homeXg != null && s.awayXg != null) {
    rows.splice(1, 0, { label: 'xG', h: s.homeXg, a: s.awayXg, fmt: (v) => v.toFixed(2) })
  }

  const short = (t: string) => t.split(/\s+/).slice(0, 2).join(' ')

  return (
    <section className="gc-section">
      <h2 className="gc-h2">How it is going</h2>
      {/* The rails are two colours and nothing else says which is which, so the
          names carry the key. */}
      <div className="gc-mom-head">
        <span className="gc-mom-home">{short(home)}</span>
        <span className="gc-mom-away">{short(away)}</span>
      </div>
      <div className="gc-mom">
        {rows.map((r) => {
          const total = r.h + r.a
          // A 0-0 split renders as an empty rail rather than as a 50/50 bar that
          // claims a balance nothing has established yet.
          const hp = total > 0 ? (r.h / total) * 100 : 0
          const fmt = r.fmt ?? ((v: number) => String(v))
          return (
            <div key={r.label} className="gc-mom-row">
              <span className="gc-mom-v np-num">{fmt(r.h)}</span>
              <div className="gc-mom-mid">
                <span className="gc-mom-k">{r.label}</span>
                <div className="gc-mom-rail">
                  <span className="gc-mom-fill" style={{ width: `${hp}%` }} />
                </div>
              </div>
              <span className="gc-mom-v gc-mom-v-away np-num">{fmt(r.a)}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── the last twenty minutes ──────────────────────────────────────────────────

function Pulse({ pulse }: { pulse: Pulse[] }) {
  if (pulse.length === 0) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Moved in the last 20 minutes</h2>
      <ul className="gc-pulse-list">
        {pulse.slice(0, 5).map((p, i) => (
          <li key={i} className={p.movePp > 0 ? 'gc-pos' : 'gc-neg'}>
            <span className="gc-pulse-move np-num">{signed(p.movePp)}</span>
            <span className="gc-pulse-what">
              {p.question.split(':').pop()?.trim()} — {p.outcome}
            </span>
            <span className="gc-pulse-price np-num">
              {odds(p.from)} → {odds(p.to)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

const CALL_LABEL: Record<Look['call'], string> = {
  back: 'CHEAP',
  fair: 'FAIR',
  rich: 'EXPENSIVE',
}

function LookRow({ look }: { look: Look }) {
  const [open, setOpen] = useState(false)
  const can = tradeable(look)

  return (
    <li className={`gc-look gc-look-${look.call}${can ? '' : ' gc-look-untradeable'}`}>
      <div className="gc-look-top">
        <span className={`gc-call gc-call-${look.call}`}>{CALL_LABEL[look.call]}</span>
        <span className="gc-look-side">{look.side}</span>
        <span className="gc-look-odds">
          {look.odds.toFixed(2)}
          <i>{pct(look.prob)}</i>
        </span>
      </div>

      <div className="gc-look-nums">
        <span>
          <em>measured</em>
          <b>{look.fairOdds ? look.fairOdds.toFixed(2) : '—'}</b>
          <i>{pct(look.fairProb)}</i>
        </span>
        <span className={look.edgePp != null && look.edgePp > 0 ? 'gc-pos' : 'gc-neg'}>
          <em>edge after fee</em>
          <b>{signed(look.edgePp)}</b>
          <i>fee {look.feePp.toFixed(2)}pp</i>
        </span>
        <span>
          <em>available</em>
          <b>{look.isMid ? 'no book' : money(look.depthUsd)}</b>
          <i>{look.spreadPp != null ? `${look.spreadPp.toFixed(1)}pp wide` : 'mid only'}</i>
        </span>
      </div>

      <p className="gc-look-why">{look.why}</p>

      <div className="gc-look-flags">
        {look.n != null && <span className="gc-flag gc-flag-ok">measured · n={look.n.toLocaleString()}</span>}
        {look.imported && <span className="gc-flag gc-flag-warn">imported rate</span>}
        {look.isMid && <span className="gc-flag gc-flag-warn">not executable</span>}
        {!look.isMid && look.depthUsd != null && look.depthUsd < 100 && (
          <span className="gc-flag gc-flag-warn">thin</span>
        )}
        <button className="gc-why" onClick={() => setOpen((v) => !v)}>
          {open ? 'hide detail' : 'where this comes from'}
        </button>
      </div>

      {open && (
        <ul className="gc-notes">
          {look.detail.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
          <li>{look.market}</li>
        </ul>
      )}
    </li>
  )
}

function Looks({ data }: { data: GameData }) {
  const looks = data.looks ?? []
  // A price you cannot pay is not a look. Those stay collapsed rather than
  // ranking alongside prices with a real book behind them.
  const live = looks.filter(tradeable)
  const mids = looks.filter((l) => !tradeable(l))
  const backs = live.filter((l) => l.call === 'back')
  const [showMids, setShowMids] = useState(false)

  return (
    <section className="gc-section">
      <h2 className="gc-h2">Worth a look</h2>

      {live.length === 0 ? (
        <div className="gc-nothing">
          <strong>No measured read on this fixture yet.</strong>
          <span>{NO_READ_REASON(data)}</span>
        </div>
      ) : (
        <>
          {backs.length === 0 && (
            <div className="gc-verdict">
              <strong>Nothing is cheap right now.</strong>
              <span>
                Every price below sits inside the taker fee of the rate we measured. That is the
                normal state of this market — the prices are shown anyway, because &quot;the market has
                this right&quot; is the answer to most questions worth asking mid-match.
              </span>
            </div>
          )}
          {/* Shown whether or not anything is cheap: the comparison IS the
              product. A bettor watching wants "is 1.23 too short?" answered in
              two seconds, and the answer is usually no. */}
          <ul className="gc-looks">
            {live.map((l) => (
              <LookRow key={l.id} look={l} />
            ))}
          </ul>
        </>
      )}

      {mids.length > 0 && (
        <>
          <button className="gc-more" onClick={() => setShowMids((v) => !v)}>
            {showMids
              ? 'hide markets with no order book'
              : `${mids.length} more measured, but with no order book to trade against`}
          </button>
          {showMids && (
            <ul className="gc-looks gc-looks-quiet">
              {mids.map((l) => (
                <LookRow key={l.id} look={l} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

/** Why the shortlist is empty. A companion that goes blank without saying why
 *  is worse than one that says "not yet". */
function NO_READ_REASON(data: GameData): string {
  if (data.board.phase === 'finished') return 'This match is over.'
  if (data.board.phase !== 'live') {
    return (
      'The measured tables are conditioned on a live state — a minute and a score — so they ' +
      'have nothing to say before kick-off. Pre-match, Polymarket sits within 0.10pp of the ' +
      'de-vigged Pinnacle line, which is another way of saying there is nothing here.'
    )
  }
  if (!data.live) {
    return (
      'The match is live but no clock matched it: api-football did not have this fixture in ' +
      'its live feed, and every measured rate here is keyed on the minute. Polymarket\'s own ' +
      'listed start time is not used as a substitute — it ran half an hour early on some ' +
      'leagues and eight hours late on others.'
    )
  }
  if (data.live.minute != null && data.live.minute < 10) {
    return 'Too early — the measured tables start at 10 minutes.'
  }
  return 'Polymarket is not quoting the rungs the measured tables price.'
}

// ── the board, tradeable first ───────────────────────────────────────────────

interface BoardRow {
  question: string
  outcome: string
  ask: number
  spreadPp: number
  depthUsd: number | null
}

function Board({ groups }: { groups: MarketGroup[] }) {
  const [open, setOpen] = useState(false)

  const { rows, quoted } = useMemo(() => {
    const rows: BoardRow[] = []
    let quoted = 0
    for (const g of groups) {
      for (const o of g.outcomes) {
        quoted++
        const b = o.book
        if (b?.ask == null || b?.bid == null) continue
        if (b.ask <= SETTLED_BAND || b.ask >= 1 - SETTLED_BAND) continue
        rows.push({
          question: g.question,
          outcome: o.name,
          ask: b.ask,
          spreadPp: (b.ask - b.bid) * 100,
          depthUsd: b.askDepthUsd,
        })
      }
    }
    rows.sort((a, b) => a.spreadPp - b.spreadPp || (b.depthUsd ?? 0) - (a.depthUsd ?? 0))
    return { rows, quoted }
  }, [groups])

  return (
    <section className="gc-section">
      <h2 className="gc-h2">The board</h2>
      <p className="gc-quiet">
        {rows.length} of {quoted} quoted outcomes have a real order book. The rest are Gamma
        mids — a number, not a price you can pay.
      </p>

      <table className="gc-board">
        <thead>
          <tr>
            <th>Market</th>
            <th>Side</th>
            <th className="gc-r">Ask</th>
            <th className="gc-r">Spread</th>
            <th className="gc-r">Depth</th>
          </tr>
        </thead>
        <tbody>
          {(open ? rows : rows.slice(0, 8)).map((r, i) => (
            <tr key={i}>
              <td className="gc-board-q">{r.question.split(':').pop()?.trim()}</td>
              <td>{r.outcome}</td>
              <td className="gc-r gc-mono">{odds(r.ask)}</td>
              <td className="gc-r gc-mono">{r.spreadPp.toFixed(1)}pp</td>
              <td className="gc-r gc-mono">{money(r.depthUsd)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length > 8 && (
        <button className="gc-more" onClick={() => setOpen((v) => !v)}>
          {open ? 'show fewer' : `show all ${rows.length}`}
        </button>
      )}
    </section>
  )
}

// ── the small print, behind a click ──────────────────────────────────────────

function Disclosure({ data }: { data: GameData }) {
  const [open, setOpen] = useState(false)

  return (
    <section className="gc-section">
      <button className="gc-more" onClick={() => setOpen((v) => !v)}>
        {open ? 'hide' : 'what this page cannot tell you'}
      </button>
      {open && (
        <ul className="gc-notes">
          <li>
            <strong>This is not a tip.</strong> Every number here is either a price Polymarket
            is quoting right now or a rate measured on historical matches. Nothing on this page
            is a prediction, and a measured rate is not a guarantee about this match.
          </li>
          <li>
            <strong>No model output, deliberately.</strong> On 6 of 6 outcome groups Polymarket&apos;s
            price beat our own model on Brier score, so a &quot;the model likes this&quot; badge would be
            selling the one thing we measured as not working.
          </li>
          <li>
            <strong>Prices are asks, not mids.</strong> The mid is the number that made a paper
            strategy book +141% where the same 15 decisions returned +3.4% live.
          </li>
          <li>
            <strong>Edges are shown after Polymarket&apos;s taker fee</strong> — shares × 0.05 × p ×
            (1−p), verified to 0.0001% on 87,000 real fills. It peaks at 1.25pp around even money.
          </li>
          {data.board.evidence && (
            <li>
              <strong>Score source:</strong> {data.live ? 'api-football live feed' : data.board.evidence}.
            </li>
          )}
          {data.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
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
  const slug = params.slug

  // ⚠️ This page used to read and write `np_watchlist` in localStorage itself.
  //    It therefore agreed with the board by accident and not by design — and
  //    once Pro gained a synced watchlist, a star added here stayed on this
  //    device while one added on the board followed the account. One hook, one
  //    list.
  const { me } = useSession()
  const { slugs, toggle } = useWatchlist(me?.plan === 'pro')
  const watched = slugs.includes(slug)

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
  const isLive = data?.board.phase === 'live'
  useEffect(() => {
    if (!isLive) return
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [isLive, load])


  return (
    <AppShell>
      <div className="gc-main">
        {loading && (
          <div className="gc-loading">
            <span className="scan-spinner" /> loading fixture…
          </div>
        )}
        {error && <div className="scan-error">Error: {error}</div>}

        {data && (
          <>
            <MatchHero data={data} />
            <OddsTiles headlines={data.headlines ?? []} />
            <PriceChart series={data.series ?? []} headlines={data.headlines ?? []} />
            {data.live?.stats && (
              <Momentum s={data.live.stats} home={data.home} away={data.away} />
            )}
            <Pulse pulse={data.pulse ?? []} />
            <Looks data={data} />
            <Board groups={data.groups} />

            <div className="gc-actions">
              <button
                className="gc-action"
                onClick={() => toggle(slug, { home: data.home, away: data.away })}
              >
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

            <Disclosure data={data} />
          </>
        )}
      </div>
    </AppShell>
  )
}
