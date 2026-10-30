import type { Metadata } from 'next'
import { AppShell } from '../components/AppShell'
import { listPublished } from '../lib/agents'
import { within } from '../lib/deadline'
import { MarketplaceView } from './MarketplaceView'

export const metadata: Metadata = {
  title: 'Marketplace: copy an AI betting agent — NOPREDICTIONS',
  description:
    'AI agents built from people’s own betting ideas, each with its strategy and its full paper-trading record. Copy one and it runs in your account.',
  alternates: { canonical: '/marketplace' },
}

/** Re-rendered at most once a minute: a record moves when a game settles. */
export const revalidate = 60

/** How long the server waits for the list. Past it the browser asks instead. */
const SERVER_WAIT_MS = 3000

export default async function MarketplacePage() {
  return (
    <AppShell>
      <div className="np-wrap">
        <MarketplaceView initial={await within(listPublished(), SERVER_WAIT_MS)} />
      </div>
    </AppShell>
  )
}
