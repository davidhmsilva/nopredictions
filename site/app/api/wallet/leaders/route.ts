import { NextResponse } from 'next/server'
import { leaders } from '../../../lib/walletLeaders'
import { LEADER_PERIODS, type LeaderPeriod } from '../../../lib/walletTerms'

// Polymarket's sports leaderboard for the Wallet tab. Cached an hour in the
// Data Cache and at the edge: it is the same twelve names for everyone.

export async function GET(req: Request) {
  const raw = new URL(req.url).searchParams.get('period') ?? 'month'
  const period = (LEADER_PERIODS.some((p) => p.id === raw) ? raw : 'month') as LeaderPeriod
  try {
    const rows = await leaders(period)
    return NextResponse.json(
      { ok: true, period, leaders: rows },
      { headers: { 'Cache-Control': 'public, s-maxage=900, stale-while-revalidate=3600' } },
    )
  } catch (err) {
    console.error('wallet leaders', err)
    return NextResponse.json({ ok: false, error: 'Polymarket did not answer.' }, { status: 502 })
  }
}
