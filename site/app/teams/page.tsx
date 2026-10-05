import type { Metadata } from 'next'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { listTeams, type TeamIndexEntry } from '../lib/teampage'
import { teamHref } from '../lib/teamSlug'

/** Every club with a league match in the last 120 days, by league. The way
 *  in to the team pages for a reader, and a crawlable path for a search
 *  engine that the sitemap backs up. */
// Dynamic on purpose: the list itself is cached (lib/teampage), and a page
// prerendered at build time without the database would freeze an empty list.
export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Teams — form, stats and odds for every club | NOPREDICTIONS',
  description:
    'Every club in the leagues we cover: recent form, league table, runs worth knowing about, and each result set against the closing price it was played at.',
  alternates: { canonical: '/teams' },
}

export default async function TeamsPage() {
  let teams: TeamIndexEntry[] = []
  try {
    teams = await listTeams()
  } catch {
    teams = []
  }
  const leagues = new Map<string, TeamIndexEntry[]>()
  for (const t of teams) {
    const k = `${t.country ?? ''}|${t.league}`
    const list = leagues.get(k) ?? []
    list.push(t)
    leagues.set(k, list)
  }

  return (
    <AppShell>
      <div className="gc-main">
        <header className="tm-hero">
          <h1>Teams</h1>
          <p className="gc-quiet">
            {teams.length ? `${teams.length} clubs in ${leagues.size} leagues. ` : ''}
            Each page has the club&apos;s form, its league table, and every result set against the
            price it closed at.
          </p>
        </header>
        {teams.length === 0 && <p className="gc-quiet">The team list could not be loaded. Try again in a moment.</p>}
        <div className="tm-index">
          {Array.from(leagues.entries()).map(([k, list]) => (
            <section key={k} className="tm-index-league">
              <h2 className="gc-h2">
                {list[0].league} <span className="gcx-dim">{list[0].country}</span>
              </h2>
              <ul>
                {list.map((t) => (
                  <li key={t.id}>
                    <Link href={teamHref(t.id, t.name)} className="tm-link">{t.name}</Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </AppShell>
  )
}
