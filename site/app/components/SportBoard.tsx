'use client'

/** One US sport's board: every game ESPN has scheduled that Kalshi or
 *  Polymarket lists, with both venues' moneyline side by side.
 *
 *  🔑 The comparison this page exists for is WHICH VENUE IS CHEAPER for the
 *     same side, and it is only marked where both books are real — graded
 *     above `none`. A cent saved against a placeholder book is not a price.
 *     It is before fees, which differ by venue, and the foot of the page says
 *     so rather than leaving the highlight to imply otherwise.
 *
 *  Away above home with an @, the way a US schedule prints a game.
 */

import { useEffect, useMemo, useState } from 'react'
import { AppShell } from './AppShell'
import { OddsToggle } from './OddsToggle'
import { SportBar } from './SportBar'
import {
  dayHeading,
  formatName,
  priceText,
  timeText,
  useOddsFormat,
  zoneLabel,
  type OddsFormat,
} from '../lib/display'
import {
  SPORT_META,
  type BookGrade,
  type SportBoardData,
  type SportGame,
  type SportKey,
  type TeamRef,
  type Venue,
  type VenueLine,
  type VenueQuote,
} from '../lib/sportsMeta'

const VENUE_NAME: Record<Venue, string> = { kalshi: 'Kalshi', polymarket: 'Polymarket' }

const GRADE_LABEL: Record<BookGrade, string> = {
  clean: 'clean book',
  thin: 'thin book',
  wide: 'wide book',
  none: 'no real book',
}

/** A live board moves; a pre-match one barely does. The server caches for a
 *  minute, so asking more often than that would only re-read the same board. */
const REFRESH_MS = 60_000

function price(q: VenueQuote | undefined, f: OddsFormat): string {
  const a = q?.ask
  // A side at 99¢+ or 1¢- is decided, not priced.
  if (a == null || a <= 0.01 || a >= 0.99) return '—'
  return priceText(a, f)
}

