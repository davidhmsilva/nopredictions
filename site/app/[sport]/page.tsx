import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { SportBoard } from '../components/SportBoard'
import { getSportBoard } from '../lib/sports'
import { SPORT_META, isSportKey } from '../lib/sportsMeta'

// One page per US sport: /nfl, /cfb, /mlb, /nba, /nhl, /wnba. Anything else at
// the top level 404s below — the static routes (lab, pricing, …) win over this
// segment, and a name that is not a sport is refused before any work is done.

/** Rendered per request, from the same shared caches the boards read (a
 *  minute old at most), so the games are in the HTML.
 *
 *  ⚠️ Not ISR. The board sweeps fetch with `cache: 'no-store'`, which makes
 *     Next abandon a static render — and the `.catch` around the board read
 *     swallowed that signal, so the page was built EMPTY and served empty to
 *     every crawler. Measured on the first deploy: /nfl with 0 games in its
 *     HTML, and a sitemap with no games at all. */
export const dynamic = 'force-dynamic'

export function generateMetadata({ params }: { params: { sport: string } }): Metadata {
  if (!isSportKey(params.sport)) return {}
  const { label } = SPORT_META[params.sport]
  const title = `${label} odds: Polymarket vs Kalshi — NOPREDICTIONS`
  const description = `Every ${label} game on Polymarket and Kalshi, side by side, with the better price marked after fees. American, decimal or implied odds. Free.`
  const url = `https://www.nopredictions.com/${params.sport}`
  return {
    title,
    description,
    alternates: { canonical: `/${params.sport}` },
    openGraph: {
      title,
      description,
      url,
      type: 'website',
      siteName: 'NOPREDICTIONS',
      images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
    },
    twitter: { card: 'summary_large_image', title, description, images: ['/banner.jpg'] },
  }
}

export default async function SportPage({ params }: { params: { sport: string } }) {
  if (!isSportKey(params.sport)) notFound()
  const initial = await getSportBoard(params.sport).catch(() => null)
  return <SportBoard sport={params.sport} initial={initial} />
}
