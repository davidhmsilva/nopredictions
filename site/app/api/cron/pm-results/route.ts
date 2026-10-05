import { NextResponse } from 'next/server'
import {
  addDays,
  computeDay,
  favRecordFor,
  isDay,
  pricedSlugs,
  rowsMissingFav,
  storeFav,
  storeRows,
} from '../../../lib/pmResults'

/** Fills `pm_results` (db/059): every finished Polymarket football fixture of
 *  a UTC day, priced at kick-off, with what happened.
 *
 *  - Vercel Cron calls it daily with no parameters: the two days before today,
 *    so a market resolved late is picked up on the second pass.
 *  - `?day=YYYY-MM-DD` does one day; `?from=…&to=…` walks a range oldest
 *    first (the favourite's record reads earlier rows, so order matters) and
 *    stops inside the time budget, answering with `next` to continue from.
 *  - `?dry=1` computes and returns without touching the database.
 *
 *  When CRON_SECRET is set, only a caller holding it gets in — Vercel Cron
 *  sends it. Without it the route is open, which is tolerable only because it
 *  is idempotent and a re-run of a stored day re-reads no price history. */
export const dynamic = 'force-dynamic'
export const maxDuration = 60

/** Stop starting new work after this much of the 60s. */
const BUDGET_MS = 45_000

export async function GET(request: Request) {
  const secret = process.env.CRON_SECRET
  if (secret && request.headers.get('authorization') !== `Bearer ${secret}`) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 })
  }

  const url = new URL(request.url)
  const dry = url.searchParams.get('dry') === '1'
  const day = url.searchParams.get('day')
  const from = url.searchParams.get('from')
  const to = url.searchParams.get('to')

  let days: string[]
  if (day) {
    if (!isDay(day)) return NextResponse.json({ error: 'day must be YYYY-MM-DD' }, { status: 400 })
    days = [day]
  } else if (from || to) {
    if (!from || !to || !isDay(from) || !isDay(to) || from > to) {
      return NextResponse.json({ error: 'from and to must be YYYY-MM-DD, from <= to' }, { status: 400 })
    }
    days = []
    for (let d = from; d <= to && days.length < 400; d = addDays(d, 1)) days.push(d)
  } else {
    const today = new Date().toISOString().slice(0, 10)
    days = [addDays(today, -2), addDays(today, -1)]
  }

  const t0 = Date.now()
  const left = () => BUDGET_MS - (Date.now() - t0)
  const report: Array<Record<string, unknown>> = []
  let next: string | null = null

  for (const d of days) {
    if (left() < 20_000) {
      next = d
      break
    }
    try {
      const skip = dry ? new Set<string>() : await pricedSlugs(d)
      const rows = await computeDay(d, skip)
      let favs = 0
      if (!dry) {
        for (const r of rows) {
          if (!r.p || skip.has(r.slug) || left() < 8_000) continue
          r.fav = await favRecordFor(r).catch(() => null)
          if (r.fav) favs++
        }
        await storeRows(rows)
        // A run that ran short left some favourites without a record.
        for (const r of await rowsMissingFav(d)) {
          if (left() < 5_000) break
          const f = await favRecordFor(r).catch(() => null)
          if (f) {
            await storeFav(r.slug, f)
            favs++
          }
        }
      }
      report.push({
        day: d,
        fixtures: rows.length,
        priced: rows.filter((r) => r.p).length,
        resolved: rows.filter((r) => r.outcome).length,
        alreadyPriced: skip.size,
        favRecords: favs,
        ...(dry ? { rows: rows.slice(0, 200) } : {}),
      })
    } catch (err) {
      report.push({ day: d, error: err instanceof Error ? err.message : String(err) })
      next = d
      break
    }
  }

  return NextResponse.json({ dry, ms: Date.now() - t0, days: report, next })
}
