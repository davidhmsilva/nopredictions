import { NextResponse } from 'next/server'
import Anthropic from '@anthropic-ai/sdk'
import { zodOutputFormat } from '@anthropic-ai/sdk/helpers/zod'
import { runSpec, type SpecResult } from '../../lib/labRun'
import { currentUser } from '../../lib/supabaseAuth'
import { claimUse, refundUse, refusalMessage } from '../../lib/plan'
import { readFailureMessage } from '../../lib/claudeJson'
import { noteIdea } from '../../lib/ideaLog'
import {
  LEAGUES,
  LEAGUE_CODES,
  NBA_SEASON_MAX,
  NBA_SEASON_MIN,
  ParseResultSchema,
  RUN_CAP,
  isNbaMarket,
  isRelative,
  namesItsTeam,
  toSqlSpec,
  type Spec,
} from '../../lib/backtest'

export const maxDuration = 60

const MODEL = 'claude-opus-4-8'

const LEAGUE_LINES = LEAGUES.map(
  l => `  ${l.code} — ${l.name} (${l.country}, tier ${l.tier})`,
).join('\n')

const SYSTEM_PROMPT = `You translate a bettor's plain-English sports theory into a strict backtest spec, or honestly refuse when the theory cannot be tested with the data available.

# Datasets — TWO sports are available

## A. FOOTBALL (soccer)
- ~101,000 finished club matches, seasons 2012-13 through 2025-26.
- Every match has the Pinnacle CLOSING price (the entry price) and most have the Pinnacle OPENING price (used for closing-line-value).
- Leagues available (use these exact codes in spec.leagues):
${LEAGUE_LINES}
- Markets: "1x2" (home / draw / away, 90-minute result) and "ou25" (over / under 2.5 goals; ~48k matches have this price).

## B. NBA (basketball)
- 10,006 finished games, seasons ${NBA_SEASON_MIN}-15 through ${NBA_SEASON_MAX}-22 ONLY. There is no NBA data after the ${NBA_SEASON_MAX}-22 season.
- Entry price is sportsbookreview's CONSENSUS closing line (not Pinnacle). No opening price, so NBA tests report no closing-line-value.
- Markets:
  - "nba_ml"     — moneyline. side: home | away. Real archived prices.
  - "nba_spread" — point spread, side covers the closing spread. side: home | away. Price assumed -110.
  - "nba_total"  — game total points. side: over | under. Price assumed -110.
- Playoffs are included and flagged; use game_type to filter.

Stakes are flat 1 unit per selection in both datasets.

# Spec fields
- market + side: what is being backed on every qualifying match. Besides home / draw / away / over / under, side can be:
  - "team": the team this rule is about, wherever it plays. Needs a named team (below) or fav_status ("back favourites" → side team, fav_status favorite; "back Liverpool" → side team, team "Liverpool"). Use this whenever the user does not say home or away.
  - "opponent": whoever plays the named team ("bet against Manchester United" → side opponent, team "Manchester United").
- leagues: array of football codes above; empty array [] = all leagues. ALWAYS [] for NBA markets.
- game_type: 'regular' | 'playoff' | 'any' — NBA only; use 'any' for football.
- fav_status: 'favorite' | 'underdog' | 'any' — whether the BACKED team is the favorite (by 1X2 close in football, by moneyline close in the NBA). Only meaningful when side is home, away or team; use 'any' otherwise.
- home_team / away_team: case-insensitive substring of the team name (e.g. "Real Madrid", "Lakers"), that team at that end; empty string = no filter.
- team: the same, at either end ("draws in Hannover games" → side draw, team "Hannover"; "over 2.5 when Liverpool play" → side over, team "Liverpool"); empty string = no team named.
- filters: list of numeric range conditions {field, op: 'gte'|'lte', value}.

  Both sports:
  - "odds": decimal odds on the backed side at the close (e.g. favorites below 1.50 → {"field":"odds","op":"lte","value":1.5}). For nba_spread / nba_total the price is a fixed 1.909, so this filter is pointless there.
  - "season": season start year. Football 2012..2025; NBA ${NBA_SEASON_MIN}..${NBA_SEASON_MAX} ("since 2018" → gte 2018).
  - "spread" and "total" below are NBA only.

  Every figure about a team's recent games is written with one of four prefixes, saying whose it is:
  - home_ the home team · away_ the away team
  - team_ the team this rule is about · opp_ its opponent. These two work only when side is team or opponent, or a team is named.
  So "Back the favourite after a loss" → side team, fav_status favorite, {"field":"team_last_gd","op":"lte","value":-1}. "Back home teams after a loss" → side home, {"field":"home_last_gd","op":"lte","value":-1}. "Back Liverpool when the opponent is winless in 5" → side team, team "Liverpool", {"field":"opp_winless_run","op":"gte","value":5}.

  The figures, shown here with home_; all league games only, all counted before kick-off:
  - "home_rest_days": days since that team's previous recorded game. Football: short rest ≈ lte 4, long rest ≈ gte 10. NBA: a back-to-back is exactly 1, so "on a back-to-back" → lte 1.

  Football only:
  - "home_form_pts5": league points in that team's previous 5 matches, 0-15 (terrible ≈ lte 4; great ≈ gte 11).
  - "home_venue_pts5": points in its previous 5 games at this end: with home_, the home team's last 5 HOME games; with away_, the away team's last 5 AWAY games; with team_ or opp_, whichever end it is at today. 0-15. "strong at home" ≈ home_venue_pts5 gte 11; "poor away from home" ≈ away_venue_pts5 lte 4.
  - "home_avg_tg5": average TOTAL goals in that team's previous 5 matches (low ≈ lte 2.2; high ≈ gte 3.2).
  - "home_avg_gf5": goals it SCORED per game in its previous 5 ("keeps scoring" ≈ gte 2; "cannot score" ≈ lte 0.8).
  - "home_avg_ga5": goals it CONCEDED per game in its previous 5 ("leaky" ≈ gte 2; "solid defence" ≈ lte 0.8).
  - "home_last_gd": its goal difference in its previous game. "after a loss" → lte -1; "after a win" → gte 1; "after a draw" → gte 0 AND lte 0; "did not lose" → gte 0; "after a heavy defeat" → lte -3; "after a big win" → gte 3.
  - "home_win_run": wins in a row before this game ("won 3 in a row" → gte 3).
  - "home_unbeaten_run": games in a row without defeat ("unbeaten in 5" → gte 5).
  - "home_loss_run": defeats in a row ("lost 3 in a row" → gte 3).
  - "home_winless_run": games in a row without a win ("winless in 5" → gte 5).
  Runs count at most ${RUN_CAP} games back: a longer one reads as ${RUN_CAP}, so "unbeaten in 15" is at best gte ${RUN_CAP}, and say so in "caveats".

  NBA only:
  - "spread": the HOME team's closing spread, negative when the home team is favored ("home favored by 5 or more" → lte -5; "close games" → gte -3 AND lte 3).
  - "total": the closing total points ("high-total games" → gte 230).
  - "home_form_w5": wins in that team's previous 5 games, 0-5 ("after a bad stretch" ≈ lte 1; "hot" ≈ gte 4).
  - "home_avg_tp5": average COMBINED points in that team's previous 5 games ("fast pace" ≈ gte 230).

Only add filters the user actually implied — do not invent extra conditions.

# What you CANNOT test (set supported=false, explain in "reason", and offer the closest testable variant in "suggestion")
- Other sports (NFL, MLB, NHL, tennis, college...).
- Football: Asian handicap, BTTS, correct score, corners, cards, half-time markets, cup ties, European competitions (Champions League etc.), derbies/rivalries, xG conditions, league-table position.
- NBA: player props, quarter/half markets, series prices, and ANYTHING about a season after ${NBA_SEASON_MAX}-22.
- Both: player-level anything (injuries, lineups, trades, coaches), referees, weather, in-play/live conditions, and Polymarket prices.
- If the theory is vague but clearly testable in spirit, translate it to the nearest concrete spec and note the approximation in "caveats" instead of refusing.
- If a theory is about a real NBA market but names a season we do not cover, DO NOT refuse outright — set supported=true, run it over ${NBA_SEASON_MIN}-15..${NBA_SEASON_MAX}-22, and say so plainly in "caveats".

# Output
- supported=true: fill spec, write "interpretation" — one plain-English sentence stating EXACTLY what will be tested (market, side, filters, leagues, seasons) — list any approximations you made in "caveats", and set reason="" and suggestion="".
- supported=false: spec=null, interpretation="", explain why in "reason", propose the closest testable theory in "suggestion".
- Be honest and literal. Never silently substitute a different theory for the user's.`

