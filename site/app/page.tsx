import type { Metadata } from 'next'
import { AppShell } from './components/AppShell'
import { HomeView } from './components/HomeView'
import { listPublished } from './lib/agents'
import { within } from './lib/deadline'

export const metadata: Metadata = { alternates: { canonical: '/' } }

/** Re-rendered at most every five minutes (ISR): the page is the same for
 *  everyone, and the Marketplace's top agents move when games settle. */
export const revalidate = 300

/** How long the server waits for the Marketplace before sending the page. */
const SERVER_WAIT_MS = 3000

/** Home: the idea box, how an idea becomes an agent, and the agents anyone
 *  can copy. The odds boards that were here until 2026-10-28 are gone. */
export default async function HomePage() {
  return (
    <AppShell>
      <HomeView initial={await within(listPublished(), SERVER_WAIT_MS)} />
    </AppShell>
  )
}
