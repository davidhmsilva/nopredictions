import type { Metadata } from 'next'
import { HomeBoard } from './components/HomeBoard'
import { rankRows, rowFromScout, rowFromSportGame } from './lib/boardRow'
import { getBoard } from './lib/scoutCache'
import { getSportBoard } from './lib/sports'
import { SPORT_KEYS } from './lib/sportsMeta'

export const metadata: Metadata = { alternates: { canonical: '/' } }

/** Rebuilt at most once a minute, from the same caches the boards read. */
export const revalidate = 60

/** How many of the biggest games the server puts in the page. Enough for the
 *  cards, the top ten and a search engine; the browser loads the rest. */
const SERVER_ROWS = 40

/** Home: the biggest games in every sport, then a way into each one. */
export default async function HomePage() {
  const [soccer, ...sports] = await Promise.all([
    getBoard().catch(() => null),
    ...SPORT_KEYS.map((k) => getSportBoard(k).catch(() => null)),
  ])
  const rows = rankRows([
    ...(soccer?.fixtures ?? []).map(rowFromScout),
    ...sports.flatMap((b, i) => (b?.games ?? []).map((g) => rowFromSportGame(g, SPORT_KEYS[i]))),
  ]).slice(0, SERVER_ROWS)
  return <HomeBoard initial={rows.length ? rows : null} />
}