// ---------------------------------------------------------------------------

const anthropic = new Anthropic()

// naive per-instance rate limit: 10 requests / minute / IP
const hits = new Map<string, number[]>()
function rateLimited(ip: string): boolean {
  const now = Date.now()
  const arr = (hits.get(ip) ?? []).filter(t => now - t < 60_000)
  arr.push(now)
  hits.set(ip, arr)
  return arr.length > 10
}

function sanitizeSpec(spec: Spec): { spec: Spec; warnings: string[] } {
  const warnings: string[] = []
  let s = { ...spec }
  const nba = isNbaMarket(s.market)

  // A team named on a rule that already says which end it backs is that end's team.
  if (s.team && (s.side === 'home' || s.side === 'away')) {
    s = { ...s, [`${s.side}_team`]: s.team, team: null }
  }
  // Conditions on "the team" where the side already says who that is.
  if (!isRelative(s) && (s.side === 'home' || s.side === 'away')) {
    const other = s.side === 'home' ? 'away' : 'home'
    for (const [k, v] of Object.entries(s)) {
      if (k.startsWith('team_')) (s as unknown as Record<string, unknown>)[`${s.side}_${k.slice(5)}`] = v
      if (k.startsWith('opp_')) (s as unknown as Record<string, unknown>)[`${other}_${k.slice(4)}`] = v
    }
  }
  if (!isRelative(s)) {
    let dropped = false
    for (const k of Object.keys(s)) {
      if (k.startsWith('team_') || k.startsWith('opp_')) {
        dropped = dropped || s.side !== 'home' && s.side !== 'away'
        delete (s as unknown as Record<string, unknown>)[k]
      }
    }
    if (dropped) warnings.push('A condition on "the team" needs a rule about one team — ignored.')
  }

  // Each sport's function ignores the other's figures, which would test a broader
  // rule than the one described. Dropped here, so the page shows what really ran.
  const otherSport = nba
    ? /_(form_pts5|avg_tg5|avg_gf5|avg_ga5|venue_pts5|last_gd|win_run|unbeaten_run|loss_run|winless_run)_(min|max)$/
    : /_(form_w5|avg_tp5)_(min|max)$|^(spread|total)_(min|max)$/
  for (const k of Object.keys(s)) {
    if (otherSport.test(k)) {
      delete (s as unknown as Record<string, unknown>)[k]
      warnings.push(`A condition that only exists for ${nba ? 'football' : 'the NBA'} was ignored.`)
    }
  }

  // A run is only counted so far back. A bound past that could never be met.
  for (const k of Object.keys(s)) {
    const o = s as unknown as Record<string, unknown>
    if (/_run_min$/.test(k) && typeof o[k] === 'number' && (o[k] as number) > RUN_CAP) {
      o[k] = RUN_CAP
      warnings.push(`Runs are counted up to ${RUN_CAP} games, so this tests a run of ${RUN_CAP} or more.`)
    }
  }

  if (nba) {
    s.leagues = null
  } else if (s.leagues) {
    const known = s.leagues.filter(c => LEAGUE_CODES.has(c))
    if (known.length !== s.leagues.length) {
      warnings.push('Some requested leagues are not in the dataset and were ignored.')
    }
    s.leagues = known.length > 0 ? known : null
  }

  if (s.fav_status && s.side !== 'home' && s.side !== 'away' && s.side !== 'team') {
    s.fav_status = null
    warnings.push('favorite/underdog filter only applies to a side that backs a team — ignored.')
  }

  const [lo, hi] = nba ? [NBA_SEASON_MIN, NBA_SEASON_MAX] : [2012, 2025]
  if (s.season_start != null) {
    if (s.season_start > hi) {
      warnings.push(
        `Requested seasons start after our NBA coverage ends (${NBA_SEASON_MAX}-22); the test ran over the full available range instead.`,
      )
      s.season_start = null
    } else {
      s.season_start = Math.max(lo, s.season_start)
    }
  }
  if (s.season_end != null) s.season_end = Math.max(lo, Math.min(hi, s.season_end))

  return { spec: s, warnings }
}

