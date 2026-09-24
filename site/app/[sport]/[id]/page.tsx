import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { EventJsonLd, SPORT_LD, etDay, etFull } from '../../components/EventJsonLd'
import { SportGameView } from '../../components/SportGameView'
import { priceText } from '../../lib/priceFormat'
import { getSportGame } from '../../lib/sportGame'
import { SPORT_META, isSportKey } from '../../lib/sportsMeta'

// The Game Center for a US game: /nfl/<ESPN event id>. The id is ESPN's
// because ESPN is the schedule both exchanges are placed on.
//
// Rendered on the server with the game in it — the teams, the prices, the
// form — so a search for "Giants vs Rams odds" has something to find. The
// browser then refreshes it every minute.

const valid = (p: { sport: string; id: string }) => isSportKey(p.sport) && /^\d{4,14}$/.test(p.id)

async function load(p: { sport: string; id: string }) {
  if (!valid(p) || !isSportKey(p.sport)) return null
  return getSportGame(p.sport, p.id).catch(() => null)
}

/** A moneyline the way a US bettor reads it: +270 / −286. */
function ml(p: number | null | undefined): string | null {
  return p != null && p > 0.01 && p < 0.99 ? priceText(p, 'american') : null
}

export async function generateMetadata({ params }: { params: { sport: string; id: string } }): Promise<Metadata> {
  if (!isSportKey(params.sport)) return {}
  const { label } = SPORT_META[params.sport]
  const url = `/${params.sport}/${params.id}`
  const g = await load(params)
  if (!g) {
    return {
      title: `${label} game odds: Polymarket vs Kalshi — NOPREDICTIONS`,
      alternates: { canonical: url },
    }
  }
  const day = etDay(g.start)
  const title = `${g.away.short} vs ${g.home.short} Odds${day ? ` (${day})` : ''} — Polymarket vs Kalshi | NOPREDICTIONS`
  const a = ml(g.board?.best.away.ask)
  const h = ml(g.board?.best.home.ask)
  const description =
    `${g.away.name} at ${g.home.name}, ${etFull(g.start) ?? ''}${g.venue ? `, ${g.venue}` : ''}. ` +
    (a && h ? `Moneyline ${g.away.short} ${a}, ${g.home.short} ${h}. ` : '') +
    `Polymarket and Kalshi side by side with the better price marked after fees, plus spreads, totals, injuries and form.`
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: { title, description, url, type: 'website', siteName: 'NOPREDICTIONS' },
    twitter: { card: 'summary', title, description },
  }
}

export default async function SportGamePage({ params }: { params: { sport: string; id: string } }) {
  if (!valid(params) || !isSportKey(params.sport)) notFound()
  const g = await load(params)
  return (
    <>
      {g && (
        <EventJsonLd
          e={{
            name: `${g.away.name} at ${g.home.name}`,
            sport: SPORT_LD[params.sport],
            start: g.start,
            url: `https://www.nopredictions.com/${params.sport}/${params.id}`,
            home: g.home.name,
            away: g.away.name,
            venue: g.venue,
          }}
        />
      )}
      <SportGameView sport={params.sport} id={params.id} initial={g} />
    </>
  )
}
