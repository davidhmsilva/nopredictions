import type { Metadata } from 'next'
import { SoccerBoard } from '../components/SoccerBoard'

// Every soccer game on the board — what the home page lists the top ten of.
// A static route, so it wins over the [sport] segment the US sports use.

const title = 'Every soccer game on the prediction markets — NOPREDICTIONS'
const description =
  'Every soccer game on the board, biggest markets first: the price, the volume, and a full report on each. American, decimal or implied odds. No tips.'

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: '/soccer' },
  openGraph: {
    title,
    description,
    url: 'https://nopredictions.com/soccer',
    type: 'website',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: { card: 'summary_large_image', title, description, images: ['/banner.jpg'] },
}

export default function SoccerPage() {
  return <SoccerBoard mode="all" />
}
