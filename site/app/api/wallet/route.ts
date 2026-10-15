import { NextResponse } from 'next/server'
import { analyseWallet, type WalletProfile } from '../../lib/wallet'
import { currentUser } from '../../lib/supabaseAuth'
import { claimUse, refundUse, refusalMessage } from '../../lib/plan'
import { sharedCache } from '../../lib/sharedCache'
import { isFreeWallet } from '../../lib/walletLeaders'

// A 16k-row wallet is ~33 pages of activity plus a metadata sweep. The walk is
// sliced and run in parallel, but a big account still needs more than the
// default budget, and half an analysis is worse than none.
export const maxDuration = 60
export const dynamic = 'force-dynamic'

const ADDRESS = /^0x[0-9a-fA-F]{40}$/

/** The free reads — traders we wrote up, and the leaderboard the page shows —
 *  are the same few dozen wallets for every visitor, so they are analysed at
 *  most twice a day each rather than once per click. In-flight requests for
 *  the same wallet on one instance share one walk of the feed. */
const cachedAnalysis = sharedCache((addr: string) => analyseWallet(addr), ['wallet-free-v1'], {
  revalidate: 12 * 3600,
})
const inFlight = new Map<string, Promise<WalletProfile>>()
function freeAnalysis(addr: string): Promise<WalletProfile> {
  const hit = inFlight.get(addr)
  if (hit) return hit
  const p = cachedAnalysis(addr).finally(() => inFlight.delete(addr))
  inFlight.set(addr, p)
  return p
}

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

  // Free: no account, no use spent. A dated read (`since`) is never free — it
  // is a different analysis from the cached one.
  if (since === null && (await isFreeWallet(address))) {
    try {
      const profile = await freeAnalysis(address.toLowerCase())
      return NextResponse.json({ ...profile, free: true })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      const known = /no Polymarket activity|could not be reconstructed/i.test(msg)
      return NextResponse.json({ error: msg }, { status: known ? 404 : 500 })
    }
  }

  // After the address checks, before the ~33-page walk of Polymarket's feed.
  const user = await currentUser()
  const use = await claimUse(user?.id ?? null, 'wallet')
  if (!use.allowed) {
    return NextResponse.json(
      { error: refusalMessage(use), entitlement: use },
      { status: use.reason === 'signed_out' ? 401 : 402 },
    )
  }

  try {
    const profile = await analyseWallet(address, since)
    return NextResponse.json({ ...profile, entitlement: use })
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Unknown error'
    // A wallet with no activity is a normal answer to a normal question, not a
    // server fault — say which it was rather than returning a bare 500.
    const known = /no Polymarket activity|could not be reconstructed/i.test(msg)
    // A 500 is ours; a 404 is a real answer to a real question. Only the first
    // gets the use back.
    if (!known) await refundUse(user?.id ?? null, 'wallet')
    return NextResponse.json({ error: msg }, { status: known ? 404 : 500 })
  }
}