function ipOf(request: Request): string {
  return request.headers.get('x-forwarded-for')?.split(',')[0]?.trim() || 'unknown'
}

export async function POST(request: Request) {
  const ip = ipOf(request)
  if (rateLimited(ip)) {
    return NextResponse.json({ ok: false, error: 'Rate limit exceeded — slow down.' }, { status: 429 })
  }

  let hypothesis: string
  try {
    const body = await request.json()
    hypothesis = String(body?.hypothesis ?? '').trim()
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid request body.' }, { status: 400 })
  }
  // 1200, not 500: the Lab's questions step appends the user's answers.
  if (!hypothesis || hypothesis.length > 1200) {
    return NextResponse.json(
      { ok: false, error: 'Hypothesis must be 1-1200 characters.' },
      { status: 400 },
    )
  }

  // The gate sits here on purpose: after the checks that cost nothing, before
  // the model call that costs money. A refused request must not spend one.
  const user = await currentUser()
  const use = await claimUse(user?.id ?? null, 'lab')
  if (!use.allowed) {
    return NextResponse.json(
      { ok: false, error: refusalMessage(use), entitlement: use },
      { status: use.reason === 'signed_out' ? 401 : 402 },
    )
  }
  const refund = () => refundUse(user?.id ?? null, 'lab')

  // 1 · Parse the plain-English theory into a spec (or an honest refusal)
  let parsed
  try {
    const msg = await anthropic.messages.parse({
      model: MODEL,
      max_tokens: 2000,
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: `Theory to test: "${hypothesis}"` }],
      output_config: { format: zodOutputFormat(ParseResultSchema) },
    })
    parsed = msg.parsed_output
    if (!parsed) throw new Error('empty parse result')
  } catch (err) {
    console.error('parse error', err)
    await refund()
    return NextResponse.json(
      { ok: false, error: readFailureMessage(err) },
      { status: 502 },
    )
  }

  if (!parsed.supported || !parsed.spec) {
    const reason = parsed.reason || 'This theory needs data we do not have yet.'
    await noteIdea({ idea: hypothesis, kind: 'prematch', supported: false, reason, suggestion: parsed.suggestion })
    return NextResponse.json({
      ok: true,
      supported: false,
      reason,
      suggestion: parsed.suggestion || null,
      entitlement: use,
    })
  }

  const { spec, warnings } = sanitizeSpec(toSqlSpec(parsed.spec))
  if (!namesItsTeam(spec)) {
    const reason = 'This rule does not say which team it backs, in every game.'
    const suggestion = 'Name a team, or say the favourite or the underdog.'
    await noteIdea({ idea: hypothesis, kind: 'prematch', supported: false, reason, suggestion })
    return NextResponse.json({ ok: true, supported: false, reason, suggestion, entitlement: use })
  }

  // 2 · Run the backtest in Postgres (separate function per sport) — the same
  //     path as the Lab's free tests, so a typed theory and the same theory
  //     picked from the list cannot disagree.
  let r: SpecResult
  try {
    r = await runSpec(spec)
  } catch (err) {
    console.error('backtest error', err)
    await refund()
    return NextResponse.json({ ok: false, error: 'Backtest engine error.' }, { status: 502 })
  }

  // After the test, on purpose: the connection is single-file, and this must not
  // stand in front of the answer.
  await noteIdea({ idea: hypothesis, kind: 'prematch', supported: true, spec })

  // 3 · Stats + honest verdict
  const caveats = [...parsed.caveats, ...warnings, ...r.datasetCaveats]

  return NextResponse.json({
    ok: true,
    supported: true,
    hypothesis,
    interpretation: parsed.interpretation,
    spec,
    verdict: r.verdict,
    stats: r.stats,
    seasons: r.seasons,
    monthly: r.monthly,
    recent: r.recent,
    caveats,
    entitlement: use,
  })
}
