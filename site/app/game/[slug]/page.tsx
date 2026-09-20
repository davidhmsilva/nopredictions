'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppShell } from '../../components/AppShell'
import type { GameData, Headline, MarketGroup, PricePoint } from '../../lib/gamecenter'
import { outcomeKey, pickFor, type KalshiGame, type KalshiQuoteRef } from '../../lib/kalshiGame'
import { netCost, type BestPick, type Quote } from '../../lib/venues'
import type { MatchContext } from '../../lib/matchcontext'
import { tradeable, type Look, type Pulse } from '../../lib/looks'
import { useSession } from '../../lib/useSession'
import { useWatchlist } from '../../lib/useWatchlist'
import { SETTLED_BAND, money, odds, oddsDec, pct, signed } from './fmt'
import { kickoffText, oddsText, useOddsFormat } from '../../lib/display'

function ordinal(n: number): string {
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'
  return `${n}${s}`
}
import { BriefCard, MomentumChart, PricedLikeCard, StreakHighlights } from './Insights'
import { MatchTab } from './MatchTab'
import { StatsTab } from './StatsTab'

// ── the match, big ───────────────────────────────────────────────────────────
//
// Everything a companion needs in the three seconds you look away from the
// game: who is playing, what the score is, what minute it is — and, before
// kick-off, how each side arrives.

function FormPills({ results }: { results: string[] }) {
  if (!results.length) return null
  return (
    <span className="gcx-hero-form" title="Last five, latest first">
      {results.map((r, i) => (
        <span key={i} className={`gcx-pill gcx-pill-${r}`}>{r}</span>
      ))}
    </span>
  )
}

function MatchHero({ data, ctx }: { data: GameData; ctx: MatchContext | null }) {
  const live = data.live
  const board = data.board
  const espn = ctx?.espn ?? null
  const kickoff = data.kickoff ? new Date(data.kickoff) : null

  // Order of trust for the score: api-football's live feed, then ESPN, then
  // what the board itself says. Never Polymarket's listed start time.
  const score = live
    ? { h: live.homeGoals, a: live.awayGoals, minute: live.minute != null ? `${live.minute}'` : null }
    : espn && espn.state !== 'pre' && espn.home.score != null && espn.away.score != null
      ? { h: espn.home.score, a: espn.away.score, minute: espn.clock }
      : board.homeGoals != null && board.awayGoals != null
        ? { h: board.homeGoals, a: board.awayGoals, minute: null }
        : null
  const done = board.phase === 'finished' || espn?.state === 'post'
  const inPlay = !done && (!!live || board.phase === 'live' || espn?.state === 'in')

  const formOf = (side: 'home' | 'away') => {
    const t = side === 'home' ? ctx?.teams?.home : ctx?.teams?.away
    if (t?.games.length) return t.games.slice(0, 5).map((g) => g.result)
    const e = side === 'home' ? espn?.home : espn?.away
    return e?.form.slice(0, 5).map((g) => g.result).filter((r) => /^[WDL]$/.test(r)) ?? []
  }
  const posOf = (side: 'home' | 'away') => espn?.table?.rows.find((r) => r.mark === side)?.rank ?? null
  const logo = (side: 'home' | 'away') => (side === 'home' ? espn?.home.logo : espn?.away.logo) ?? null

  const meta = [
    espn?.venue ? `${espn.venue}${espn.city ? `, ${espn.city}` : ''}` : null,
    espn?.referee ? `Referee ${espn.referee}` : null,
    espn?.broadcasts.length ? espn.broadcasts.slice(0, 2).join(' · ') : null,
  ].filter(Boolean)

  return (
    <header className={`gc-hero${inPlay ? ' is-live' : ''}`}>
      <div className="gc-hero-top">
        <span className="gc-hero-comp">{data.competition ?? 'Football'}</span>
        {done ? (
          <span className="np-badge">FULL TIME</span>
        ) : inPlay ? (
          <span className="np-badge is-live">
            ● LIVE
            {score?.minute && <span className="gc-hero-min">{score.minute}</span>}
          </span>
        ) : kickoff ? (
          <span className="gc-hero-ko np-num">
            {kickoffText(kickoff)}
          </span>
        ) : null}
      </div>

      <div className="gc-hero-teams">
        <span className="gc-hero-team">
          {logo('home') && <img className="gcx-logo" src={logo('home') as string} alt="" loading="lazy" />}
          <span>
            {data.home}
            <span className="gcx-hero-sub">
              {posOf('home') && <span className="gcx-hero-pos">{ordinal(posOf('home') as number)}</span>}
              <FormPills results={formOf('home')} />
            </span>
          </span>
        </span>
        {score ? (
          <span className="gc-hero-score np-num">
            {score.h}<i>–</i>{score.a}
          </span>
        ) : (
          <span className="gc-hero-v">v</span>
        )}
        <span className="gc-hero-team gc-hero-team-away">
          <span>
            {data.away}
            <span className="gcx-hero-sub">
              <FormPills results={formOf('away')} />
              {posOf('away') && <span className="gcx-hero-pos">{ordinal(posOf('away') as number)}</span>}
            </span>
          </span>
          {logo('away') && <img className="gcx-logo" src={logo('away') as string} alt="" loading="lazy" />}
        </span>
      </div>

      {espn && espn.home.halves.length > 1 && (
        <div className="gcx-hero-halves np-num">HT {espn.home.halves[0]}–{espn.away.halves[0]}</div>
      )}

      {/* When there is no clock, say so rather than showing a minute we do not
          have. Polymarket's listed start ran half an hour early on some leagues
          and eight hours late on others, so it is never used as a substitute. */}
      {inPlay && !score?.minute && (
        <div className="gc-hero-noclock" title={board.evidence ?? undefined}>
          in play · no verified clock for this competition
        </div>
      )}

      {meta.length > 0 && <div className="gcx-hero-meta">{meta.join('  ·  ')}</div>}
    </header>
  )
}