function money(v: number | null | undefined): string {
  if (v == null || v <= 0) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function cents(x: number | null | undefined): string {
  return x == null ? '—' : `${Math.round(x * 100)}¢`
}

function When({ g }: { g: SportGame }) {
  if (g.state === 'in') return <span className="np-badge is-live">● {g.detail}</span>
  if (g.state === 'post') return <span className="np-badge">{g.detail}</span>
  const d = new Date(g.start)
  const mins = Math.round((d.getTime() - Date.now()) / 60000)
  return <span className="np-num sp-time">{mins > 0 && mins < 60 ? `${mins}m` : timeText(d)}</span>
}

function Team({ t, home, scored }: { t: TeamRef; home?: boolean; scored: boolean }) {
  return (
    <span className="sp-team" title={t.name}>
      {t.logo ? <img className="sp-logo" src={t.logo} alt="" loading="lazy" /> : <span className="sp-logo" />}
      <span className="sp-name">
        {home && <em>@</em>}
        {t.short}
      </span>
      {scored && t.score != null && <b className="np-num sp-score">{t.score}</b>}
    </span>
  )
}

function PriceCell({
  line,
  side,
  best,
  f,
}: {
  line: VenueLine | null
  side: 'home' | 'away'
  best: boolean
  f: OddsFormat
}) {
  if (!line) return <td className="sp-c-px is-empty">—</td>
  const q = line[side]
  const title =
    `${VENUE_NAME[line.venue]} — ask ${cents(q.ask)}, bid ${cents(q.bid)}` +
    (q.askDepthUsd != null ? `, ${money(q.askDepthUsd)} offered at the ask` : '') +
    (best ? '. The cheaper venue for this side, before fees.' : '')
  return (
    <td className={`sp-c-px np-num${best ? ' is-best' : ''}${line.grade === 'none' ? ' is-dim' : ''}`} title={title}>
      {price(q, f)}
    </td>
  )
}

function VenueMeta({ line, venue }: { line: VenueLine | null; venue: Venue }) {
  if (!line) {
    return (
      <span className="sp-venue is-missing">
        <b>{VENUE_NAME[venue]}</b> not listed
      </span>
    )
  }
  // The worse side's spread; none at all when the book is not two-sided.
  const spreads = [line.home.spread, line.away.spread].filter((x): x is number => x != null)
  const spread = spreads.length === 2 ? Math.max(spreads[0], spreads[1]) : null
  return (
    <span className={`sp-venue is-${line.grade}`}>
      <b>{VENUE_NAME[venue]}</b>
      <span>{GRADE_LABEL[line.grade]}</span>
      {spread != null && <span className="np-num">{cents(spread)} wide</span>}
      <span className="np-num">{money(line.volume)} vol</span>
      <a href={line.url} target="_blank" rel="noopener noreferrer">
        Open ↗
      </a>
    </span>
  )
}

function GameRows({ g, f }: { g: SportGame; f: OddsFormat }) {
  const scored = g.state !== 'pre'
  return (
    <tbody className={`sp-game${g.state === 'in' ? ' is-live' : ''}`}>
      <tr>
        <td className="sp-c-when" rowSpan={3}>
          <When g={g} />
        </td>
        <td className="sp-c-team">
          <Team t={g.away} scored={scored} />
        </td>
        <PriceCell line={g.kalshi} side="away" best={g.best.away === 'kalshi'} f={f} />
        <PriceCell line={g.polymarket} side="away" best={g.best.away === 'polymarket'} f={f} />
      </tr>
      <tr>
        <td className="sp-c-team">
          <Team t={g.home} home scored={scored} />
        </td>
        <PriceCell line={g.kalshi} side="home" best={g.best.home === 'kalshi'} f={f} />
        <PriceCell line={g.polymarket} side="home" best={g.best.home === 'polymarket'} f={f} />
      </tr>
      <tr className="sp-meta">
        <td colSpan={3}>
          <VenueMeta line={g.kalshi} venue="kalshi" />
          <VenueMeta line={g.polymarket} venue="polymarket" />
        </td>
      </tr>
    </tbody>
  )
}

export function SportBoard({ sport }: { sport: SportKey }) {
  const meta = SPORT_META[sport]
  const [data, setData] = useState<SportBoardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const oddsFmt = useOddsFormat()
  // Named once, in each table head. Client-side: the server runs in UTC.
  const zone = useMemo(() => zoneLabel(), [])

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetch(`/api/sports/${sport}`)
        .then(async (r) => {
          const b = await r.json()
          if (!r.ok || !b.ok) throw new Error(b.error ?? `HTTP ${r.status}`)
          return b as SportBoardData
        })
        .then((b) => {
          if (cancelled) return
          setData(b)
          setError(null)
        })
        .catch((e) => {
          // A failed refresh keeps the board already on screen.
          if (!cancelled) setError(e instanceof Error ? e.message : 'Could not reach the venues')
        })
    load()
    const t = setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [sport])

  /** Live games first, under their own heading; then one section per day. */
  const sections = useMemo(() => {
    if (!data) return []
    const out: { label: string; games: SportGame[] }[] = []
    const live = data.games.filter((g) => g.state === 'in')
    if (live.length) out.push({ label: 'Live now', games: live })
    for (const g of data.games) {
      if (g.state === 'in') continue
      const label = dayHeading(new Date(g.start))
      const last = out[out.length - 1]
      if (last && last.label === label) last.games.push(g)
      else out.push({ label, games: [g] })
    }
    return out
  }, [data])

  const c = data?.counts

  return (
    <AppShell>
      <SportBar />
      <div className="np-wrap">
        <div className="sc-head">
          <p className="sc-eyebrow">NOPREDICTIONS · {meta.label} on the prediction markets</p>
          <h1 className="sc-h1">{meta.label}: Kalshi and Polymarket, side by side</h1>
          <p className="sc-h1-sub">
            Every {meta.label} game either exchange lists in the next {meta.days} days, placed on
            ESPN&apos;s schedule. Both moneylines at the ask, the book behind each, and the cheaper
            venue marked where both books are real. No tips: the numbers, and you decide.
          </p>
        </div>

        <div className="sc-bar">
          <div className="sc-bar-left">
            <span className="sc-count np-num">
              {data ? `${data.games.length} game${data.games.length === 1 ? '' : 's'}` : ''}
            </span>
          </div>
          <div className="sc-bar-right">
            <OddsToggle className="np-odds-bar" />
          </div>
        </div>

        {!data && !error && <div className="np-empty">Reading Kalshi and Polymarket…</div>}

        {error && !data && (
          <div className="np-note sc-error">
            <strong>Could not load the board.</strong> {error}
          </div>
        )}

        {data && data.games.length === 0 && (
          <div className="np-empty">
            Neither Kalshi nor Polymarket lists any {meta.label} game in the next {meta.days} days.
          </div>
        )}

        {sections.map((s) => (
          <section key={s.label} className="sp-day">
            <h2 className="sp-day-h">{s.label}</h2>
            <div className="sc-table-wrap">
              <table className="sp-table">
                <thead>
                  <tr>
                    <th className="sp-c-when">{zone}</th>
                    <th className="sp-c-team">Moneyline</th>
                    <th className="sp-c-px" title={`Kalshi's ask, in ${formatName(oddsFmt)}`}>
                      Kalshi
                    </th>
                    <th className="sp-c-px" title={`Polymarket's ask, in ${formatName(oddsFmt)}`}>
                      Polymarket
                    </th>
                  </tr>
                </thead>
                {s.games.map((g) => (
                  <GameRows key={g.id} g={g} f={oddsFmt} />
                ))}
              </table>
            </div>
          </section>
        ))}

        {data && c && (
          <p className="sp-foot">
            Placed on ESPN&apos;s schedule: Kalshi <b className="np-num">{c.kalshiPlaced}</b> of{' '}
            <b className="np-num">{c.kalshi}</b> games, Polymarket{' '}
            <b className="np-num">{c.polymarketPlaced}</b> of <b className="np-num">{c.polymarket}</b>. A
            market we cannot put on a game with both teams matched exactly is left out, never guessed.
            Prices are each venue&apos;s ask before fees — Kalshi&apos;s taker fee is 0.07 × p × (1 − p)
            per contract, Polymarket&apos;s is its own — and the highlight marks the lower ask only
            where both books are real. Kalshi counts volume in $1 contracts, Polymarket in dollars
            traded. Updated {timeText(new Date(data.generatedAt))}.
          </p>
        )}
      </div>
    </AppShell>
  )
}
