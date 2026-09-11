// The written brief — Claude reading the numbers this page already shows and
// saying which of them matter. It is given ONLY those numbers and told to use
// nothing else; the page shows it next to the numbers, so a sentence that is
// not backed by one on screen is visible as such.
//
// One brief per fixture, not per visitor: cached in the shared data cache on
// the slug and on whether the line-ups are out, so cost scales with the number
// of fixtures anyone opens and not with traffic. A failure is thrown, never
// returned, so it is not cached.

import Anthropic from '@anthropic-ai/sdk'
import { betaZodOutputFormat } from '@anthropic-ai/sdk/helpers/beta/zod'
import { unstable_cache } from 'next/cache'
import { z } from 'zod'
import type { Headline } from './gamecenter'
import type { Fixture, MatchContext } from './matchcontext'
import type { PricedLike } from './pricedLike'
import type { FormStats, TeamForm } from './teamform'

const MODEL = 'claude-opus-5'

const BriefSchema = z.object({
  headline: z.string().describe('One sentence, under 110 characters: the single most useful thing about this fixture.'),
  points: z
    .array(
      z.object({
        title: z.string().describe('2-5 words'),
        body: z.string().describe('1-2 sentences, every number taken from the facts'),
      })
    )
    .describe('3 to 5 points, most useful first'),
  caveat: z.string().describe('One sentence on the biggest limitation of what the facts can say.'),
})

export type Brief = z.infer<typeof BriefSchema> & {
  writtenAt: string
  model: string
  lineups: boolean
  /** Written after kick-off: about how the sides arrived, with no prices. */
  inPlay: boolean
}

const SYSTEM = `You write the pre-match brief on the NOPREDICTIONS Game Center, a Polymarket football companion. The site's line is "No predictions. Just edges" — its readers are bettors, and its credibility is that it never oversells.

You are given a JSON object of facts about one fixture: Polymarket prices, historical rates for matches priced like this one (from ~48k matches with Pinnacle closing prices), each team's recent record from our database (counts like 7 of 10), current streaks with how likely they are by chance at the league rate, each team's record against the closing price, head-to-head results, the league table and line-ups when available.

Rules:
- Use ONLY numbers present in the facts. Never invent a statistic, injury, player or piece of news. If something is not in the facts, do not mention it.
- Quote counts; do not re-count. Where a summary is given (e.g. head_to_head_summary), use it rather than tallying the list yourself.
- No tips. Never tell the reader to bet, back, lay or fade anything, and never predict the result. You may say which numbers are interesting and why.
- Odds are decimal (e.g. 1.85). Write probabilities as percentages only next to the decimal they come from.
- Sample sizes matter: 4 of 5 is weak evidence, 7 of 10 is little better. Say so when a point rests on a small count.
- The market already knows recent form. We tested it on 14,365 held-out matches: form features added nothing to the closing price. So a streak is a description, not a reason the price is wrong — say that when you mention one.
- A gap between Polymarket's price and the "priced like this" history is NOT an edge: the history is an average match at that line, the price knows who is playing. Describe such a gap as the board pricing this fixture differently from an average one, and say what in the team facts might explain it.
- A team "beating the closing price" over its last games (e.g. 7 wins where the closing odds implied 4.2) is mostly variance on samples this small. Mention it only with that framing.
- If "match_state" says the match is already in play, the facts carry no prices on purpose: live prices reflect the score. Write about how the two sides arrived — form, runs, head to head, the table — and never describe the match in progress, the score, or any price.
- Plain, direct English. No hype, no emojis, no exclamation marks.`

function pct(c: { k: number; n: number }): string {
  return c.n ? `${c.k}/${c.n}` : 'n/a'
}

