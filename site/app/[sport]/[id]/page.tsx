import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { SportGameView } from '../../components/SportGameView'
import { SPORT_META, isSportKey } from '../../lib/sportsMeta'

// The Game Center for a US game: /nfl/<ESPN event id>. The id is ESPN's
// because ESPN is the schedule both exchanges are placed on.

export function generateMetadata({ params }: { params: { sport: string; id: string } }): Metadata {
  if (!isSportKey(params.sport)) return {}
  const { label } = SPORT_META[params.sport]
  const title = `${label} game odds: Polymarket vs Kalshi — NOPREDICTIONS`
  const description = `Both apps' prices on this ${label} game, the better one marked after fees, every other market, and the matchup file.`
  return {
    title,
    description,
    alternates: { canonical: `/${params.sport}/${params.id}` },
    openGraph: { title, description, type: 'website', siteName: 'NOPREDICTIONS' },
  }
}

export default function SportGamePage({ params }: { params: { sport: string; id: string } }) {
  if (!isSportKey(params.sport) || !/^\d{4,14}$/.test(params.id)) notFound()
  return <SportGameView sport={params.sport} id={params.id} />
}
