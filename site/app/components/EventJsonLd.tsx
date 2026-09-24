/** Structured data for one game — schema.org's SportsEvent — so a search
 *  engine reads the teams, the start and the venue without running the page. */

export interface EventLd {
  name: string
  sport: string
  start: string
  url: string
  home: string
  away: string
  venue?: string | null
  description?: string
}

export function EventJsonLd({ e }: { e: EventLd }) {
  const data = {
    '@context': 'https://schema.org',
    '@type': 'SportsEvent',
    name: e.name,
    sport: e.sport,
    startDate: e.start,
    eventStatus: 'https://schema.org/EventScheduled',
    url: e.url,
    homeTeam: { '@type': 'SportsTeam', name: e.home },
    awayTeam: { '@type': 'SportsTeam', name: e.away },
    competitor: [
      { '@type': 'SportsTeam', name: e.home },
      { '@type': 'SportsTeam', name: e.away },
    ],
    ...(e.venue ? { location: { '@type': 'Place', name: e.venue } } : {}),
    ...(e.description ? { description: e.description } : {}),
  }
  // "<" escaped so nothing in a team name can close the script tag.
  const json = JSON.stringify(data).replace(/</g, '\\u003c')
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: json }} />
}

/** What schema.org calls each sport. */
export const SPORT_LD: Record<string, string> = {
  soccer: 'Soccer',
  nfl: 'American football',
  cfb: 'American football',
  mlb: 'Baseball',
  nba: 'Basketball',
  wnba: 'Basketball',
  nhl: 'Ice hockey',
}

const ET_DAY = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric' })
const ET_FULL = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

/** Dates for titles and descriptions, written server-side in US Eastern — the
 *  zone the site's quota day and its US audience already use. */
export function etDay(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : ET_DAY.format(d)
}
export function etFull(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : `${ET_FULL.format(d)} ET`
}
