import type { MetadataRoute } from 'next'
import { articleSlugs } from './lib/insights'
import { getBoard } from './lib/scoutCache'
import { getSportBoard } from './lib/sports'
import { SPORT_KEYS } from './lib/sportsMeta'
import { within } from './lib/deadline'

/** Every page worth finding: the boards, every game on them right now, the
 *  articles, and the tools. Games come and go daily, so the list is rebuilt
 *  every hour from the same caches the boards read — no extra sweep.
 *
 *  A board that fails to load costs its games, not the sitemap. */
export const dynamic = 'force-dynamic'
// See app/page.tsx: a static build of this file would swallow the board reads
// and list no games.

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
    page('/insights', 'weekly', 0.6),
    ...articleSlugs().map((s) => page(`/insights/${s}`, 'monthly', 0.5)),
    page('/lab', 'monthly', 0.6),
    page('/pricing', 'monthly', 0.5),
    page('/terms', 'yearly', 0.1),
    page('/privacy', 'yearly', 0.1),
    page('/refunds', 'yearly', 0.1),
  ]

  const [soccer, ...sports] = await Promise.all([
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

  return [...fixed, ...games]
}
