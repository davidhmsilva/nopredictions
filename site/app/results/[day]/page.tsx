import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { AppShell } from '../../components/AppShell'
import { addDays, calibration, isDay, readRange, type PmResult } from '../../lib/pmResults'
import { ResultsView } from './ResultsView'

/** One day of finished Polymarket football, every game against the price it
 *  kicked off at. The server sends the day with 14h of margin on each side;
 *  the browser keeps the games that fall on this date in the READER's zone. */
export const revalidate = 600

const MARGIN_BEFORE_H = 14
const MARGIN_AFTER_H = 12

async function load(day: string) {
  const from = new Date(Date.parse(`${day}T00:00:00Z`) - MARGIN_BEFORE_H * 3600_000).toISOString()
  const to = new Date(Date.parse(`${addDays(day, 1)}T00:00:00Z`) + MARGIN_AFTER_H * 3600_000).toISOString()
  const [rows, cal] = await Promise.all([readRange(from, to), calibration()])
  return { rows, cal }
}

/** The day's line, on the UTC day — what a link preview can know. */
function summary(rows: PmResult[], day: string) {
  const own = rows.filter((r) => r.kickoff.slice(0, 10) === day && r.p && r.outcome && (r.volume ?? 0) >= 1000)
  const fav = own.filter((r) => Math.max(r.p!.home, r.p!.away) >= 0.5)
  const won = fav.filter((r) => (r.p!.home >= r.p!.away ? 'home' : 'away') === r.outcome).length
  const exp = fav.reduce((s, r) => s + Math.max(r.p!.home, r.p!.away), 0)
  const upset = own
    .map((r) => ({ r, p: r.p![r.outcome!] }))
    .sort((a, b) => a.p - b.p)[0]
  return { n: own.length, fav: fav.length, won, exp, upset }
}

export async function generateMetadata({ params }: { params: { day: string } }): Promise<Metadata> {
  if (!isDay(params.day)) return { title: 'Results — NOPREDICTIONS' }
  const date = new Date(`${params.day}T12:00:00Z`).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  })
  const title = `Soccer results against the price — ${date} | NOPREDICTIONS`
  let description = `Every Polymarket soccer game of ${date}: the odds at kick-off, the result, and which favourites the market got wrong.`
  try {
    const { rows } = await load(params.day)
    const s = summary(rows, params.day)
    if (s.fav) {
      description =
        `${date}: favourites won ${s.won} of ${s.fav}; the prices expected ${s.exp.toFixed(1)}. ` +
        (s.upset
          ? `Biggest surprise: ${s.upset.r.home} ${s.upset.r.score ?? ''} ${s.upset.r.away}, a ${Math.round(
              s.upset.p * 100
            )}% outcome. `
          : '') +
        'Every game against the price it kicked off at.'
    }
  } catch {
    /* the generic line stands */
  }
  const url = `/results/${params.day}`
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: { title, description, url, type: 'website', siteName: 'NOPREDICTIONS' },
    twitter: { card: 'summary', title, description },
  }
}

export default async function ResultsPage({ params }: { params: { day: string } }) {
  if (!isDay(params.day)) notFound()
  const { rows, cal } = await load(params.day)
  return (
    <AppShell>
      <ResultsView day={params.day} rows={rows} cal={cal} />
    </AppShell>
  )
}
