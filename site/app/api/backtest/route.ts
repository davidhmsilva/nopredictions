import { NextResponse } from 'next/server'
import Anthropic from '@anthropic-ai/sdk'
import { zodOutputFormat } from '@anthropic-ai/sdk/helpers/zod'
import { getSql } from '../../lib/db'
import {
  LEAGUES,
  LEAGUE_CODES,
  NBA_CAVEATS,
  NBA_SEASON_MAX,
  NBA_SEASON_MIN,
  ParseResultSchema,
  isNbaMarket,
  toSqlSpec,
  computeStats,
  verdict,
  type RawBacktest,
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
- market + side: what is being backed on every qualifying match.
- leagues: array of football codes above; empty array [] = all leagues. ALWAYS [] for NBA markets.
- game_type: 'regular' | 'playoff' | 'any' — NBA only; use 'any' for football.
- fav_status: 'favorite' | 'underdog' | 'any' — whether the BACKED team is the favorite (by 1X2 close in football, by moneyline close in the NBA). Only meaningful when side is home or away; use 'any' otherwise.
- home_team / away_team: case-insensitive substring of the team name (e.g. "Real Madrid", "Lakers"); empty string = no filter.
- filters: list of numeric range conditions {field, op: 'gte'|'lte', value}.

  Both sports:
  - "odds": decimal odds on the backed side at the close (e.g. favorites below 1.50 → {"field":"odds","op":"lte","value":1.5}). For nba_spread / nba_total the price is a fixed 1.909, so this filter is pointless there.
  - "season": season start year. Football 2012..2025; NBA ${NBA_SEASON_MIN}..${NBA_SEASON_MAX} ("since 2018" → gte 2018).
  - "home_rest_days" / "away_rest_days": days since that team's previous recorded game. Football: short rest ≈ lte 4, long rest ≈ gte 10. NBA: a back-to-back is exactly 1, so "on a back-to-back" → lte 1.

  Football only:
  - "home_form_pts5" / "away_form_pts5": league points in that team's previous 5 matches, 0-15 (terrible ≈ lte 4; great ≈ gte 11).
  - "home_avg_tg5" / "away_avg_tg5": average TOTAL goals in that team's previous 5 matches (low ≈ lte 2.2; high ≈ gte 3.2).

  NBA only:
  - "spread": the HOME team's closing spread, negative when the home team is favored ("home favored by 5 or more" → lte -5; "close games" → gte -3 AND lte 3).
  - "total": the closing total points ("high-total games" → gte 230).
  - "home_form_w5" / "away_form_w5": wins in that team's previous 5 games, 0-5 ("after a bad stretch" ≈ lte 1; "hot" ≈ gte 4).
  - "home_avg_tp5" / "away_avg_tp5": average COMBINED points in that team's previous 5 games ("fast pace" ≈ gte 230).

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
  const s = { ...spec }
  const nba = isNbaMarket(s.market)

  if (nba) {
    s.leagues = null
  } else if (s.leagues) {
    const known = s.leagues.filter(c => LEAGUE_CODES.has(c))
    if (known.length !== s.leagues.length) {
      warnings.push('Some requested leagues are not in the dataset and were ignored.')
    }
    s.leagues = known.length > 0 ? known : null
  }

  if (s.fav_status && s.side !== 'home' && s.side !== 'away') {
    s.fav_status = null
    warnings.push('favorite/underdog filter only applies to home/away sides — ignored.')
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
  if (!hypothesis || hypothesis.length > 500) {
    return NextResponse.json(
      { ok: false, error: 'Hypothesis must be 1-500 characters.' },
      { status: 400 },
    )
  }

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
    return NextResponse.json(
      { ok: false, error: 'The agent failed to parse this hypothesis. Try rephrasing.' },
      { status: 502 },
    )
  }

  if (!parsed.supported || !parsed.spec) {
    return NextResponse.json({
      ok: true,
      supported: false,
      reason: parsed.reason || 'This theory needs data we do not have yet.',
      suggestion: parsed.suggestion || null,
    })
  }

  const { spec, warnings } = sanitizeSpec(toSqlSpec(parsed.spec))

  // 2 · Run the backtest in Postgres (separate function per sport)
  const nba = isNbaMarket(spec.market)
  let raw: RawBacktest
  try {
    const sql = getSql()
    const rows = nba
      ? await sql`SELECT run_backtest_nba(${sql.json(spec as never)}) AS r`
      : await sql`SELECT run_backtest(${sql.json(spec as never)}) AS r`
    raw = rows[0].r as RawBacktest
  } catch (err) {
    console.error('backtest error', err)
    return NextResponse.json({ ok: false, error: 'Backtest engine error.' }, { status: 502 })
  }

  // 3 · Stats + honest verdict
  const stats = computeStats(raw)
  const v = verdict(stats)

  const caveats = [
    ...parsed.caveats,
    ...warnings,
    ...(nba
      ? NBA_CAVEATS
      : [
          'Entry price = Pinnacle closing odds, flat 1u stakes. Beating the close is the hardest version of this test.',
        ]),
    'Rest days / form only count games in our dataset (league games — cups are not included).',
  ]

  return NextResponse.json({
    ok: true,
    supported: true,
    hypothesis,
    interpretation: parsed.interpretation,
    spec,
    verdict: v,
    stats,
    seasons: raw.seasons,
    monthly: raw.monthly,
    caveats,
  })
}
