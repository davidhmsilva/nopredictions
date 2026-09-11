// The pressure our agent recorded for this fixture, minute by minute.
//
// Joined on Polymarket's own event title, which the pressure daemon writes on
// every row of a listed fixture — no name matching to get wrong. The SIDES are
// then read from api-football's home/away on those rows, never from the order
// of the title: a side error here would draw one team's pressure under the
// other's name.
//
// ⚠️ This is a picture of who is on top, not a forecast. The whole box-score
// reading adds +0.0008 pseudo-R² over free match state, and the price carries
// ~16× more (finding-live-reading-ceiling). The page says that beside it.

import { getSql } from './db'
import { teamScore } from './gamecenter'

export interface MomentumPoint {
  minute: number
  home: number
  away: number
  homeGoals: number
  awayGoals: number
}

export interface Momentum {
  points: MomentumPoint[]
  source: 'api-football' | 'espn' | 'mixed'
  estimatedXg: boolean
}

export async function fetchMomentum(title: string, home: string): Promise<Momentum | null> {
  const sql = getSql()
  const rows = await sql<
    { minute: number; h: number; a: number; hg: number; ag: number; home: string; away: string; src: string | null; has_xg: boolean | null }[]
  >`
    select minute,
           max(home_danger)::float h, max(away_danger)::float a,
           max(home_goals)::int hg, max(away_goals)::int ag,
           max(home) home, max(away) away,
           max(stats_source) src, bool_and(has_xg) has_xg
      from pressure_observations
     where event_title = ${title}
       and observed_at > now() - interval '5 hours'
       and home_danger is not null and away_danger is not null
     group by minute
     order by minute
  `
  if (rows.length < 3) return null

  const first = rows[0]
  const swapped = teamScore(home, first.away) > teamScore(home, first.home)
  const sources = new Set(rows.map((r) => r.src ?? 'api-football'))

  return {
    points: rows.map((r) => ({
      minute: r.minute,
      home: swapped ? r.a : r.h,
      away: swapped ? r.h : r.a,
      homeGoals: swapped ? r.ag : r.hg,
      awayGoals: swapped ? r.hg : r.ag,
    })),
    source: sources.size > 1 ? 'mixed' : sources.has('espn') ? 'espn' : 'api-football',
    estimatedXg: rows.some((r) => r.has_xg === false),
  }
}
