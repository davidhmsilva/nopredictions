'use client'

/** Dropping odds — where the market moved, before anyone kicked a ball.
 *
 *  Laid out the way a line-movement page is usually laid out, because the shape
 *  works: a hero that leads with the single biggest move, then a dense table
 *  where each row is one fixture and the price path is a sparkline. Cards were
 *  the wrong container — they give eight equally sized boxes to a list whose
 *  whole point is that the top of it matters most.
 *
 *  🔑 The board is SORTED by probability points, not by the percentage the odds
 *     fell. Those rank differently and the difference is not cosmetic: 11.87 →
 *     10.20 is a 14.1% drop and 1.4pp, while 2.68 → 2.40 is 10.4% and 4.1pp.
 *     Sorting on the percentage puts longshots at the top of every board,
 *     because the same probability move is a larger fraction of a larger
 *     number. Both are shown — the percentage is what "dropping odds" means to
 *     a reader — but only one of them decides the order.
 *
 *  ⚠️ The page shows what moved. It does not claim that following the move
 *     pays; this project's own measurements point the other way, and the footer
 *     says so rather than leaving the arrows to imply otherwise.
 */

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import type { MoverRow, MoversMeta } from '../lib/movers'
import { SPORT_KEYS, SPORT_META } from '../lib/sportsMeta'
import type { BoardSport } from '../lib/boardRow'
import { kickoffText, priceText, useOddsFormat, type OddsFormat } from '../lib/display'

// ── formatting ───────────────────────────────────────────────────────────────

function odds(p: number | null | undefined, f: OddsFormat): string {
  if (p == null || p <= 0.005 || p >= 0.995) return '—'
  return priceText(p, f)
}

