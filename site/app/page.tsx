'use client'

/** SCOUT — the matchday board, on both exchanges.
 *
 *  🔑 The two feeds arrive separately and that is deliberate. Polymarket's
 *     whole board is one paged Gamma sweep and answers in well under a second
 *     warm. Kalshi's is 139 rate-limited series requests — ~34 seconds cold —
 *     so waiting for it server-side would make everybody pay for a column only
 *     some fixtures have. The board renders on Polymarket and the Kalshi
 *     prices land a beat later, merged in the browser by `venueMerge`.
 *
 *     A Kalshi outage therefore costs the Kalshi column and nothing else.
 */

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { BoardView, LiveState, money, odds } from './components/BoardView'
import { IconBoard, IconDrop, IconInsights, IconLab, IconWallet } from './components/icons'
import type { ScoutFixture } from './lib/scoutTypes'
import type { KalshiFixture } from './lib/kalshiSoccerTypes'
import { mergeKalshi } from './lib/venueMerge'
import { rankRows, rowFromScout, SOCCER_COLUMNS } from './lib/boardRow'
import { useOddsFormat } from './lib/display'
import { VENUE_NAME } from './lib/venues'

/** Kalshi's index is rebuilt about four times an hour across the deployment;
 *  asking more often than its own price clock would only re-read it. */
const KALSHI_REFRESH_MS = 60_000

