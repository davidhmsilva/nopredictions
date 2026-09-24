import type { Metadata } from 'next'
import { SoccerBoard } from '../components/SoccerBoard'
import { getBoard } from '../lib/scoutCache'

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

/** Rebuilt at most once a minute, so the games are in the HTML. */
export const revalidate = 60

export default async function SoccerPage() {
  const board = await getBoard().catch(() => null)
  return <SoccerBoard initial={board?.fixtures ?? null} />
}
