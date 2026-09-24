import type { Metadata } from 'next'
import { EventJsonLd, SPORT_LD, etDay, etFull } from '../../components/EventJsonLd'
import { priceText } from '../../lib/priceFormat'
import { loadFixture } from '../../lib/matchcontext'
import { getBoard } from '../../lib/scoutCache'
import { within } from '../../lib/deadline'

// The soccer Game Center's page is a client component and cannot export
// metadata, so the title, the description and the event's structured data
// live here. They come from the board the page was clicked from (cached, no
// extra request) and from Polymarket's event only when the board no longer
// lists the game.

interface Fx {
  home: string
  away: string
  competition: string | null
  kickoff: string | null
  prices: { home: number | null; draw: number | null; away: number | null }
}

async function fixture(slug: string): Promise<Fx | null> {
  const board = await within(getBoard(), 2500)
  const f = board?.fixtures.find((x) => x.slug === slug)
  if (f) {
    return {
      home: f.home,
      away: f.away,
      competition: f.competition,
      kickoff: f.kickoff,
      prices: {
        home: f.best?.home?.ask ?? null,
        draw: f.best?.draw?.ask ?? null,
        away: f.best?.away?.ask ?? null,
      },
    }
  }
  const fx = await within(loadFixture(slug), 2500)
  return fx
    ? {
        home: fx.home,
        away: fx.away,
        competition: fx.competition,
        kickoff: fx.kickoff,
        prices: { home: null, draw: null, away: null },
      }
    : null
}

const dec = (p: number | null) => (p != null && p > 0.01 && p < 0.99 ? priceText(p, 'decimal') : null)

export async function generateMetadata({ params }: { params: { slug: string } }): Promise<Metadata> {
  const url = `/game/${params.slug}`
  const f = await fixture(params.slug)
  if (!f) return { title: 'Match odds: Polymarket vs Kalshi — NOPREDICTIONS', alternates: { canonical: url } }
  const day = etDay(f.kickoff)
  const title = `${f.home} vs ${f.away} Odds${day ? ` (${day})` : ''} — Polymarket vs Kalshi | NOPREDICTIONS`
  const [h, d, a] = [dec(f.prices.home), dec(f.prices.draw), dec(f.prices.away)]
  const description =
    `${f.home} v ${f.away}${f.competition ? `, ${f.competition}` : ''}${f.kickoff ? `, ${etFull(f.kickoff)}` : ''}. ` +
    (h && d && a ? `Odds ${f.home} ${h}, draw ${d}, ${f.away} ${a}. ` : '') +
    `Polymarket and Kalshi side by side with the better price marked after fees, plus form, stats, line-ups and every market.`
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: { title, description, url, type: 'website', siteName: 'NOPREDICTIONS' },
    twitter: { card: 'summary', title, description },
  }
}

export default async function GameLayout({
  children,
  params,
}: {
  children: React.ReactNode
  params: { slug: string }
}) {
  const f = await fixture(params.slug)
  return (
    <>
      {f?.kickoff && (
        <EventJsonLd
          e={{
            name: `${f.home} vs ${f.away}`,
            sport: SPORT_LD.soccer,
            start: f.kickoff,
            url: `https://www.nopredictions.com/game/${params.slug}`,
            home: f.home,
            away: f.away,
            description: f.competition ?? undefined,
          }}
        />
      )}
      {children}
    </>
  )
}
