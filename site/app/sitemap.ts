import type { MetadataRoute } from 'next'
import { within } from './lib/deadline'
import { publishedAgents } from './lib/marketplaceCache'

/** Every page worth finding: home, Create, the Marketplace and every agent
 *  published on it, pricing and the legal pages. A user's own agents
 *  (/agent/…) are private and never listed.
 *
 *  Rebuilt at most once an hour. A database that does not answer costs the
 *  agent pages, not the sitemap. */
export const revalidate = 3600

const SITE = 'https://www.nopredictions.com'

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date()
  const page = (
    path: string,
    changeFrequency: MetadataRoute.Sitemap[number]['changeFrequency'],
    priority: number,
    lastModified: Date = now
  ) => ({ url: `${SITE}${path}`, lastModified, changeFrequency, priority })

  const fixed: MetadataRoute.Sitemap = [
    page('/', 'daily', 1),
    page('/create', 'weekly', 0.9),
    page('/marketplace', 'hourly', 0.9),
    page('/pricing', 'monthly', 0.5),
    page('/terms', 'yearly', 0.1),
    page('/privacy', 'yearly', 0.1),
    page('/refunds', 'yearly', 0.1),
  ]

  const published = await within(publishedAgents(), 20_000)
  const agents: MetadataRoute.Sitemap = (published ?? []).map((a) =>
    page(`/marketplace/${a.id}`, 'daily', 0.7, new Date(a.last_bet_at ?? a.published_at ?? a.created_at))
  )

  return [...fixed, ...agents]
}
