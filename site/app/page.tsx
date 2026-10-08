import type { Metadata } from 'next'
import { HomeBoard } from './components/HomeBoard'
import { rankRows, rowFromScout, rowFromSportGame } from './lib/boardRow'
import { getBoard } from './lib/scoutCache'
import { getSportBoard } from './lib/sports'
import { SPORT_KEYS } from './lib/sportsMeta'
import { within } from './lib/deadline'

/** How long the server waits for the boards before sending the page anyway. */
const SERVER_WAIT_MS = 3000

export const metadata: Metadata = { alternates: { canonical: '/' } }

/** Regenerated at most once a minute (ISR), from the boards the publisher
 *  writes (lib/remoteBoard), so the games are in the HTML and a crawler
 *  walking the site does not cost a render per hit.
 *
 *  ⚠️ This only works because the board read is a cacheable fetch. With the
 *     old in-process sweep (`cache: 'no-store'`) Next abandoned the static
 *     render and the page was built EMPTY — /nfl with 0 games in its HTML. */
export const revalidate = 60

/** How many of the biggest games the server puts in the page. Enough for the
 *  cards, the top ten and a search engine; the browser loads the rest. */
const SERVER_ROWS = 40

/** Home: the biggest games in every sport, then a way into each one. */
export default async function HomePage() {
  const [soccer, ...sports] = await Promise.all([
    within(getBoard(), SERVER_WAIT_MS),
    ...SPORT_KEYS.map((k) => within(getSportBoard(k), SERVER_WAIT_MS)),
  ])
  const rows = rankRows([
    ...(soccer?.fixtures ?? []).map(rowFromScout),
    ...sports.flatMap((b, i) => (b?.games ?? []).map((g) => rowFromSportGame(g, SPORT_KEYS[i]))),
  ]).slice(0, SERVER_ROWS)
  return <HomeBoard initial={rows.length ? rows : null} />
}
