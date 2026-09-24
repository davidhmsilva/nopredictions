/** The written brief on a US Game Center — the soccer brief's writer, given
 *  this game's facts and rules for these sports.
 *
 *  Same contract as lib/matchbrief: Claude sees ONLY the numbers the page
 *  shows, is told to use nothing else, and the page shows the brief beside
 *  them, so a sentence without a number behind it is visible as such. One
 *  brief per game per phase, cached; a failure is thrown, never cached.
 */

import { cachedBriefWith, type Brief } from './matchbrief'
import { SPORT_META } from './sportsMeta'
import type { SportGamePage } from './sportGameTypes'

const SYSTEM = `You write the pre-game brief on the NOPREDICTIONS Game Center, which puts Polymarket and Kalshi side by side for US sports. The site's line is "No predictions. Just edges" — its readers are bettors, and its credibility is that it never oversells.

You are given a JSON object of facts about one game: each exchange's moneyline price (decimal odds) and which pays more after fees, the sportsbook line ESPN carries, ESPN's own matchup model, each team's record, last five games, injuries, season numbers, leaders, standings, record against the spread and the season series where available.

Rules:
- Use ONLY what is in the facts. Never invent a statistic, injury, player, trend or piece of news. If something is not in the facts, do not mention it.
- No tips. Never tell the reader to bet, back or fade anything, and never predict the result. You may say which numbers are interesting and why.
- Odds are decimal (e.g. 1.85). Write probabilities as percentages only next to the number they come from.
- The exchanges, the sportsbook and ESPN's model usually agree within a few points. A gap between them is worth one sentence, described as a difference between sources, never as an edge.
- Last-five records and short streaks are small samples: say so when a point rests on one. Early-season records (two or three games) say very little.
- If "game_state" says the game has started, the facts carry no prices on purpose. Write about how the two sides arrived — records, form, injuries, standings — and never describe the game in progress or any price.
- Plain, direct English. No hype, no emojis, no exclamation marks.`

function dec(p: number | null | undefined): number | null {
  return p != null && p > 0.01 && p < 0.99 ? +(1 / p).toFixed(2) : null
}

function american(ml: number | null): number | null {
  if (ml == null || ml === 0) return null
  const p = ml < 0 ? -ml / (-ml + 100) : 100 / (ml + 100)
  return dec(p)
}

export function usBriefFacts(g: SportGamePage, started: boolean) {
  const side = (k: 'home' | 'away') => {
    const t = g[k]
    const pick = g.board?.best[k]
    return {
      team: t.name,
      role: k,
      record: t.record,
      home_or_road_record: t.splitRecord,
      against_the_spread: g.ats[k],
      last_five: g.recent[k].map((r) => `${r.result} ${r.atVs} ${r.opponent} ${r.score}`),
      injuries: g.injuries[k].map((x) => `${x.player}${x.position ? ` (${x.position})` : ''}: ${x.status}`),
      leaders: g.leaders[k].map((l) => `${l.category}: ${l.player} ${l.value}`),
      moneyline: started
        ? null
        : {
            best_price: dec(pick?.ask),
            pays_more_on: pick?.venue ?? 'level or one app only',
            by_app: Object.fromEntries(
              (g.board?.venues ?? []).map((b) => [b.venue, dec(b.quotes[k]?.ask)])
            ),
          },
    }
  }
  return {
    sport: SPORT_META[g.sport].label,
    game: `${g.away.name} at ${g.home.name}`,
    start_utc: g.start,
    venue: g.venue,
    game_state: started ? 'started — prices withheld' : 'not started',
    away: side('away'),
    home: side('home'),
    sportsbook: started || !g.sportsbook
      ? null
      : {
          provider: g.sportsbook.provider,
          spread: g.sportsbook.details,
          total: g.sportsbook.overUnder,
          moneyline_decimal: {
            [g.away.name]: american(g.sportsbook.awayMoneyline),
            [g.home.name]: american(g.sportsbook.homeMoneyline),
          },
        },
    espn_matchup_model_percent: started ? null : g.predictor
      ? { [g.away.name]: g.predictor.away, [g.home.name]: g.predictor.home }
      : null,
    season_series: g.series,
    season_numbers: g.state === 'pre' ? g.stats.map((s) => `${s.label}: ${g.away.abbr} ${s.away}, ${g.home.abbr} ${s.home}`) : [],
    standings: g.standings.map((grp) => ({
      group: grp.title,
      rows: grp.rows.map((r) => `${r.team} ${grp.cols.map((c, i) => `${c} ${r.cells[i]}`).join(' ')}`),
    })),
  }
}

export function usBrief(g: SportGamePage): Promise<Brief> {
  const started = g.state !== 'pre'
  return cachedBriefWith(
    ['us-brief-v1', g.sport, g.id, started ? 'live' : 'pre'],
    SYSTEM,
    usBriefFacts(g, started),
    started
  )
}
