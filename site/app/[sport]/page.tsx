import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { SportBoard } from '../components/SportBoard'
import { SPORT_KEYS, SPORT_META, isSportKey } from '../lib/sportsMeta'

// One page per US sport: /nfl, /cfb, /mlb, /nba, /nhl, /wnba. Anything else at
// the top level still 404s — the static routes (lab, pricing, …) win over this
// segment, and `dynamicParams = false` refuses every name not listed here.
export const dynamicParams = false

export function generateStaticParams() {
  return SPORT_KEYS.map((sport) => ({ sport }))
}

export function generateMetadata({ params }: { params: { sport: string } }): Metadata {
  if (!isSportKey(params.sport)) return {}
  const { label } = SPORT_META[params.sport]
  const title = `${label} on Kalshi and Polymarket, side by side — NOPREDICTIONS`
  const description = `Every ${label} game on Kalshi and Polymarket: both moneylines at the ask, the book behind each, and which venue is cheaper. American, decimal or implied odds. No tips.`
  const url = `https://nopredictions.com/${params.sport}`
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

export default function SportPage({ params }: { params: { sport: string } }) {
  if (!isSportKey(params.sport)) notFound()
  return <SportBoard sport={params.sport} />
}