// ── the prices, as tiles ─────────────────────────────────────────────────────

function OddsTiles({ headlines }: { headlines: Headline[] }) {
  const oddsFmt = useOddsFormat()
  if (headlines.length === 0) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">The main markets</h2>
      <div className="gc-tiles">
        {headlines.map((h) => (
          <div key={h.label} className={`gc-tile${h.isMid ? ' is-mid' : ''}`}>
            <span className="gc-tile-k">{h.label}</span>
            <span className="gc-tile-v np-num">{oddsDec(h.odds, oddsFmt)}</span>
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
  const oddsFmt = useOddsFormat()

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
    const oddsV = pts.map((p) => 1 / p.p)
    const lo = Math.max(1.01, Math.min(...oddsV) * 0.93)
    const hi = Math.min(60, Math.max(...oddsV) * 1.07)

    const x = (t: number) => PAD_L + ((t - t0) / (t1 - t0 || 1)) * (W - PAD_L - PAD_R)
    const y = (o: number) =>
      PAD_T +
      (1 - (Math.log(o) - Math.log(lo)) / (Math.log(hi) - Math.log(lo) || 1)) * (H - PAD_T - PAD_B)

    const lines = shown.map((s) => {
      const p = s.points
        .filter((q) => q.p > 0.02 && q.p < 0.98)
        .map((q, j) => `${j === 0 ? 'M' : 'L'}${x(q.t).toFixed(1)},${y(1 / q.p).toFixed(1)}`)
        .join(' ')
      return {
        label: s.label,
        d: p,
        colour: SERIES_COLOUR[series.findIndex((z) => z.label === s.label) % SERIES_COLOUR.length],
      }
    })

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
      <h2 className="gc-h2">Price · last {hours > 0 ? `${hours}h` : '24h'}</h2>

      {geom ? (
        <div className="gc-chart">
          <svg viewBox={`0 0 ${geom.W} ${geom.H}`} className="gc-chart-svg" role="img"
               aria-label="Prices over time for this fixture's main markets">
            {geom.ticks.map((t) => (
              <g key={t.v}>
                <line x1={geom.PAD_L} y1={t.y} x2={geom.W - 8} y2={t.y} className="gc-chart-grid" />
                <text x={6} y={t.y + 5} className="gc-chart-tick">
                  {oddsFmt === 'decimal' ? t.v.toFixed(t.v < 10 ? 2 : 0) : oddsText(t.v, oddsFmt)}
                </text>
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
              {o && <b className="np-num">{oddsDec(o, oddsFmt)}</b>}
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

// ── the last twenty minutes ──────────────────────────────────────────────────

function PulseList({ pulse }: { pulse: Pulse[] }) {
  const oddsFmt = useOddsFormat()
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
              {odds(p.from, oddsFmt)} → {odds(p.to, oddsFmt)}
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
  const oddsFmt = useOddsFormat()
  const can = tradeable(look)

  return (
    <li className={`gc-look gc-look-${look.call}${can ? '' : ' gc-look-untradeable'}`}>
      <div className="gc-look-top">
        <span className={`gc-call gc-call-${look.call}`}>{CALL_LABEL[look.call]}</span>
        <span className="gc-look-side">{look.side}</span>
        <span className="gc-look-odds">
          {oddsDec(look.odds, oddsFmt)}
          <i>{pct(look.prob)}</i>
        </span>
      </div>

      <div className="gc-look-nums">
        <span>
          <em>measured</em>
          <b>{oddsDec(look.fairOdds, oddsFmt)}</b>
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
      'de-vigged Pinnacle line, which is another way of saying there is nothing here. The ' +
      '"priced like this" table on Overview is the pre-match comparison.'
    )
  }
  if (!data.live) {
    return (
      'The match is live but no published minute matched it: api-football did not have this ' +
      'fixture in its live feed and Polymarket is not sending a clock on this event either, ' +
      'and every measured rate here is keyed on the minute. Polymarket\'s listed START TIME is ' +
      'never used as a substitute — it ran half an hour early on some leagues and eight hours ' +
      'late on others.'
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
  /** Polymarket's executable quote for this outcome. */
  pm: Quote
  depthUsd: number | null
  /** Kalshi's price for the SAME bet, where it lists one. */
  k: KalshiQuoteRef | null
  /** Which venue is cheaper, net of each one's taker fee. Null when only one
   *  of them quotes it — there is nothing to be better than. */
  pick: BestPick | null
}

/** The board, both exchanges.
 *
 *  🔑 Polymarket on the left, Kalshi on the right, and the cheaper of the two
 *     marked — AFTER each venue's taker fee. That does not change who wins a
 *     price by a cent or more (see lib/venues), but it roughly halves what the
 *     win is worth and it decides a tie near even money, where Kalshi's
 *     40%-higher fee costs 0.5pp on its own.
 *
 *  ⚠️ An Under is bought as the NO leg of Kalshi's Over ticker. That is a real
 *     price, not a derived one — on a binary book, buying NO at 1 − yes_bid IS
 *     selling YES at the bid — but its DEPTH is the size resting on the yes
 *     bid, which the feed does not carry, so it is left unknown.
 */
function Board({ groups, kalshi }: { groups: MarketGroup[]; kalshi: KalshiGame | null }) {
  const [open, setOpen] = useState(false)
  const [bothOnly, setBothOnly] = useState(false)
  const oddsFmt = useOddsFormat()

  const { rows, quoted, matched, cheaper } = useMemo(() => {
    const rows: BoardRow[] = []
    let quoted = 0
    for (const g of groups) {
      for (const o of g.outcomes) {
        quoted++
        const b = o.book
        if (b?.ask == null || b?.bid == null) continue
        if (b.ask <= SETTLED_BAND || b.ask >= 1 - SETTLED_BAND) continue
        const pm: Quote = {
          bid: b.bid,
          ask: b.ask,
          spread: b.ask - b.bid,
          askDepthUsd: b.askDepthUsd,
        }
        const k = kalshi?.byOutcome[outcomeKey(g.question, o.name)] ?? null
        rows.push({
          question: g.question,
          outcome: o.name,
          pm,
          depthUsd: b.askDepthUsd,
          k,
          pick: pickFor(pm, k ?? undefined),
        })
      }
    }
    // Tightest book first: the spread is the tell, not the depth.
    rows.sort(
      (a, b) => (a.pm.spread ?? 9) - (b.pm.spread ?? 9) || (b.depthUsd ?? 0) - (a.depthUsd ?? 0)
    )
    const matched = rows.filter((r) => r.k != null).length
    const cheaper = rows.filter((r) => r.pick?.venue === 'kalshi').length
    return { rows, quoted, matched, cheaper }
  }, [groups, kalshi])

  const shown = bothOnly ? rows.filter((r) => r.k != null) : rows

  return (
    <section className="gc-section">
      <h2 className="gc-h2">The board, on both exchanges</h2>
      <p className="gc-quiet">
        {rows.length} of {quoted} quoted Polymarket outcomes have a real order book — the rest are
        Gamma mids, a number rather than a price you can pay.{' '}
        {kalshi ? (
          <>
            Kalshi lists this fixture and quotes{' '}
            <b className="gc-mono">{matched}</b> of the same bets;{' '}
            {cheaper > 0 ? (
              <>
                it is the cheaper venue on <b className="gc-mono">{cheaper}</b> of them, after both
                taker fees.
              </>
            ) : (
              <>Polymarket is at least as cheap on every one of them, after both taker fees.</>
            )}
          </>
        ) : (
          <>Kalshi does not list this fixture, so there is nothing to compare against.</>
        )}
      </p>

      {matched > 0 && (
        <button className="gc-more" onClick={() => setBothOnly((v) => !v)}>
          {bothOnly ? 'show every market' : `show only the ${matched} both exchanges quote`}
        </button>
      )}

      <div className="gcx-scroll">
        <table className="gc-board">
          <thead>
            <tr>
              <th>Market</th>
              <th>Side</th>
              <th className="gc-r">Polymarket</th>
              <th className="gc-r">Kalshi</th>
              <th className="gc-r">Spread</th>
              <th className="gc-r">Depth</th>
            </tr>
          </thead>
          <tbody>
            {(open ? shown : shown.slice(0, 8)).map((r, i) => (
              <tr key={i}>
                <td className="gc-board-q">{r.question.split(':').pop()?.trim()}</td>
                <td>{r.outcome}</td>
                <td
                  className={`gc-r gc-mono${r.pick?.venue === 'polymarket' ? ' is-best' : ''}`}
                  title={venueTitle('polymarket', r)}
                >
                  {odds(r.pm.ask, oddsFmt)}
                </td>
                <td
                  className={`gc-r gc-mono${r.pick?.venue === 'kalshi' ? ' is-best' : ''}${
                    r.k ? '' : ' is-empty'
                  }`}
                  title={venueTitle('kalshi', r)}
                >
                  {r.k ? odds(r.k.quote.ask, oddsFmt) : '—'}
                </td>
                <td className="gc-r gc-mono">{((r.pm.spread ?? 0) * 100).toFixed(1)}pp</td>
                <td className="gc-r gc-mono">{money(r.depthUsd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {shown.length > 8 && (
        <button className="gc-more" onClick={() => setOpen((v) => !v)}>
          {open ? 'show fewer' : `show all ${shown.length}`}
        </button>
      )}

      <p className="gc-quiet gc-board-note">
        Prices are each exchange&apos;s ask — what buying that side costs now — and the highlight
        marks the cheaper of the two <b>after each venue&apos;s taker fee</b>: Polymarket
        0.05 × p × (1 − p) per share, Kalshi 0.07 × p × (1 − p) per contract. Spread and depth are
        Polymarket&apos;s, from its own book. This is a price comparison and never an arb: across
        57 fixtures quoted on both venues the net arb count was zero, the gross ceiling being one
        tick against a ~3pp fee bar.
      </p>
    </section>
  )
}

function venueTitle(venue: 'polymarket' | 'kalshi', r: BoardRow): string {
  if (venue === 'kalshi') {
    if (!r.k) return 'Kalshi does not quote this bet.'
    const lines = [
      `Kalshi: ${r.k.label}${r.k.side === 'no' ? ' — the NO leg of that ticker' : ''}`,
      `ask ${cents(r.k.quote.ask)}, bid ${cents(r.k.quote.bid)}`,
      `${cents(netCost(r.k.quote.ask ?? 0, 'kalshi'))} with its taker fee`,
    ]
    if (r.pick?.venue === 'kalshi' && r.pick.savingPp != null) {
      lines.push(`Cheaper here by ${r.pick.savingPp.toFixed(1)}pp after both fees.`)
    }
    return lines.join('\n')
  }
  const lines = [
    `Polymarket: ask ${cents(r.pm.ask)}, bid ${cents(r.pm.bid)}`,
    `${cents(netCost(r.pm.ask ?? 0, 'polymarket'))} with its taker fee`,
  ]
  if (r.pick?.venue === 'polymarket' && r.pick.savingPp != null) {
    lines.push(`Cheaper here by ${r.pick.savingPp.toFixed(1)}pp after both fees.`)
  }
  return lines.join('\n')
}

const cents = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}¢`)

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
            <strong>Form is shown, not sold.</strong> Team numbers come from our own database of
            145,000 matches with half-time scores and closing prices. We tested recent form against
            the closing price on 14,365 held-out matches and it added nothing — a streak here is a
            description, and its &quot;1 in N&quot; is worked out at the league&apos;s own rate.
          </li>
          <li>
            <strong>The brief is written by AI</strong> from the numbers on this page and told to
            use nothing else. Where it quotes a number, the number is on the page beside it.
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

// ── tabs ─────────────────────────────────────────────────────────────────────

const TABS = [
  ['overview', 'Overview'],
  ['stats', 'Stats'],
  ['match', 'Match'],
  ['markets', 'Markets'],
] as const
type Tab = (typeof TABS)[number][0]

// ── page ─────────────────────────────────────────────────────────────────────

export default function GamePage({ params }: { params: { slug: string } }) {
  const [data, setData] = useState<GameData | null>(null)
  const [ctx, setCtx] = useState<MatchContext | null>(null)
  const [ctxLoading, setCtxLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<Tab>('overview')
  const slug = params.slug

  // One watchlist hook for the board and this page, so a star follows the
  // account on Pro and the device otherwise — never one of each.
  const { me } = useSession()
  const { slugs, toggle } = useWatchlist(me?.plan === 'pro')
  const watched = slugs.includes(slug)

  // A shared "#stats" link opens Stats — on first load AND when only the hash
  // changes, which does not reload the page.
  useEffect(() => {
    const fromHash = () => {
      const h = window.location.hash.slice(1)
      if (TABS.some(([k]) => k === h)) setTab(h as Tab)
    }
    fromHash()
    window.addEventListener('hashchange', fromHash)
    return () => window.removeEventListener('hashchange', fromHash)
  }, [])
  const choose = (t: Tab) => {
    setTab(t)
    try {
      history.replaceState(null, '', `#${t}`)
    } catch {
      /* a sandboxed frame may refuse; the tab still switches */
    }
  }

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

  const loadCtx = useCallback(async () => {
    try {
      const res = await fetch(`/api/game/context?slug=${encodeURIComponent(slug)}`)
      if (res.ok) setCtx(await res.json())
    } catch {
      /* the price panel stands on its own; the context panels say they are missing */
    } finally {
      setCtxLoading(false)
    }
  }, [slug])

  useEffect(() => {
    load()
    loadCtx()
  }, [load, loadCtx])

  // Refresh while the match is live. 30s matches how long the live feed is
  // cached server-side; the match context changes more slowly than the prices.
  const isLive = data?.board.phase === 'live' || ctx?.espn?.state === 'in'
  useEffect(() => {
    if (!isLive) return
    const a = setInterval(load, 30000)
    const b = setInterval(loadCtx, 60000)
    return () => {
      clearInterval(a)
      clearInterval(b)
    }
  }, [isLive, load, loadCtx])

  // Before kick-off the line-ups are the one thing worth re-checking for.
  const waitingForXi = !!ctx?.espn && ctx.espn.state === 'pre' && !ctx.espn.lineupsConfirmed
  useEffect(() => {
    if (!waitingForXi) return
    const id = setInterval(loadCtx, 5 * 60000)
    return () => clearInterval(id)
  }, [waitingForXi, loadCtx])

  const swapped = !!ctx?.espn?.swapped

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
            <MatchHero data={data} ctx={ctx} />

            <nav className="gcx-tabs" role="tablist" aria-label="Game Center sections">
              {TABS.map(([k, label]) => (
                <button
                  key={k}
                  role="tab"
                  aria-selected={tab === k}
                  className={`gcx-tab${tab === k ? ' is-on' : ''}`}
                  onClick={() => choose(k)}
                >
                  {label}
                  {k === 'match' && isLive && <i className="gcx-tab-dot" aria-label="live" />}
                  {k === 'match' && !isLive && ctx?.espn?.lineupsConfirmed && ctx.espn.state === 'pre' && (
                    <i className="gcx-tab-new">XI</i>
                  )}
                </button>
              ))}
            </nav>

            {tab === 'overview' && (
              <>
                <BriefCard slug={slug} />
                <OddsTiles headlines={data.headlines ?? []} />
                {ctx?.momentum && isLive && (
                  <MomentumChart m={ctx.momentum} espn={ctx.espn} home={data.home} away={data.away} />
                )}
                <PricedLikeCard pl={data.pricedLike} />
                <StreakHighlights ctx={ctx?.teams ?? null} swapped={swapped} />
                <PriceChart series={data.series ?? []} headlines={data.headlines ?? []} />
                <PulseList pulse={data.pulse ?? []} />
              </>
            )}

            {tab === 'stats' && (
              <StatsTab
                ctx={ctx?.teams ?? null}
                loading={ctxLoading}
                espn={ctx?.espn ?? null}
                home={data.home}
                away={data.away}
              />
            )}

            {tab === 'match' && (
              <MatchTab
                espn={ctx?.espn ?? null}
                loading={ctxLoading}
                momentum={ctx?.momentum ?? null}
                liveStats={data.live?.stats ?? null}
                home={data.home}
                away={data.away}
              />
            )}

            {tab === 'markets' && (
              <>
                <PulseList pulse={data.pulse ?? []} />
                <Looks data={data} />
                <Board groups={data.groups} kalshi={data.kalshi} />
              </>
            )}

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
