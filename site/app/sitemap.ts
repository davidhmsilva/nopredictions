import type { MetadataRoute } from 'next'
import { articleSlugs } from './lib/insights'
import { getBoard } from './lib/scoutCache'
import { getSportBoard } from './lib/sports'
import { SPORT_KEYS } from './lib/sportsMeta'
import { within } from './lib/deadline'
import { listTeams } from './lib/teampage'
import { teamHref } from './lib/teamSlug'

/** Every page worth finding: the boards, every game on them right now, the
 *  articles, and the tools. Games come and go daily, so the list is rebuilt
 *  every hour from the same caches the boards read — no extra sweep.
 *
 *  A board that fails to load costs its games, not the sitemap. */
// Regenerated at most once an hour. The board reads are cacheable fetches of
// the publisher's files (lib/remoteBoard), so this no longer builds empty —
// see app/page.tsx. A crawler asking for it again inside the hour costs nothing.
export const revalidate = 3600

const SITE = 'https://www.nopredictions.com'

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date()
  const page = (
    path: string,
    changeFrequency: MetadataRoute.Sitemap[number]['changeFrequency'],
    priority: number
  ) => ({ url: `${SITE}${path}`, lastModified: now, changeFrequency, priority })

  const fixed: MetadataRoute.Sitemap = [
    page('/', 'hourly', 1),
    page('/soccer', 'hourly', 0.9),
    ...SPORT_KEYS.map((k) => page(`/${k}`, 'hourly', 0.9)),
    page('/dropping-odds', 'hourly', 0.8),
    page('/teams', 'weekly', 0.7),
    // The last week of results: a finished day never changes once filled.
    ...Array.from({ length: 7 }, (_, i) =>
      page(`/results/${new Date(Date.now() - (i + 1) * 86_400_000).toISOString().slice(0, 10)}`, 'daily', 0.6)
    ),
    page('/insights', 'weekly', 0.6),
    ...articleSlugs().map((s) => page(`/insights/${s}`, 'monthly', 0.5)),
    page('/lab', 'monthly', 0.6),
    page('/pricing', 'monthly', 0.5),
    page('/terms', 'yearly', 0.1),
    page('/privacy', 'yearly', 0.1),
    page('/refunds', 'yearly', 0.1),
  ]

  const [teams, soccer, ...sports] = await Promise.all([
    within(listTeams().catch(() => null), 20_000),
    within(getBoard(), 20_000),
    ...SPORT_KEYS.map((k) => within(getSportBoard(k), 20_000)),
  ])

  const games: MetadataRoute.Sitemap = [
    ...(soccer?.fixtures ?? [])
      .filter((f) => !f.finished)
      .map((f) => page(`/game/${f.slug}`, 'hourly', 0.7)),
    ...sports.flatMap((b, i) =>
      (b?.games ?? []).map((g) => page(`/${SPORT_KEYS[i]}/${g.id}`, 'hourly', 0.7))
    ),
  ]

  const clubs: MetadataRoute.Sitemap = (teams ?? []).map((t) => page(teamHref(t.id, t.name), 'daily', 0.6))

  return [...fixed, ...games, ...clubs]
}