function statsFacts(s: FormStats) {
  return {
    games: s.games,
    record: `${s.w}W ${s.d}D ${s.l}L`,
    goals_for_avg: +s.gf.toFixed(2),
    goals_against_avg: +s.ga.toFixed(2),
    over_1_5: pct(s.o15),
    over_2_5: pct(s.o25),
    over_3_5: pct(s.o35),
    btts: pct(s.btts),
    clean_sheets: pct(s.cleanSheet),
    failed_to_score: pct(s.failedToScore),
    goal_before_half_time: pct(s.htGoal),
    first_half_over_1_5: pct(s.htO15),
    scored_in_first_half: pct(s.scored1h),
    conceded_in_first_half: pct(s.conceded1h),
    led_at_half_time: pct(s.ledAtHt),
    avg_first_half_goals: s.avgHtGoals != null ? +s.avgHtGoals.toFixed(2) : null,
  }
}

function teamFacts(t: TeamForm | null, venue: 'home' | 'away', swapped: boolean) {
  if (!t) return null
  return {
    name: t.name,
    league: t.league,
    last_10: statsFacts(t.splits.last10),
    [`last_10_${venue}`]: statsFacts(venue === 'home' ? t.splits.home10 : t.splits.away10),
    last_5_results: t.games.slice(0, 5).map((g) =>
      `${g.venue === 'H' ? 'v' : '@'} ${g.opponent} ${g.gf}-${g.ga}${g.hf != null ? ` (HT ${g.hf}-${g.ha})` : ''}`
    ),
    // A venue-scoped run was computed on the assumed venue; when ESPN says the
    // sides are the other way round it describes the wrong set of games.
    streaks: t.streaks.filter((s) => !(swapped && s.scope === 'venue')).map((s) => ({
      what: s.label,
      scope: s.scope === 'venue' ? `${venue} games` : 'all games',
      length: s.kind === 'run' ? `${s.k} in a row` : `${s.k} of last ${s.of}`,
      league_rate: `${Math.round(s.base * 100)}%`,
      chance_at_league_rate: `1 in ${Math.max(2, Math.round(1 / s.chance))}`,
    })),
    against_closing_price: {
      wins: t.market.wins
        ? `${t.market.wins.actual} wins in ${t.market.wins.games}; closing odds implied ${t.market.wins.expected.toFixed(1)}`
        : null,
      overs_2_5: t.market.overs
        ? `${t.market.overs.actual} overs in ${t.market.overs.games}; closing odds implied ${t.market.overs.expected.toFixed(1)}`
        : null,
    },
  }
}

function h2hSummary(ctx: MatchContext) {
  const g = (ctx.teams?.h2h ?? []).slice(0, 6)
  const h = ctx.teams?.home?.name
  const a = ctx.teams?.away?.name
  if (!g.length || !h || !a) return null
  const won = (team: string) =>
    g.filter((x) => (x.home === team && x.hs > x.as) || (x.away === team && x.as > x.hs)).length
  return {
    meetings: g.length,
    [`${h}_wins`]: won(h),
    draws: g.filter((x) => x.hs === x.as).length,
    [`${a}_wins`]: won(a),
    over_2_5: g.filter((x) => x.hs + x.as > 2).length,
    both_scored: g.filter((x) => x.hs > 0 && x.as > 0).length,
  }
}

