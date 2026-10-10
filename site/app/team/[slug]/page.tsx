import type { Metadata } from 'next'
import { notFound, permanentRedirect } from 'next/navigation'
import { AppShell } from '../../components/AppShell'
import { teamPage, upcomingFor, type TeamPageData } from '../../lib/teampage'
import { getBoard } from '../../lib/scoutCache'
import { within } from '../../lib/deadline'
import { idOfSlug, teamHref, teamSlug } from '../../lib/teamSlug'
import { TeamView } from './TeamView'

/** A club's page: /team/<id>-<name>. Rendered on the server from our own
 *  database and cached, so a crawler walking every club costs a query per
 *  club per three hours, not per visit. The next games come from the Scout
 *  board's cache; a board that is slow costs that panel, never the page. */
export const revalidate = 1800

/** No club is built at deploy time; each is rendered on its first visit and
 *  then served from the cache for `revalidate`. Without this, Next renders a
 *  dynamic segment on every request whatever `revalidate` says, and a crawler
 *  walking the sitemap cost ~1.6s of CPU per club per hit (Observability,
 *  2026-10-01→07: 28% of the site's CPU). */
export function generateStaticParams() {
  return []
}

async function load(slug: string): Promise<TeamPageData | null> {
  const id = idOfSlug(slug)
  if (id == null) return null
  // A database error is thrown, not turned into a 404: a cached "not found"
  // for a club that exists is worse than an error page that retries.
  return teamPage(id)
}

export async function generateMetadata({ params }: { params: { slug: string } }): Promise<Metadata> {
  const t = await load(params.slug)
  if (!t) return { title: 'Team not found — NOPREDICTIONS' }
  const url = teamHref(t.id, t.name)
  const s = t.splits.last10
  const title = `${t.name} Form, Stats & Odds${t.league ? ` — ${t.league}` : ''} | NOPREDICTIONS`
  const description =
    `${t.name}${t.league ? ` (${t.league})` : ''}: last ${s.games} W${s.w} D${s.d} L${s.l}, ` +
    `over 2.5 in ${s.o25.k} of ${s.o25.n}, both teams scored in ${s.btts.k}. ` +
    `Every result with the closing price it was played at, the league table against what the prices expected, ` +
    `and the next games on Polymarket and Kalshi.`
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: { title, description, url, type: 'website', siteName: 'NOPREDICTIONS' },
    twitter: { card: 'summary', title, description },
  }
}

export default async function TeamPage({ params }: { params: { slug: string } }) {
  const t = await load(params.slug)
  if (!t) notFound()
  // One URL per club: a missing or stale name part goes to the current one.
  if (params.slug !== teamSlug(t.id, t.name)) permanentRedirect(teamHref(t.id, t.name))

  const board = await within(getBoard(), 2500)
  const upcoming = board ? await within(upcomingFor(t, board.fixtures), 2500) : null

  const ld = {
    '@context': 'https://schema.org',
    '@type': 'SportsTeam',
    name: t.name,
    sport: 'Soccer',
    url: `https://www.nopredictions.com${teamHref(t.id, t.name)}`,
    ...(t.league ? { memberOf: { '@type': 'SportsOrganization', name: t.league } } : {}),
  }

  return (
    <AppShell>
      <script
        type="application/ld+json"
        // "<" escaped so nothing in a team name can close the script tag.
        dangerouslySetInnerHTML={{ __html: JSON.stringify(ld).replace(/</g, '\\u003c') }}
      />
      <TeamView t={t} upcoming={upcoming} />
    </AppShell>
  )
}
