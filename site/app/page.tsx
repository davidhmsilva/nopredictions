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

/** Rendered per request, from the same shared caches the boards read (a
 *  minute old at most), so the games are in the HTML.
 *
 *  ⚠️ Not ISR. The board sweeps fetch with `cache: 'no-store'`, which makes
 *     Next abandon a static render — and the `.catch` around the board read
 *     swallowed that signal, so the page was built EMPTY and served empty to
 *     every crawler. Measured on the first deploy: /nfl with 0 games in its
 *     HTML, and a sitemap with no games at all. */
export const dynamic = 'force-dynamic'

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
