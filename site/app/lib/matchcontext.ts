// Everything the Game Center knows about a fixture that is NOT the order book:
// the teams from our database, the match from ESPN, the pressure our agent
// recorded. Served by /api/game/context so the page's price panel never waits
// on it, and read by /api/game/brief to write the brief.

import {
  buildGroups,
  buildHeadlines,
  fetchEvent,
  fetchSiblings,
  inferBoardState,
  pmOver25,
  type Headline,
  type MarketGroup,
} from './gamecenter'
import { fetchEspnMatch, type EspnMatch } from './espnMatch'
import { fetchMomentum, type Momentum } from './momentum'
import { buildPricedLike, type PricedLike } from './pricedLike'
import { teamContext, type TeamContext } from './teamform'

export function slugOf(param: string): string {
  // A pasted Polymarket URL as well as a bare slug — how people arrive.
  return (
    param.match(/polymarket\.com\/(?:[a-z]{2}\/)?(?:event|sports\/[^/]+)\/([^/?#]+)/)?.[1] ??
    param.trim()
  )
}

export function extractTeams(title: string): { home: string; away: string } | null {
  const clean = title.replace(/\s+-\s+.*$/, '').trim()
  const m = clean.match(/^(.+?)\s+vs\.?\s+(.+?)$/i)
  return m ? { home: m[1].trim(), away: m[2].trim() } : null
}

export interface Fixture {
  slug: string
  title: string
  home: string
  away: string
  competition: string | null
  kickoff: string | null
}

export async function loadFixture(slug: string): Promise<Fixture | null> {
  const main = await fetchEvent(slug)
  if (!main) return null
  const title = String(main.title ?? '')
  const teams = extractTeams(title)
  if (!teams) return null
  return {
    slug,
    title,
    ...teams,
    competition:
      typeof main.sport === 'object' && main.sport
        ? String((main.sport as Record<string, unknown>).name ?? '') || null
        : null,
    kickoff: main.startTime ? String(main.startTime) : null,
  }
}

export interface MatchContext {
  teams: TeamContext | null
  espn: EspnMatch | null
  momentum: Momentum | null
  /** Parts that failed, by name — a panel that is missing says why. */
  failed: string[]
}

export async function buildMatchContext(fx: Fixture): Promise<MatchContext> {
  const [teams, espn, momentum] = await Promise.allSettled([
    teamContext(fx.home, fx.away),
    fetchEspnMatch(fx.home, fx.away, fx.kickoff),
    fetchMomentum(fx.title, fx.home),
  ])
  const failed: string[] = []
  const val = <T,>(r: PromiseSettledResult<T>, name: string): T | null => {
    if (r.status === 'fulfilled') return r.value
    console.error(`game context: ${name} failed`, r.reason)
    failed.push(name)
    return null
  }
  return {
    teams: val(teams, 'teams'),
    espn: val(espn, 'espn'),
    momentum: val(momentum, 'momentum'),
    failed,
  }
}

/** The board at Gamma mids — no CLOB round trips. Enough for the brief, which
 *  quotes prices as "around", never as something to pay. */
export async function boardAtMids(fx: Fixture): Promise<{
  groups: MarketGroup[]
  headlines: Headline[]
  pricedLike: PricedLike
  started: boolean
}> {
  const siblings = await fetchSiblings(fx.slug, fx.title)
  const main = siblings.length ? siblings : [await fetchEvent(fx.slug)].filter(Boolean)
  const groups = buildGroups(main as Array<Record<string, unknown>>)
  const headlines = buildHeadlines(groups, fx.home, fx.away)
  const board = inferBoardState(groups, fx.home, fx.away)
  const started = board.phase === 'live' || board.phase === 'finished'
  return {
    groups,
    headlines,
    started,
    pricedLike: buildPricedLike({
      groups,
      headlines,
      home: fx.home,
      away: fx.away,
      preOver25: started ? null : pmOver25(groups),
      started,
    }),
  }
}
