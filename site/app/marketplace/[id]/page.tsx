import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { cache } from 'react'
import { AppShell } from '../../components/AppShell'
import { within } from '../../lib/deadline'
import { publishedAgent } from '../../lib/marketplaceCache'
import { PublicAgent } from './PublicAgent'

/** Re-rendered at most once a minute, on demand: an agent's page is shared as
 *  a link, so it should arrive with its record in the HTML. */
export const revalidate = 60

/** How long the server waits for the agent. Past it the browser asks instead. */
const SERVER_WAIT_MS = 4000

function idOf(raw: string): number | null {
  const id = Number(raw)
  return Number.isInteger(id) && id > 0 ? id : null
}

// One read for the metadata and the page. Wrapped, because `within` answers
// null when time runs out, and only a real "no such agent" may become a 404.
const load = cache((id: number) => within(publishedAgent(id).then((detail) => ({ detail })), SERVER_WAIT_MS))

export async function generateMetadata({ params }: { params: { id: string } }): Promise<Metadata> {
  const id = idOf(params.id)
  const r = id ? await load(id) : null
  if (!r?.detail) return { title: 'An AI betting agent — NOPREDICTIONS' }
  const a = r.detail.agent
  const settled = a.wins + a.losses
  const record = settled
    ? `${settled} settled paper bets, ${a.pl_units >= 0 ? '+' : '−'}${Math.abs(a.pl_units).toFixed(2)} units.`
    : 'No settled bets yet.'
  // The operator's agents are shown as NOPREDICTIONS; the site's name once.
  const by = a.author === 'NOPREDICTIONS' ? 'by NOPREDICTIONS' : `by ${a.author} · NOPREDICTIONS`
  return {
    title: `${a.name} — an AI betting agent ${by}`,
    description: `${a.interpretation ?? a.theory ?? a.name} ${record} Copy it and it runs in your account.`,
    alternates: { canonical: `/marketplace/${a.id}` },
  }
}

export default async function PublishedAgentPage({ params }: { params: { id: string } }) {
  const id = idOf(params.id)
  if (!id) notFound()
  const r = await load(id)
  if (r && !r.detail) notFound()
  return (
    <AppShell>
      <div className="gc-main">
        <PublicAgent id={id} initial={r?.detail ?? null} />
      </div>
    </AppShell>
  )
}