export function briefFacts(
  fx: Fixture,
  ctx: MatchContext,
  headlines: Headline[],
  pricedLike: PricedLike,
  started: boolean
) {
  const espn = ctx.espn
  const tbl = espn?.table?.rows.filter((r) => r.mark) ?? []
  const pl = (b: PricedLike['totals'] | PricedLike['result']) =>
    b && {
      bucket: `${Math.round(b.lo * 100)}-${Math.round(b.hi * 100)}%`,
      matches: b.n,
      lines: b.lines.map((l) => ({
        market: l.label,
        historical_rate: l.rate != null ? `${Math.round(l.rate * 100)}%` : null,
        polymarket_price: l.pmAsk != null ? +(1 / l.pmAsk).toFixed(2) : null,
      })),
    }
  return {
    fixture: `${fx.home} v ${fx.away}`,
    competition: fx.competition,
    kickoff_utc: fx.kickoff,
    venue_note: espn?.swapped ? `${fx.away} are the home side` : null,
    // Once the ball is rolling the board prices the SCORE. A brief quoting
    // 6.06 for a side trailing 0-1 at 45' as if it were the opening line
    // wrote "unusually wide line" about a goal — so after kick-off the
    // prices are withheld entirely, never passed with a caveat.
    match_state: started ? 'in play — prices withheld' : 'not started',
    polymarket_prices: started
      ? null
      : headlines.map((h) => ({ market: h.label, decimal: h.odds ? +h.odds.toFixed(2) : null })),
    priced_like_this: started
      ? null
      : {
          over_2_5_bucket: pl(pricedLike.totals),
          home_win_bucket: pl(pricedLike.result),
        },
    [fx.home]: teamFacts(ctx.teams?.home ?? null, espn?.swapped ? 'away' : 'home', !!espn?.swapped),
    [fx.away]: teamFacts(ctx.teams?.away ?? null, espn?.swapped ? 'home' : 'away', !!espn?.swapped),
    streak_tests_run: ctx.teams?.checked ?? null,
    // Counted here, not left to the model: asked to read six scorelines it
    // once reported "three draws" where there were four.
    head_to_head_summary: h2hSummary(ctx),
    head_to_head: (ctx.teams?.h2h ?? []).slice(0, 6).map((g) =>
      `${g.date.slice(0, 10)} ${g.home} ${g.hs}-${g.as} ${g.away}${g.hht != null ? ` (HT ${g.hht}-${g.aht})` : ''}`
    ),
    table: tbl.map((r) => `${r.rank}. ${r.team} — ${r.pts} pts from ${r.gp}, GD ${r.gd}`),
    lineups: espn?.lineupsConfirmed
      ? {
          [espn.home.name]: `${espn.home.formation ?? '?'}: ${espn.home.starters.map((p) => p.short).join(', ')}`,
          [espn.away.name]: `${espn.away.formation ?? '?'}: ${espn.away.starters.map((p) => p.short).join(', ')}`,
        }
      : 'not announced yet',
  }
}

async function write(facts: unknown, lineups: boolean, inPlay: boolean): Promise<Brief> {
  if (!process.env.ANTHROPIC_API_KEY) throw new Error('ANTHROPIC_API_KEY not set')
  const client = new Anthropic()
  const msg = await client.beta.messages.parse({
    model: MODEL,
    max_tokens: 8000,
    // Opus 5 can decline; the fallback re-runs the same request on Opus 4.8
    // inside the same call rather than leaving the panel empty.
    betas: ['server-side-fallback-2026-06-01'],
    fallbacks: [{ model: 'claude-opus-4-8' }],
    output_config: { effort: 'low', format: betaZodOutputFormat(BriefSchema) },
    system: SYSTEM,
    messages: [{ role: 'user', content: JSON.stringify(facts) }],
  })
  if (msg.stop_reason === 'refusal') throw new Error('brief declined')
  if (!msg.parsed_output) throw new Error('empty brief')
  const out = msg.parsed_output
  // The schema asks for 3-5; the cap is enforced here rather than trusted.
  return { ...out, points: out.points.slice(0, 5), writtenAt: new Date().toISOString(), model: msg.model, lineups, inPlay }
}

/** Keyed on the PHASE, so a brief written with prices before kick-off is never
 *  served as, or overwritten by, the price-free one written after it. */
export function cachedBrief(slug: string, lineups: boolean, inPlay: boolean, facts: unknown): Promise<Brief> {
  const phase = inPlay ? 'live' : lineups ? 'pre-xi' : 'pre'
  return unstable_cache(() => write(facts, lineups, inPlay), ['gc-brief-v3', slug, phase], {
    revalidate: 12 * 3600,
  })()
    // Capped on the way out as well as on the way in: a brief cached before a
    // rule changed must not outlive the rule.
    .then((b) => ({ ...b, points: b.points.slice(0, 5) }))
}
