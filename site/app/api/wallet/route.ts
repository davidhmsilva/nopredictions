import { NextResponse } from 'next/server'
import { analyseWallet } from '../../lib/wallet'

// A 16k-row wallet is ~33 pages of activity plus a metadata sweep. The walk is
// sliced and run in parallel, but a big account still needs more than the
// default budget, and half an analysis is worse than none.
export const maxDuration = 60
export const dynamic = 'force-dynamic'

const ADDRESS = /^0x[0-9a-fA-F]{40}$/

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url)
  const address = (searchParams.get('address') || '').trim()
  const sinceRaw = searchParams.get('since')

  if (!ADDRESS.test(address)) {
    return NextResponse.json(
      { error: 'Expected a 0x-prefixed 40-character wallet address.' },
      { status: 400 },
    )
  }

  let since: number | null = null
  if (sinceRaw) {
    const t = Date.parse(sinceRaw.length <= 10 ? `${sinceRaw}T00:00:00Z` : sinceRaw)
    if (Number.isNaN(t)) {
      return NextResponse.json({ error: '`since` must be a date, e.g. 2026-07-01.' }, { status: 400 })
    }
    since = Math.floor(t / 1000)
  }

  try {
    const profile = await analyseWallet(address, since)
    return NextResponse.json(profile)
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Unknown error'
    // A wallet with no activity is a normal answer to a normal question, not a
    // server fault — say which it was rather than returning a bare 500.
    const known = /no Polymarket activity|could not be reconstructed/i.test(msg)
    return NextResponse.json({ error: msg }, { status: known ? 404 : 500 })
  }
}
