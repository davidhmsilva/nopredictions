import { NextResponse } from 'next/server'
import { searchProfiles } from '../../../lib/walletLeaders'

// Trader names → wallet addresses, through Polymarket's own profile search.
// Nobody knows a 42-character address; people know a name off a leaderboard.

export async function GET(req: Request) {
  const q = (new URL(req.url).searchParams.get('q') ?? '').trim()
  if (q.length < 2 || q.length > 40) return NextResponse.json({ ok: true, profiles: [] })
  try {
    const profiles = await searchProfiles(q.toLowerCase())
    return NextResponse.json(
      { ok: true, profiles },
      { headers: { 'Cache-Control': 'public, s-maxage=600, stale-while-revalidate=3600' } },
    )
  } catch (err) {
    console.error('wallet search', err)
    return NextResponse.json({ ok: false, profiles: [] }, { status: 502 })
  }
}
