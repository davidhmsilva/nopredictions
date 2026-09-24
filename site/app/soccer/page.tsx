import type { Metadata } from 'next'
import { SoccerBoard } from '../components/SoccerBoard'
import { getBoard } from '../lib/scoutCache'
import { within } from '../lib/deadline'

// Every soccer game on the board — what the home page lists the top ten of.
// A static route, so it wins over the [sport] segment the US sports use.

const title = 'Soccer odds: Polymarket vs Kalshi — NOPREDICTIONS'
const description =
  'Every soccer game on Polymarket and Kalshi, side by side, with the better price marked after fees. Biggest games first, and a full match page for each. Free.'

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: '/soccer' },
  openGraph: {
    title,
    description,
    url: 'https://www.nopredictions.com/soccer',
    type: 'website',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: { card: 'summary_large_image', title, description, images: ['/banner.jpg'] },
}

/** Rendered per request, from the same shared caches the boards read (a
 *  minute old at most), so the games are in the HTML.
 *
 *  ⚠️ Not ISR. The board sweeps fetch with `cache: 'no-store'`, which makes
 *     Next abandon a static render — and the `.catch` around the board read
 *     swallowed that signal, so the page was built EMPTY and served empty to
 *     every crawler. Measured on the first deploy: /nfl with 0 games in its
 *     HTML, and a sitemap with no games at all. */
export const dynamic = 'force-dynamic'

export default async function SoccerPage() {
  const board = await within(getBoard(), 3000)
  return <SoccerBoard initial={board?.fixtures ?? null} />
}
