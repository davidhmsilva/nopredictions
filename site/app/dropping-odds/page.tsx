'use client'

/** Dropping odds — where the market moved, before anyone kicked a ball.
 *
 *  Cards in the same shape as Scout's, because it is the same board seen
 *  through one question: which side did money come for in the last day.
 *
 *  🔑 "Dropping odds" is the decimal falling, which is the probability rising.
 *     So the card leads with the side that SHORTENED, in decimal, with what it
 *     was before — and a board where everything drifted out has no movers
 *     rather than a least-bad one.
 *
 *  ⚠️ It shows what moved. It does not claim that following the move pays. The
 *     nearest thing this project has measured says the opposite: ask movement
 *     carries nothing beyond the ask level (−0.00042 pseudo-R², CI
 *     [−0.00104, +0.00019]), and pre-match Polymarket football did not survive
 *     the spread floor. The footer says so, because a page that shows arrows
 *     and stays quiet about that is making a claim by implication.
 */

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { IconLive } from '../components/icons'
import type { Mover, MoversMeta } from '../lib/movers'

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

/** How far out the match is. The whole point of showing it: an 8pp move at
 *  T-2h and the same move at T-40h are different events. */
function toKick(h: number | null): string {
  if (h == null) return '—'
  if (h < 1) return `${Math.round(h * 60)}m`
  if (h < 24) return `${h.toFixed(0)}h`
  return `${Math.floor(h / 24)}d ${Math.round(h % 24)}h`
}

/** One card. Extracted because the funded board and the thin board draw the
 *  same thing — the only difference between them is which list they are in,
 *  and that difference belongs in the heading rather than in the card. */
function MoverCard({ f }: { f: Mover }) {
  return (
    <Link href={`/game/${f.slug}`} className="do-card">
      <div className="do-card-top">
        <span className="do-comp">{f.competition ?? 'Football'}</span>
        <span className="do-kick">
          <IconLive className="do-kick-icn" />
          T−{toKick(f.hoursToKickoff)}
        </span>
      </div>

      <div className="do-teams">
        <span className={f.move.side === 'home' ? 'is-backed' : ''}>{f.home}</span>
        <span className="do-v">v</span>
        <span className={f.move.side === 'away' ? 'is-backed' : ''}>{f.away}</span>
      </div>

      <div className="do-move">
        <div className="do-move-side">
          <span className="do-move-label">SHORTENED</span>
          <b>{f.move.label}</b>
        </div>
        <div className="do-move-odds">
          <span className="np-num do-was">{odds(f.move.before)}</span>
          <span className="do-arrow" aria-hidden="true">→</span>
          <span className="np-num do-now">{odds(f.move.now)}</span>
        </div>
        <div className="do-move-pp np-num">−{f.move.pp.toFixed(1)}pp</div>
      </div>

      <div className="do-foot">
        <span className="np-num">{money(f.volumeUsd)} traded</span>
        {f.move.pp1h != null && Math.abs(f.move.pp1h) >= 0.5 && (
          <span className={`np-num do-1h${f.move.pp1h > 0 ? ' is-up' : ''}`}>
            {f.move.pp1h > 0 ? '+' : ''}
            {f.move.pp1h.toFixed(1)}pp last hour
          </span>
        )}
        <span className="do-go">Open →</span>
      </div>
    </Link>
  )
}

export default function DroppingOddsPage() {
  const [funded, setFunded] = useState<Mover[] | null>(null)
  const [thin, setThin] = useState<Mover[]>([])
  const [showThin, setShowThin] = useState(false)
  const [meta, setMeta] = useState<MoversMeta | null>(null)
  const [error, setError] = useState<string | null>(null)

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

  return (
    <AppShell>
      <div className="do-page">
        <header className="do-head">
          <h1>Dropping odds</h1>
          <p>
            Polymarket football, ranked by the side that shortened most in the last
            24 hours. Pre-match only, and only where enough money has gone through
            for the move to mean anything.
          </p>
        </header>

        {error && (
          <div className="np-note do-error">
            <strong>Could not load the board.</strong> {error}
          </div>
        )}

        {!funded && !error && <div className="np-empty">Reading the board…</div>}

        {funded && funded.length === 0 && (
          <div className="np-empty">
            Nothing on a funded book moved more than {meta?.minMovePp ?? 2}pp today.
            That is a normal answer, not an outage.
          </div>
        )}

        {funded && funded.length > 0 && (
          <div className="do-grid">
            {funded.map((f) => (
              <MoverCard key={f.slug} f={f} />
            ))}
          </div>
        )}

        {thin.length > 0 && (
          <section className="do-thin">
            <button
              className="do-thin-toggle"
              onClick={() => setShowThin((v) => !v)}
              aria-expanded={showThin}
            >
              <span>
                Thin books · <b className="np-num">{thin.length}</b> more moved, on under{' '}
                ${(meta?.fundedVolumeUsd ?? 5000).toLocaleString('en-US')}
              </span>
              <span className="do-thin-chev" aria-hidden="true">{showThin ? '▲' : '▼'}</span>
            </button>

            {showThin && (
              <>
                <p className="do-thin-why">
                  Kept apart rather than hidden, and not allowed to lead the board.
                  A big number on a thin book is usually two orders, not a market
                  changing its mind — and the two are indistinguishable from the
                  percentage alone.{' '}
                  <span className="do-thin-cite">
                    Measured on one live board, 8 September 2026: the median move was
                    the same in every volume band (3.0pp under $5k, 3.0pp at $5–10k,
                    2.5pp above), but only the thin band had a tail — a 23pp swing on
                    $2,672, against a 4pp maximum on everything funded. Same middle,
                    fat tail on one side, is what noise looks like.
                  </span>
                </p>
                <div className="do-grid">
                  {thin.map((f) => (
                    <MoverCard key={f.slug} f={f} />
                  ))}
                </div>
              </>
            )}
          </section>
        )}

        {meta && (
          <div className="do-meta">
            <b className="np-num">{meta.shown}</b> shown of{' '}
            <b className="np-num">{meta.candidates}</b> pre-match fixtures ·{' '}
            <b className="np-num">{meta.droppedForSmallMove}</b> moved less than{' '}
            {meta.minMovePp}pp · <b className="np-num">{meta.droppedForVolume}</b> moved
            but had under ${meta.minVolumeUsd.toLocaleString('en-US')} through them ·{' '}
            <b className="np-num">{meta.noChangePublished}</b> had no 24h change
            published — a market listed today has no yesterday.
          </div>
        )}

        <div className="np-note do-honest">
          <strong>This is what moved, not what to back.</strong> A shortening price
          is money arriving, and money arriving is not the same as money being
          right. Our own measurements point the other way: ask movement carried
          nothing beyond the ask level, and pre-match Polymarket football did not
          survive the spread floor. Both are written up in{' '}
          <Link href="/insights">Insights</Link>. Treat this board as a place to
          look, not a signal to follow.
        </div>
      </div>
    </AppShell>
  )
}