function money(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

/** How far the DECIMAL fell, as a percentage of what it was — the visceral
 *  number, and the one the phrase "dropping odds" actually describes. */
function dropPct(m: MoverRow['move']): number {
  const was = 1 / m.before
  const now = 1 / m.now
  return was > 0 ? ((was - now) / was) * 100 : 0
}

function kickoff(iso: string | null, hours: number | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  // ⚠️ Never the browser's locale: an empty locale array renders "quarta,
  //    9/09" on a Portuguese machine and "Wed, 9 Sep" on an English one — the
  //    same page reading differently to two people comparing a column of
  //    times. The format follows the reader's ZONE instead (lib/display):
  //    "Sun, Sep 13, 1:00 PM ET" in New York, "Sun 13 Sep, 18:00 BST" in
  //    London. English for both, and the zone is always named.
  const rel =
    hours == null ? '' : hours < 1 ? ` · ${Math.round(hours * 60)}m` : ` · T−${hours.toFixed(0)}h`
  return `${kickoffText(d)}${rel}`
}

// ── the price path ───────────────────────────────────────────────────────────

/** A sparkline of the backed side, plotted as the DECIMAL so the line falls
 *  the same way the number beside it does.
 *
 *  ⚠️ Scaled to its own minimum and maximum, so every line fills its box. That
 *     makes the SHAPE readable and the HEIGHT meaningless between rows — the
 *     numbers carry magnitude, this carries the path. */
function Spark({ points, w = 96, h = 26 }: { points: number[]; w?: number; h?: number }) {
  if (points.length < 2) {
    return (
      <span className="do-spark-none" title="Polymarket published no price history for this token">
        —
      </span>
    )
  }
  const ys = points.map((p) => (p > 0.005 ? 1 / p : 0))
  const lo = Math.min(...ys)
  const hi = Math.max(...ys)
  const span = hi - lo || 1
  const step = w / (ys.length - 1)
  const d = ys
    .map(
      (y, i) =>
        `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(h - ((y - lo) / span) * h).toFixed(1)}`
    )
    .join(' ')
  const fell = ys[ys.length - 1] < ys[0]

  return (
    <svg
      className={`do-spark${fell ? ' is-down' : ' is-up'}`}
      viewBox={`0 0 ${w} ${h}`}
      width={w}
      height={h}
      aria-hidden="true"
    >
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

// ── one row ──────────────────────────────────────────────────────────────────

const CHIP_CLASS: Record<MoverRow['backed'], string> = { left: 'is-home', right: 'is-away', draw: 'is-draw' }

function Row({ f }: { f: MoverRow }) {
  const oddsFmt = useOddsFormat()
  return (
    <Link href={f.href} className="do-row">
      <div className="do-c-match">
        <div className="do-teams">
          <span className={f.backed === 'left' ? 'is-backed' : ''}>{f.left}</span>
          <span className="do-vs">{f.joiner}</span>
          <span className={f.backed === 'right' ? 'is-backed' : ''}>{f.right}</span>
        </div>
        <div className="do-when">{kickoff(f.kickoff, f.hoursToKickoff)}</div>
      </div>

      <div className="do-c-league">
        <span className="np-badge">{f.competition ?? 'Soccer'}</span>
      </div>

      <div className="do-c-backed">
        <span className={`do-chip ${CHIP_CLASS[f.backed]}`}>{f.chip}</span>
        <span className="do-backed-name">{f.move.label}</span>
      </div>

      <div className="do-c-move">
        <b className="np-num do-pct">↓{dropPct(f.move).toFixed(1)}%</b>
        <span className="np-num do-pp">{f.move.pp.toFixed(1)}pp</span>
      </div>

      <div className="do-c-was np-num">{odds(f.move.before, oddsFmt)}</div>
      <div className="do-c-now np-num">{odds(f.move.now, oddsFmt)}</div>

      <div className="do-c-trend">
        <Spark points={f.spark} />
      </div>

      <div className="do-c-vol np-num">{money(f.volumeUsd)}</div>
    </Link>
  )
}

function Table({ rows }: { rows: MoverRow[] }) {
  return (
    <div className="do-table">
      <div className="do-head-row">
        <span>Match</span>
        <span>League</span>
        <span>Backed</span>
        <span>Move</span>
        <span>24h ago</span>
        <span>Now</span>
        <span>Trend</span>
        <span>Volume</span>
      </div>
      {rows.map((f) => (
        <Row key={f.key} f={f} />
      ))}
    </div>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function DroppingOddsPage() {
  const [allFunded, setFunded] = useState<MoverRow[] | null>(null)
  const [allThin, setThin] = useState<MoverRow[]>([])
  const [sport, setSport] = useState<BoardSport | null>(null)
  const [showThin, setShowThin] = useState(false)
  const [meta, setMeta] = useState<MoversMeta | null>(null)
  const [error, setError] = useState<string | null>(null)
  const oddsFmt = useOddsFormat()

  useEffect(() => {
    let cancelled = false
    fetch('/api/movers')
      .then(async (r) => {
        const b = await r.json()
        if (!r.ok || !b.ok) throw new Error(b.error ?? `HTTP ${r.status}`)
        return b
      })
      .then((b) => {
        if (cancelled) return
        setFunded(b.funded ?? [])
        setThin(b.thin ?? [])
        setMeta(b.meta ?? null)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not reach Polymarket')
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Only the sports that actually have a mover get a chip.
  const sportsHere = useMemo(() => {
    const present = new Set([...(allFunded ?? []), ...allThin].map((m) => m.sport))
    return (['soccer', ...SPORT_KEYS] as BoardSport[]).filter((k) => present.has(k))
  }, [allFunded, allThin])
  const funded = allFunded && (sport ? allFunded.filter((m) => m.sport === sport) : allFunded)
  const thin = sport ? allThin.filter((m) => m.sport === sport) : allThin

  const top = funded?.[0] ?? null

  return (
    <AppShell>
      <div className="do-page">
        {/* ── hero: what this is, and the single biggest move ── */}
        <section className="do-hero">
          <div className="do-hero-copy">
            <h1>
              Watch the money.
              <br />
              Not the tip.
            </h1>
            <p>
              Every game on Polymarket — soccer, NFL, college football, MLB, NBA, NHL and
              WNBA — ranked by the side that shortened most in the last 24 hours. Pre-match
              only, and only where enough has traded for the move to mean anything.
            </p>
            <div className="do-hero-cta">
              <Link href="/" className="np-btn np-btn-primary">
                See today&apos;s full board
              </Link>
              <Link href="/insights" className="np-btn">
                Why these are not tips
              </Link>
            </div>
          </div>

          <div className="do-hero-panel">
            {top ? (
              <Link href={top.href} className="do-top">
                <div className="do-top-head">
                  <span className="do-top-label">Biggest move now</span>
                  <b className="np-num do-top-pct">↓{dropPct(top.move).toFixed(1)}%</b>
                </div>

                <div className="do-top-sel">
                  <span className="do-top-sel-label">BACKED SELECTION</span>
                  <b>{top.move.label}</b>
                  <span className="do-top-fixture">
                    {top.left} <em>{top.joiner}</em> {top.right}
                  </span>
                </div>

                <div className="do-top-odds">
                  <span>
                    <em>24H AGO</em>
                    <b className="np-num do-top-was">{odds(top.move.before, oddsFmt)}</b>
                  </span>
                  <span className="do-top-arrow" aria-hidden="true">→</span>
                  <span>
                    <em>NOW</em>
                    <b className="np-num do-top-now">{odds(top.move.now, oddsFmt)}</b>
                  </span>
                </div>

                <div className="do-top-spark">
                  <Spark points={top.spark} w={300} h={72} />
                </div>

                <div className="do-top-foot">
                  {top.competition ?? 'Soccer'} · {kickoff(top.kickoff, top.hoursToKickoff)} ·{' '}
                  {money(top.volumeUsd)} traded
                </div>
              </Link>
            ) : (
              <div className="do-top is-empty">
                {error
                  ? 'Could not read the board.'
                  : funded
                    ? 'Nothing on a funded book has moved today. That is a normal answer, not an outage.'
                    : 'Reading the board…'}
              </div>
            )}
          </div>
        </section>

        {sportsHere.length > 1 && (
          <div className="do-sports" role="group" aria-label="Sport">
            <button className={`sc-cat${sport === null ? ' is-on' : ''}`} onClick={() => setSport(null)}>
              All sports
            </button>
            {sportsHere.map((k) => (
              <button
                key={k}
                className={`sc-cat${sport === k ? ' is-on' : ''}`}
                onClick={() => setSport(sport === k ? null : k)}
              >
                {k === 'soccer' ? 'Soccer' : SPORT_META[k].tab ?? SPORT_META[k].label}
              </button>
            ))}
          </div>
        )}

        {error && (
          <div className="np-note do-error">
            <strong>Could not load the board.</strong> {error}
          </div>
        )}

        {funded && funded.length > 0 && (
          <section className="do-section">
            <div className="do-section-head">
              <h2>Market movers</h2>
              <span>
                SHORTENERS · LAST 24H · BOOKS OVER $
                {(meta?.fundedVolumeUsd ?? 5000).toLocaleString('en-US')}
              </span>
            </div>
            <Table rows={funded} />
          </section>
        )}

        {thin.length > 0 && (
          <section className="do-section do-thin">
            <button
              className="do-thin-toggle"
              onClick={() => setShowThin((v) => !v)}
              aria-expanded={showThin}
            >
              <span>
                Thin books · <b className="np-num">{thin.length}</b> more moved, on under $
                {(meta?.fundedVolumeUsd ?? 5000).toLocaleString('en-US')}
              </span>
              <span className="do-thin-chev" aria-hidden="true">
                {showThin ? '▲' : '▼'}
              </span>
            </button>

            {showThin && (
              <>
                <p className="do-thin-why">
                  Kept apart rather than hidden, and not allowed to lead the board. A big
                  number on a thin book is usually two orders, not a market changing its
                  mind — and the two are indistinguishable from the percentage alone.
                  <span className="do-thin-cite">
                    Measured on one live board, 8 September 2026: the median move was the
                    same in every volume band (3.0pp under $5k, 3.0pp at $5–10k, 2.5pp
                    above), but only the thin band had a tail — a 23pp swing on $2,672,
                    against a 4pp maximum on everything funded. Same middle, fat tail on
                    one side, is what noise looks like.
                  </span>
                </p>
                <Table rows={thin} />
              </>
            )}
          </section>
        )}

        {meta && (
          <div className="do-meta">
            <b className="np-num">{meta.shown}</b> shown of{' '}
            <b className="np-num">{meta.candidates}</b> pre-match games ·{' '}
            <b className="np-num">{meta.droppedForSmallMove}</b> moved less than{' '}
            {meta.minMovePp}pp · <b className="np-num">{meta.droppedForVolume}</b> moved but
            had under ${meta.minVolumeUsd.toLocaleString('en-US')} through them ·{' '}
            <b className="np-num">{meta.noChangePublished}</b> had no 24h change published —
            a market listed today has no yesterday.
          </div>
        )}

        <div className="np-note do-honest">
          <strong>This is what moved, not what to back.</strong> A shortening price is
          money arriving, and money arriving is not the same as money being right. Our own
          measurements point the other way: ask movement carried nothing beyond the ask
          level, and pre-match Polymarket football did not survive the spread floor. Both
          are written up in <Link href="/insights">Insights</Link>.
          <br />
          <br />
          The board is ordered by <strong>probability points</strong>, not by the
          percentage the odds fell. They disagree, and the disagreement matters: 11.87 to
          10.20 is 14.1% of the price and 1.4 points of probability, while 2.68 to 2.40 is
          10.4% and 4.1 points. Ranking on the percentage would put longshots at the top of
          every board for no reason other than their arithmetic.
        </div>
      </div>
    </AppShell>
  )
}
