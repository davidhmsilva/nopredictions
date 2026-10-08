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

/** Regenerated at most once a minute (ISR), from the boards the publisher
 *  writes (lib/remoteBoard), so the games are in the HTML and a crawler
 *  walking the site does not cost a render per hit.
 *
 *  ⚠️ This only works because the board read is a cacheable fetch. With the
 *     old in-process sweep (`cache: 'no-store'`) Next abandoned the static
 *     render and the page was built EMPTY — /nfl with 0 games in its HTML. */
export const revalidate = 60

export default async function SoccerPage() {
  const board = await within(getBoard(), 3000)
  return <SoccerBoard initial={board?.fixtures ?? null} />
}
