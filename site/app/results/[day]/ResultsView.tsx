'use client'

// A day of finished football, as the market saw it at kick-off.
//
// What this page must not do is call an upset a mistake. A 68% favourite
// loses about one time in three by construction; the only thing a day of
// results can show is whether the prices were CALIBRATED — whether 60-70%
// favourites win 60-70% of the time — and that needs hundreds of games, which
// is what the table at the foot is for.

import Link from 'next/link'
import { useMemo, useState } from 'react'
import type { CalibrationBand, FavRecord, PmResult, RecordCount } from '../../lib/pmResults'
import { DayStrip } from '../../components/DayStrip'
import { isUsViewer, priceText, timeText, useMounted, useOddsFormat, type OddsFormat } from '../../lib/display'
import { dayLabel, localDate, shiftDay, yesterdayLocal } from '../../lib/localDay'

/** A book that traded less than this is two people, not a market (the same
 *  floor as Dropping odds). */
const MIN_VOLUME = 1000

type Side = 'home' | 'draw' | 'away'

const pct = (p: number) => `${Math.round(p * 100)}%`

function money(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `$${Math.round(v / 1e3)}k`
  return `$${Math.round(v)}`
}

function favSide(r: PmResult): 'home' | 'away' | null {
  if (!r.p) return null
  const s = r.p.home >= r.p.away ? 'home' : 'away'
  return r.p[s] >= 0.5 ? s : null
}

function nameOf(r: PmResult, s: Side): string {
  return s === 'home' ? r.home : s === 'away' ? r.away : 'the draw'
}

function Score({ r }: { r: PmResult }) {
  if (r.score) return <b className="rs-score np-num">{r.score}</b>
  const word = r.outcome === 'home' ? 'home win' : r.outcome === 'away' ? 'away win' : r.outcome === 'draw' ? 'draw' : 'pending'
  return <span className="rs-score rs-noscore">{word}</span>
}

function Prices({ r, f }: { r: PmResult; f: OddsFormat }) {
  if (!r.p) return <span className="rs-px gcx-dim">no price at kick-off</span>
  return (
    <span className="rs-px np-num">
      {(['home', 'draw', 'away'] as Side[]).map((s) => (
        <span key={s} className={r.outcome === s ? 'is-hit' : ''} title={`${nameOf(r, s)}: ${pct(r.p![s])} at kick-off`}>
          {s === 'draw' ? 'X' : s === 'home' ? '1' : '2'} {priceText(r.p![s], f)}
        </span>
      ))}
    </span>
  )
}

function recordText(c: RecordCount): string {
  return `${c.w}W ${c.d}D ${c.l}L in ${c.n} — the prices expected ${c.exp.toFixed(1)} wins`
}

function FavLine({ fav, r }: { fav: FavRecord; r: PmResult }) {
  const band = `${Math.round(fav.band[0] * 100)}–${Math.round(fav.band[1] * 100)}%`
  if (!fav.pm && !fav.db) {
    return (
      <p className="rs-fav">
        {fav.team} as a {band} favourite before this game: <span className="gcx-dim">no earlier priced game in our data.</span>
      </p>
    )
  }
  return (
    <p className="rs-fav">
      {fav.team} as a {band} favourite before this game —{' '}
      {fav.db && (
        <>
          league games{fav.db.from ? ` since ${fav.db.from.slice(0, 4)}` : ''}: <b>{recordText(fav.db)}</b>
          {fav.pm ? '; ' : ''}
        </>
      )}
      {fav.pm && (
        <>
          on Polymarket: <b>{recordText(fav.pm)}</b>
        </>
      )}
      {r.outcome && r.outcome !== fav.side && <span className="gcx-dim"> · this time: {r.outcome === 'draw' ? 'drew' : 'lost'}</span>}
    </p>
  )
}

function Row({ r, f, mounted }: { r: PmResult; f: OddsFormat; mounted: boolean }) {
  const fav = favSide(r)
  const hit = r.outcome && r.p ? r.p[r.outcome] : null
  return (
    <li className="rs-row">
      <Link href={`/game/${r.slug}`} className="rs-main">
        <span className="rs-time gc-mono">{mounted ? timeText(new Date(r.kickoff)) : r.kickoff.slice(11, 16)}</span>
        <span className="rs-teams">
          <span className={r.outcome === 'home' ? 'is-won' : ''}>{r.home}</span>
          <Score r={r} />
          <span className={r.outcome === 'away' ? 'is-won' : ''}>{r.away}</span>
        </span>
        <Prices r={r} f={f} />
        <span className={`rs-hit np-num${hit != null && hit < 0.25 ? ' is-surprise' : ''}`} title="What the market gave the result that happened">
          {hit != null ? pct(hit) : ''}
        </span>
      </Link>
      {r.fav && fav && <FavLine fav={r.fav} r={r} />}
    </li>
  )
}

