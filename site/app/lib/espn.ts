/** ESPN's public soccer scoreboard — the live clock the board cannot give us.
 *
 *  Scout could say a fixture was LIVE only when a market on its board had
 *  already resolved, which on a goalless first half is nothing at all. Every
 *  other started match got `KICKED OFF?` — honest, and useless to look at.
 *
 *  ESPN answers it outright: state, minute and score, no key and no quota, one
 *  request per LEAGUE rather than per fixture.
 *
 *  ⚠️ DO NOT SET A USER-AGENT. Measured 2026-09-06: curl's default and
 *  python-requests' default are served 200, while a browser string and a custom
 *  `nopredictions/1.0 (+url)` are both 403. The edge rejects browser-shaped and
 *  custom agents. `fetch` sends its own default and is served; adding a polite
 *  header is the one thing that breaks this.
 *
 *  ⚠️ This is ESPN's website backend, not a product they sell. No docs, no SLA.
 *  Everything here fails soft: a league that does not answer contributes
 *  nothing, and Scout falls back to the board evidence it always had.
 */

import { teamScore } from './gamecenter'

const BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer'

/** The competitions Polymarket actually lists a board for, plus the majors.
 *  A code that does not exist returns nothing and costs one failed request. */
const LEAGUES = [
  'usa.1', 'eng.1', 'eng.2', 'eng.3', 'eng.4', 'esp.1', 'esp.2',
  'ita.1', 'ita.2', 'ger.1', 'ger.2', 'fra.1', 'fra.2', 'ned.1', 'ned.2',
  'por.1', 'bel.1', 'tur.1', 'sui.1', 'aut.1', 'sco.1', 'nor.1', 'swe.1',
  'den.1', 'mex.1', 'bra.1', 'bra.2', 'arg.1', 'jpn.1', 'kor.1', 'chi.1',
  'col.1', 'ecu.1', 'per.1', 'uru.1', 'par.1', 'gre.1', 'rus.1', 'ukr.1',
  'pol.1', 'cze.1', 'rou.1', 'isr.1', 'aus.1', 'chn.1', 'ind.1',
  'uefa.champions', 'uefa.europa', 'uefa.europa.conf', 'conmebol.libertadores',
]

/** The same bar the fixture matcher uses. Below it, two names are not the same
 *  club — and a wrong match here puts another game's clock on this card. */
const MIN_SIDE_SCORE = 0.6

export interface EspnLive {
  home: string
  away: string
  /** Stoppage folded in: 90'+5' is 95, because a card showing 90' for both is
   *  telling you the same thing about two different moments. */
  minute: number | null
  homeGoals: number
  awayGoals: number
  finished: boolean
}

function minuteOf(status: Record<string, unknown>): number | null {
  const raw = String(status.displayClock ?? '').trim()
  if (raw) {
    const parts = raw.replace(/'/g, ' ').replace(/\+/g, ' ').split(/\s+/).filter((p) => /^\d+$/.test(p))
    if (parts.length) return parts.reduce((s, p) => s + parseInt(p, 10), 0)
  }
  const secs = status.clock
  return typeof secs === 'number' && secs > 0 ? Math.floor(secs / 60) : null
}

async function fetchLeague(code: string): Promise<EspnLive[]> {
  try {
    const res = await fetch(`${BASE}/${code}/scoreboard`, {
      signal: AbortSignal.timeout(8000),
      cache: 'no-store',
    })
    if (!res.ok) return []
    const body = (await res.json()) as { events?: Array<Record<string, unknown>> }

    const out: EspnLive[] = []
    for (const ev of body.events ?? []) {
      const comp = ((ev.competitions as Array<Record<string, unknown>>) ?? [])[0]
      if (!comp) continue
      const sides = (comp.competitors as Array<Record<string, unknown>>) ?? []
      if (sides.length !== 2) continue

      // The homeAway flag, never the array order — reading the order puts the
      // score the wrong way round the day ESPN lists them reversed.
      const home = sides.find((s) => s.homeAway === 'home') ?? sides[0]
      const away = sides.find((s) => s.homeAway === 'away') ?? sides[1]
      const status = (comp.status as Record<string, unknown>) ?? {}
      const type = (status.type as Record<string, unknown>) ?? {}
      const state = String(type.state ?? '')
      if (state !== 'in' && state !== 'post') continue

      out.push({
        home: String(((home.team as Record<string, unknown>) ?? {}).displayName ?? ''),
        away: String(((away.team as Record<string, unknown>) ?? {}).displayName ?? ''),
        minute: state === 'in' ? minuteOf(status) : null,
        homeGoals: parseInt(String(home.score ?? '0'), 10) || 0,
        awayGoals: parseInt(String(away.score ?? '0'), 10) || 0,
        finished: state === 'post',
      })
    }
    return out
  } catch {
    // A league that will not answer contributes nothing. It must never take the
    // board down with it — Scout worked before this file existed.
    return []
  }
}

/** Every match ESPN has in play or just finished, across the mapped leagues. */
export async function fetchEspnLive(): Promise<EspnLive[]> {
  const settled = await Promise.allSettled(LEAGUES.map(fetchLeague))
  return settled.flatMap((r) => (r.status === 'fulfilled' ? r.value : []))
}

/** The ESPN match for a Polymarket pair, or null.
 *
 *  Alias-aware scoring on both sides, never substring containment: "Real
 *  Madrid" and "Real Sociedad" share a word, and the first-word match this
 *  replaces once paired River Plate with Platense. A tie returns null — two
 *  fixtures scoring the same means the names cannot separate them.
 */
export function matchEspn(home: string, away: string, pool: EspnLive[]): EspnLive | null {
  const scored = pool
    .map((e) => {
      const h = teamScore(home, e.home)
      const a = teamScore(away, e.away)
      return { e, score: Math.min(h, a) < MIN_SIDE_SCORE ? 0 : (h + a) / 2 }
    })
    .filter((x) => x.score > 0)
    .sort((x, y) => y.score - x.score)

  if (!scored.length) return null
  if (scored.length > 1 && Math.abs(scored[0].score - scored[1].score) < 1e-9) return null
  return scored[0].e
}