export default function ScoutPage() {
  const [fixtures, setFixtures] = useState<ScoutFixture[]>([])
  const [kalshi, setKalshi] = useState<KalshiFixture[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const oddsFmt = useOddsFormat()

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

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetch('/api/venues/soccer')
        .then((r) => r.json())
        .then((body) => {
          // A failed Kalshi read leaves whatever we already had. An empty
          // column is a claim about Kalshi; a missing one is a claim about us.
          if (!cancelled && body?.ok) setKalshi(body.fixtures ?? [])
        })
        .catch(() => {
          /* the board is never waiting on this */
        })
    load()
    const t = setInterval(load, KALSHI_REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  const { rows, placed } = useMemo(() => {
    const base = fixtures.map((f) => ({ ...f }))
    const merged = mergeKalshi(base, kalshi ?? [])
    // Ranked AFTER the merge, because the rank is combined volume.
    return { rows: rankRows(merged.fixtures.map(rowFromScout)), placed: merged.placed }
  }, [fixtures, kalshi])

  const headline = useMemo(() => rows.filter((r) => !r.finished).slice(0, 4), [rows])

  const head = (
    <>
      <div className="sc-head">
        <p className="sc-eyebrow">NOPREDICTIONS · Prediction-market research</p>
        <h1 className="sc-h1">See if the price is wrong — before you trade it</h1>
        <p className="sc-h1-sub">
          Today&apos;s football on Polymarket and Kalshi, biggest markets first, with the cheaper
          exchange marked on every price — after each one&apos;s taker fee, not before it. Open a
          game to check its price against how matches priced the same way actually ended, both
          teams&apos; form against the closing line, and the book you would be trading into. No
          tips: the numbers, and you decide.
        </p>
        <nav className="sc-does" aria-label="What you can do here">
          <a href="#games">
            <IconBoard />
            <div>
              <b>Open a game</b>
              <span>Every fixture below has its own page</span>
            </div>
          </a>
          <Link href="/lab">
            <IconLab />
            <div>
              <b>Test a theory</b>
              <span>Backtest your idea on 111,475 real games</span>
            </div>
          </Link>
          <Link href="/wallet">
            <IconWallet />
            <div>
              <b>Read a trader</b>
              <span>Any Polymarket wallet&apos;s full record</span>
            </div>
          </Link>
        </nav>
      </div>
      <div id="games" />

      {!loading && !error && headline.length > 0 && (
        <div className="sc-big">
          {headline.map((r) => (
            <Link
              key={r.key}
              href={r.href ?? '#'}
              className={`sc-big-card${r.live ? ' is-live' : ''}`}
            >
              <div className="sc-big-top">
                <span className="sc-big-comp">{r.competition ?? 'Football'}</span>
                <span className="sc-big-venues">
                  {r.venues.map((v) => (
                    <em key={v.venue} title={`${VENUE_NAME[v.venue]} lists this fixture`}>
                      {v.venue === 'kalshi' ? 'K' : 'P'}
                    </em>
                  ))}
                </span>
                <LiveState r={r} />
              </div>

              <div className="sc-big-teams">
                <span>{r.left}</span>
                <span className="sc-big-v">
                  {r.score && r.live ? (
                    <b className="np-num sc-big-score">
                      {r.score.left}–{r.score.right}
                    </b>
                  ) : (
                    'v'
                  )}
                </span>
                <span>{r.right}</span>
              </div>

              <div className="sc-big-odds">
                {SOCCER_COLUMNS.map((c) => {
                  const pick = r.best[c.key]
                  return (
                    <span
                      key={c.key}
                      className={`sc-big-odd${c.divider ? ' sc-big-odd-alt' : ''}${
                        pick?.venue ? ' is-best' : ''
                      }`}
                    >
                      <em>{c.label}</em>
                      <b className="np-num">
                        {odds(pick?.ask ?? null, oddsFmt)}
                        {pick?.venue && (
                          <i className="sc-best-v" aria-hidden="true">
                            {pick.venue === 'kalshi' ? 'K' : 'P'}
                          </i>
                        )}
                      </b>
                    </span>
                  )
                })}
              </div>

              <div className="sc-big-foot">
                <span className="np-num sc-big-vol">{money(r.volume)} traded</span>
                {r.markets != null && <span className="np-num sc-big-mkts">{r.markets} markets</span>}
                <span className="sc-report">
                  View report <span aria-hidden="true">→</span>
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  )

  const foot = (
    <p className="sp-foot">
      Prices are each exchange&apos;s <b>ask</b> — what buying that side costs right now — and the
      board shows the cheaper of the two <b>after each venue&apos;s taker fee</b>: Polymarket
      0.05 × p × (1 − p) per share, Kalshi 0.07 × p × (1 − p) per contract, 40% more. Comparing
      the printed asks would hand Kalshi wins it does not have. A venue can only be marked
      cheaper where its book is real — a lone sell order behind an empty bid side is the cheapest
      quote on the card by arithmetic and is not a market.{' '}
      {kalshi === null ? (
        <>Kalshi&apos;s board is still loading.</>
      ) : (
        <>
          Kalshi lists <b className="np-num">{placed}</b> of these{' '}
          <b className="np-num">{rows.length}</b> fixtures; a market we cannot place on a fixture
          with both teams matched and the kick-off agreeing is left out, never guessed.
        </>
      )}{' '}
      Volume adds Polymarket&apos;s dollars traded to Kalshi&apos;s $1 contracts — close enough to
      rank on, which is all it is used for.
    </p>
  )

  const links = (
    <>
      <Link
        href="/dropping-odds"
        className="sc-cat is-link"
        title="Where the market moved in the last 24 hours — pre-match, and only on books with real money through them"
      >
        <IconDrop className="sc-cat-icn" />
        Dropping odds
        <span className="sc-cat-go" aria-hidden="true">→</span>
      </Link>
      <Link
        href="/insights"
        className="sc-cat is-link"
        title="What we measured, and what it said — including the results that went the wrong way"
      >
        <IconInsights className="sc-cat-icn" />
        Insights
        <span className="sc-cat-go" aria-hidden="true">→</span>
      </Link>
    </>
  )

  return (
    <BoardView
      columns={SOCCER_COLUMNS}
      rows={rows}
      loading={loading}
      error={error}
      joiner="v"
      competitionChips
      allLabel="All football"
      head={head}
      foot={foot}
      emptyLabel="No board matches that filter right now."
      pending={kalshi === null ? 'Kalshi loading…' : null}
      links={links}
      initialQuery={query}
    />
  )
}