function Calibration({ cal }: { cal: { bands: CalibrationBand[]; since: string | null; total: number } }) {
  if (cal.total < 50) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Are the prices right? Every favourite since {cal.since?.slice(0, 10)}</h2>
      <div className="gcx-scroll">
        <table className="gcx-table rs-cal">
          <thead>
            <tr>
              <th>Favourite priced</th>
              <th className="gc-r">Games</th>
              <th className="gc-r">Priced to win</th>
              <th className="gc-r">Won</th>
              <th className="gc-r">±</th>
            </tr>
          </thead>
          <tbody>
            {cal.bands.map((b) => {
              const diff = (b.won - b.priced) * 100
              // ~2 standard errors of a share over n games.
              const se2 = 200 * Math.sqrt((b.priced * (1 - b.priced)) / Math.max(b.n, 1))
              return (
                <tr key={b.lo}>
                  <td className="gc-mono">
                    {Math.round(b.lo * 100)}–{Math.round(b.hi * 100)}%
                  </td>
                  <td className="gc-r gc-mono">{b.n}</td>
                  <td className="gc-r gc-mono">{pct(b.priced)}</td>
                  <td className="gc-r gc-mono">{pct(b.won)}</td>
                  <td className={`gc-r gc-mono ${Math.abs(diff) < se2 ? 'gcx-dim' : diff > 0 ? 'gc-pos' : 'gcx-neg'}`}>
                    {diff > 0 ? '+' : ''}
                    {diff.toFixed(1)}pp
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="gc-chart-note">
        Polymarket&apos;s price at kick-off against what happened, books that traded $1k or more. A difference
        in grey is inside two standard errors of zero — what chance alone produces on that many games.
      </p>
    </section>
  )
}

export function ResultsView({
  day,
  rows,
  cal,
}: {
  day: string
  rows: PmResult[]
  cal: { bands: CalibrationBand[]; since: string | null; total: number }
}) {
  const mounted = useMounted()
  const f = useOddsFormat()
  const [thin, setThin] = useState(false)
  const us = mounted && isUsViewer()

  // The reader's own date once hydrated; the UTC date before that.
  const own = useMemo(
    () => rows.filter((r) => (mounted ? localDate(new Date(r.kickoff)) : r.kickoff.slice(0, 10)) === day),
    [rows, day, mounted]
  )
  const funded = own.filter((r) => (r.volume ?? 0) >= MIN_VOLUME)
  const thinCount = own.length - funded.length
  const listed = thin ? own : funded

  const settled = funded.filter((r) => r.p && r.outcome)
  const favs = settled.filter((r) => favSide(r))
  const favWon = favs.filter((r) => favSide(r) === r.outcome).length
  const favExp = favs.reduce((s, r) => s + r.p![favSide(r)!], 0)
  const surprises = settled
    .map((r) => ({ r, p: r.p![r.outcome!] }))
    .sort((a, b) => a.p - b.p)
    .slice(0, 3)

  const groups = useMemo(() => {
    const m = new Map<string, PmResult[]>()
    for (const r of listed) {
      const k = r.competition ?? 'Other'
      m.set(k, [...(m.get(k) ?? []), r])
    }
    const vol = (xs: PmResult[]) => xs.reduce((s, r) => s + (r.volume ?? 0), 0)
    return Array.from(m.entries())
      .sort((a, b) => vol(b[1]) - vol(a[1]))
      .map(([name, xs]) => ({ name, rows: xs.sort((a, b) => a.kickoff.localeCompare(b.kickoff)) }))
  }, [listed])

  const isYesterday = mounted && day === yesterdayLocal()
  const canNext = mounted && day < yesterdayLocal()

  return (
    <div className="gc-main rs-page">
      <DayStrip active={isYesterday ? 'yesterday' : null} />

      <header className="tm-hero">
        <div className="rs-daynav">
          <Link href={`/results/${shiftDay(day, -1)}`} className="rs-daynav-a" aria-label="Previous day">
            ‹ {dayLabel(shiftDay(day, -1), us)}
          </Link>
          {canNext && (
            <Link href={`/results/${shiftDay(day, 1)}`} className="rs-daynav-a" aria-label="Next day">
              {dayLabel(shiftDay(day, 1), us)} ›
            </Link>
          )}
        </div>
        <h1>Results · {dayLabel(day, us)}</h1>
        <p className="gc-quiet">
          Every Polymarket soccer game of the day against the price it kicked off at. The price is
          Polymarket&apos;s own at the whistle; the result is how its markets resolved.
        </p>
      </header>

      {own.length === 0 ? (
        <div className="gc-nothing">
          <strong>No results stored for this day.</strong>
          <span>Results are filled once a day, early in the morning UTC, for the two days before.</span>
        </div>
      ) : (
        <>
          <section className="rs-tiles">
            <div className="rs-tile">
              <span>Games</span>
              <b className="np-num">{funded.length}</b>
              <em>{thinCount ? `+${thinCount} on thin books` : 'all funded'}</em>
            </div>
            <div className="rs-tile">
              <span>Favourites won</span>
              <b className="np-num">
                {favWon} <i>of {favs.length}</i>
              </b>
              <em>the prices expected {favExp.toFixed(1)}</em>
            </div>
          </section>

          {surprises.length > 0 && (
            <section className="gc-section">
              <h2 className="gc-h2">What the market gave least</h2>
              <div className="rs-surprises">
                {surprises.map(({ r, p }) => {
                  const fs = favSide(r)
                  return (
                    <Link key={r.slug} href={`/game/${r.slug}`} className="rs-card">
                      <span className="rs-card-comp">{r.competition}</span>
                      <span className="rs-card-teams">
                        {r.home} <b className="np-num">{r.score ?? '–'}</b> {r.away}
                      </span>
                      <span className="rs-card-line">
                        {fs ? (
                          <>
                            The market gave {nameOf(r, fs)} <b>{pct(r.p![fs])}</b>.{' '}
                          </>
                        ) : null}
                        {r.outcome === 'draw' ? 'The draw' : nameOf(r, r.outcome!)} was <b>{pct(p)}</b>.
                      </span>
                      <span className="rs-card-vol">{money(r.volume)} traded</span>
                    </Link>
                  )
                })}
              </div>
              <p className="gc-chart-note">
                An outcome priced at {surprises[0] ? pct(surprises[0].p) : '15%'} still happens about once in{' '}
                {surprises[0] ? Math.max(2, Math.round(1 / surprises[0].p)) : 7} games, so one result can&apos;t
                say whether the price was wrong. Hundreds can: the table at the foot of the page counts how
                often each price band actually came in, and that is where a mispriced band would show.
              </p>
            </section>
          )}

          <section className="gc-section">
            <h2 className="gc-h2">Every game</h2>
            <p className="rs-legend gcx-dim">
              1 · X · 2 at kick-off, in your odds format; the one that happened is marked. The last figure is
              what the market gave it.
            </p>
            {groups.map((g) => (
              <div key={g.name} className="rs-group">
                <h3 className="rs-comp">{g.name}</h3>
                <ul className="rs-list">
                  {g.rows.map((r) => (
                    <Row key={r.slug} r={r} f={f} mounted={mounted} />
                  ))}
                </ul>
              </div>
            ))}
            {thinCount > 0 && (
              <button className="gc-more" onClick={() => setThin((v) => !v)}>
                {thin ? 'hide thin books' : `show ${thinCount} more on books under $1k traded`}
              </button>
            )}
          </section>
        </>
      )}

      <Calibration cal={cal} />

      <section className="gc-section">
        <p className="np-note">
          <strong>What a favourite&apos;s record at its price has been tested for, and what it hasn&apos;t.</strong>{' '}
          One test, on clubs: across 4,455 club seasons in 22 leagues, how far a club beat or missed Pinnacle&apos;s
          closing prices in one season did not carry into the next (correlation 0.004; as a 60–75% favourite,
          0.04 over 646 pairs). That is one method against the sharpest closing line — not a finding that
          Polymarket prices favourites right. National teams are untested: we don&apos;t yet hold their
          historical odds, so their records here count only Polymarket&apos;s own games, and whether famous
          national sides are overpriced as favourites is an open question we are loading data to answer.
        </p>
      </section>
    </div>
  )
}
