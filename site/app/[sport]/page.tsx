import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { SportBoard } from '../components/SportBoard'
import { getSportBoard } from '../lib/sports'
import { within } from '../lib/deadline'
import { SPORT_KEYS, SPORT_META, isSportKey } from '../lib/sportsMeta'

// One page per US sport: /nfl, /cfb, /mlb, /nba, /nhl, /wnba. Anything else at
// the top level 404s below — the static routes (lab, pricing, …) win over this
// segment, and a name that is not a sport is refused before any work is done.

/** Regenerated at most once a minute (ISR), from the boards the publisher
 *  writes (lib/remoteBoard), so the games are in the HTML and a crawler
 *  walking the site does not cost a render per hit.
 *
 *  ⚠️ This only works because the board read is a cacheable fetch. With the
 *     old in-process sweep (`cache: 'no-store'`) Next abandoned the static
 *     render and the page was built EMPTY — /nfl with 0 games in its HTML. */
export const revalidate = 60

/** The six boards are prerendered and regenerated like the home page. */
export function generateStaticParams() {
  return SPORT_KEYS.map((sport) => ({ sport }))
}

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
  const initial = await within(getSportBoard(params.sport), 3000)
  return <SportBoard sport={params.sport} initial={initial} />
}
