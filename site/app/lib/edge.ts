const SHARP_BOOKS: Record<string, number> = { pinnacle: 0.65, betfair_ex_eu: 0.35 }

const TARGET_SPORTS = [
  'soccer_epl',
  'soccer_spain_la_liga',
  'soccer_germany_bundesliga',
  'soccer_italy_serie_a',
  'soccer_france_ligue_one',
  'soccer_uefa_champs_league',
  'soccer_uefa_europa_league',
  'soccer_uefa_europa_conference_league',
  'soccer_england_championship',
  'soccer_spain_segunda_division',
  'soccer_germany_bundesliga2',
  'soccer_italy_serie_b',
  'soccer_france_ligue_deux',
  'soccer_netherlands_eredivisie',
  'soccer_portugal_primeira_liga',
]

const SPORT_LABELS: Record<string, string> = {
  soccer_epl: 'Premier League',
  soccer_spain_la_liga: 'La Liga',
  soccer_germany_bundesliga: 'Bundesliga',
  soccer_italy_serie_a: 'Serie A',
  soccer_france_ligue_one: 'Ligue 1',
  soccer_uefa_champs_league: 'Champions League',
  soccer_uefa_europa_league: 'Europa League',
  soccer_uefa_europa_conference_league: 'Conference League',
  soccer_england_championship: 'Championship',
  soccer_spain_segunda_division: 'Segunda División',
  soccer_germany_bundesliga2: 'Bundesliga 2',
  soccer_italy_serie_b: 'Serie B',
  soccer_france_ligue_deux: 'Ligue 2',
  soccer_netherlands_eredivisie: 'Eredivisie',
  soccer_portugal_primeira_liga: 'Primeira Liga',
}

export { TARGET_SPORTS, SPORT_LABELS }

// ── Types ───────────────────────────────────────────────────────────────────

export interface TotalsLine {
  line: number
  over_prob: number
  under_prob: number
}

export interface SharpEvent {
  home: string
  away: string
  commence_time: string
  sport: string
  home_prob: number
  draw_prob: number | null
  away_prob: number
  totals: TotalsLine[]
  sources: Record<string, Record<string, number | null>>
}

export interface AnalyzedMarket {
  market_title: string
  event_title: string
  home: string
  away: string
  sport: string
  sport_label: string
  commence_time: string
  outcome_key: string
  outcome_label: string
  pm_price: number
  sharp_prob: number
  edge_pp: number
  ev_pct: number
  is_edge: boolean
  reasoning: string
}

export interface ScanResult {
  scanned_at: string
  markets_analyzed: number
  events_analyzed: number
  edges_found: number
  analyzed: AnalyzedMarket[]
  odds_api_remaining: string | null
}

export interface LiveAnalysis {
  home: string
  away: string
  score: string | null
  minute: number | null
  is_live: boolean
  commence_time: string
  sport: string
  sport_label: string
  sharp_odds: {
    home_prob: number
    draw_prob: number | null
    away_prob: number
    totals: TotalsLine[]
  } | null
  poisson: Record<string, number> | null
  dc_model: {
    home_win: number
    draw: number
    away_win: number
    over_2_5: number
    under_2_5: number
    over_1_5: number
    under_1_5: number
    btts: number
    lambda_home: number
    lambda_away: number
  } | null
  pm_markets: {
    title: string
    pm_price: number
    fair_prob: number | null
    edge_pp: number | null
    is_edge: boolean
    reasoning: string
    dc_prob: number | null
    dc_edge_pp: number | null
  }[]
}

// ── Helpers ──────────────────────────────────────────────────────────────────

export function norm(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, '')
    .replace(/\b(fc|cf|sc|ac|ss|afc|bsc|1\.|vfb|vfl|rb|sv|fk|sk|bv|borussia)\b/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function matchKeys(home: string, away: string): string[] {
  const hn = norm(home)
  const an = norm(away)
  return [
    `${hn}_${an}`,
    `${hn.slice(0, 6)}_${an.slice(0, 6)}`,
    `${hn.slice(0, 8)}_${an.slice(0, 8)}`,
  ]
}

// ── Vig removal ─────────────────────────────────────────────────────────────

export function vigRemove(event: {
  home_team: string
  away_team: string
  bookmakers: Array<{
    key: string
    markets: Array<{
      key: string
      outcomes: Array<{ name: string; price: number; point?: number }>
    }>
  }>
}): SharpEvent | null {
  const { home_team, away_team, bookmakers } = event
  const bookProbs: Record<string, Record<string, number | null>> = {}
  const bookTotals: Record<string, Array<{ line: number; over_prob: number; under_prob: number }>> =
    {}

  for (const bk of bookmakers) {
    if (!(bk.key in SHARP_BOOKS)) continue

    for (const mkt of bk.markets) {
      if (mkt.key === 'h2h') {
        const raw: Record<string, number> = {}
        for (const o of mkt.outcomes) raw[o.name] = o.price
        const ho = raw[home_team]
        const ao = raw[away_team]
        const dro = raw['Draw']
        if (!ho || !ao) continue
        const hImp = 1 / ho
        const dImp = dro ? 1 / dro : 0
        const aImp = 1 / ao
        const total = hImp + dImp + aImp
        bookProbs[bk.key] = {
          home: hImp / total,
          draw: dro ? dImp / total : null,
          away: aImp / total,
        }
      } else if (mkt.key === 'totals') {
        const lines: Record<number, { over?: number; under?: number }> = {}
        for (const o of mkt.outcomes) {
          const point = o.point ?? 0
          if (!lines[point]) lines[point] = {}
          if (o.name.toLowerCase().includes('over')) lines[point].over = o.price
          else if (o.name.toLowerCase().includes('under')) lines[point].under = o.price
        }
        const totals: Array<{ line: number; over_prob: number; under_prob: number }> = []
        for (const [line, ld] of Object.entries(lines)) {
          if (ld.over && ld.under) {
            const oImp = 1 / ld.over
            const uImp = 1 / ld.under
            const t = oImp + uImp
            totals.push({ line: Number(line), over_prob: oImp / t, under_prob: uImp / t })
          }
        }
        if (totals.length) bookTotals[bk.key] = totals
      }
    }
  }

  if (!Object.keys(bookProbs).length) return null

  function wavg(field: string): number | null {
    const vals: [number, number][] = []
    for (const [k, bp] of Object.entries(bookProbs)) {
      const v = bp[field]
      if (v != null) vals.push([v, SHARP_BOOKS[k]])
    }
    if (!vals.length) return null
    return vals.reduce((s, [v, w]) => s + v * w, 0) / vals.reduce((s, [, w]) => s + w, 0)
  }

  const hp = wavg('home')
  const dp = wavg('draw')
  const ap = wavg('away')
  if (!hp || !ap) return null

  const total = (hp || 0) + (dp || 0) + (ap || 0)

  const result: SharpEvent = {
    home: home_team,
    away: away_team,
    commence_time: event.commence_time as unknown as string,
    sport: '',
    home_prob: hp / total,
    draw_prob: dp ? dp / total : null,
    away_prob: ap / total,
    totals: [],
    sources: bookProbs,
  }

  if (Object.keys(bookTotals).length) {
    const merged: Record<number, { over: [number, number][]; under: [number, number][] }> = {}
    for (const [bk, lines] of Object.entries(bookTotals)) {
      const w = SHARP_BOOKS[bk]
      for (const ld of lines) {
        if (!merged[ld.line]) merged[ld.line] = { over: [], under: [] }
        merged[ld.line].over.push([ld.over_prob, w])
        merged[ld.line].under.push([ld.under_prob, w])
      }
    }
    result.totals = Object.entries(merged)
      .map(([line, td]) => ({
        line: Number(line),
        over_prob:
          td.over.reduce((s, [v, w]) => s + v * w, 0) / td.over.reduce((s, [, w]) => s + w, 0),
        under_prob:
          td.under.reduce((s, [v, w]) => s + v * w, 0) /
          td.under.reduce((s, [, w]) => s + w, 0),
      }))
      .sort((a, b) => a.line - b.line)
  }

  return result
}

// ── Sharp probability lookup ────────────────────────────────────────────────

export function sharpProbForOutcome(outcomeKey: string, ev: SharpEvent): number | null {
  if (outcomeKey === 'home') return ev.home_prob
  if (outcomeKey === 'draw') return ev.draw_prob
  if (outcomeKey === 'away') return ev.away_prob

  const m = outcomeKey.match(/^(over|under)_([\d.]+)$/)
  if (m && ev.totals.length) {
    const dir = m[1] as 'over' | 'under'
    const line = parseFloat(m[2])
    const exact = ev.totals.find((t) => t.line === line)
    if (exact) return exact[`${dir}_prob`]
    const closest = ev.totals.reduce(
      (best, t) => (Math.abs(t.line - line) < Math.abs(best.line - line) ? t : best),
      ev.totals[0]
    )
    if (Math.abs(closest.line - line) <= 0.5) return closest[`${dir}_prob`]
  }
  return null
}

// ── Match sharp event to PM market ──────────────────────────────────────────

export function fuzzyFindEvent(
  home: string | null,
  away: string | null,
  lookup: Record<string, SharpEvent>
): SharpEvent | null {
  if (home && away) {
    for (const key of matchKeys(home, away)) {
      if (lookup[key]) return lookup[key]
    }
    const hn = norm(home)
    const an = norm(away)
    const h6 = hn.slice(0, 6)
    const a6 = an.slice(0, 6)
    for (const ev of Object.values(lookup)) {
      const eh = norm(ev.home)
      const ea = norm(ev.away)
      const hMatch = hn.includes(eh) || eh.includes(hn) || (h6 && eh.includes(h6))
      const aMatch = an.includes(ea) || ea.includes(an) || (a6 && ea.includes(a6))
      if (hMatch && aMatch) return ev
    }
  }
  if (home) {
    const tn = norm(home)
    const t6 = tn.slice(0, 6)
    for (const ev of Object.values(lookup)) {
      const eh = norm(ev.home)
      const ea = norm(ev.away)
      if (tn.includes(eh) || eh.includes(tn) || tn.includes(ea) || ea.includes(tn)) return ev
      if (t6 && (eh.includes(t6) || ea.includes(t6))) return ev
    }
  }
  return null
}

// ── PM market classification ────────────────────────────────────────────────

const REJECT_PATTERNS = [
  /spread|handicap/i,
  /exact score/i,
  /\d+\s*-\s*\d+/,
  /corners|cards|goalscorer|anytime|first goal|clean sheet/i,
  /halftime|half.?time/i,
]

export function classifyOutcome(
  title: string,
  home: string,
  away: string
): { key: string; label: string } | null {
  const t = title.toLowerCase()
  for (const pat of REJECT_PATTERNS) {
    if (pat.test(t)) return null
  }

  if (t.includes('draw') && !t.includes('no draw') && !t.includes('spread')) {
    return { key: 'draw', label: 'Draw' }
  }

  let m = t.match(/(over|under)\s*([\d.]+)/)
  if (m) return { key: `${m[1]}_${m[2]}`, label: `${m[1][0].toUpperCase() + m[1].slice(1)} ${m[2]} goals` }

  m = t.match(/o\/u\s*([\d.]+)/)
  if (m) return { key: `over_${m[1]}`, label: `Over ${m[1]} goals` }

  if (t.includes('both teams to score') || t.includes('btts')) {
    const isNo = t.split('btts').pop()?.includes('no') || t.includes('not to score')
    return { key: isNo ? 'btts_no' : 'btts_yes', label: isNo ? 'BTTS No' : 'BTTS Yes' }
  }

  const hn6 = norm(home).slice(0, 6)
  const an6 = norm(away).slice(0, 6)
  const tn = norm(t)

  if (hn6 && tn.includes(hn6) && t.includes('win')) return { key: 'home', label: `${home} win` }
  if (an6 && tn.includes(an6) && t.includes('win')) return { key: 'away', label: `${away} win` }
  if (hn6 && tn.includes(hn6)) return { key: 'home', label: `${home} win` }

  return null
}

export function is1x2Market(q: string): boolean {
  // Strip date suffixes (e.g. "on 2026-05-17") before scoring-pattern checks
  const t = q.replace(/\s+on\s+\d{4}-\d{2}-\d{2}/i, '').toLowerCase()
  const rejects = [
    /\bby\s+\d/, /\b\d{1,2}\s*-\s*\d{1,2}\b/, /exact/, /halftime/, /half.?time/,
    /over\s*[\d.]/, /under\s*[\d.]/, /spread/, /handicap/, /btts/, /both teams/,
    /clean sheet/, /first goal/, /anytime/, /goalscorer/, /corners/, /cards/, /total goals/,
  ]
  if (rejects.some((r) => r.test(t))) return false
  return [/^will .{2,50} win/, /\bdraw\b/, /^will .{2,50} (beat|defeat)/].some((r) => r.test(t))
}

export function isTotalsMarket(q: string): boolean {
  const t = q.toLowerCase()
  const rejects = [/corners/, /cards/, /halftime/, /half.?time/, /spread/, /exact/, /goalscorer/, /anytime/, /first goal/]
  if (rejects.some((r) => r.test(t))) return false
  return /o\/u\s*[\d.]|over\s*[\d.]|under\s*[\d.]|both teams to score|btts/.test(t)
}

export function extractTeams(title: string): { home: string; away: string } | null {
  const clean = title.replace(/\s+-\s+(?:More Markets|Halftime.*|Exact Score|.*Markets.*)$/i, '').trim()
  let m = clean.match(/(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:[:\-?]|$)/)
  if (m) return { home: m[1].trim(), away: m[2].trim() }
  m = clean.match(/will\s+(.+?)\s+(?:beat|win\s+vs?\.?)\s+(.+?)[?.]/i)
  if (m) return { home: m[1].trim(), away: m[2].trim() }
  return null
}

export function extractTeamFromWinQ(title: string): string | null {
  const m = title.match(/will\s+(.+?)\s+(?:FC\s+|CF\s+)?win\b/i)
  if (m) {
    let team = m[1].replace(/\s+(?:FC|CF|SC|AC)\s*$/i, '').trim()
    team = team.replace(/\s+on\s+\d{4}-\d{2}-\d{2}.*/, '').trim()
    return team.length > 2 ? team : null
  }
  return null
}

// ── Poisson model ───────────────────────────────────────────────────────────

function poissonPmf(k: number, lambda: number): number {
  if (lambda <= 0) return k === 0 ? 1 : 0
  let result = Math.exp(-lambda)
  for (let i = 1; i <= k; i++) result *= lambda / i
  return result
}

export function oddsToLambda(hOdds: number, dOdds: number, aOdds: number): [number, number] {
  const hImp = 1 / hOdds
  const dImp = dOdds ? 1 / dOdds : 0
  const aImp = 1 / aOdds
  const total = hImp + dImp + aImp
  const ph = hImp / total
  const pa = aImp / total
  const totalGoals = 2.5
  if (pa > 0 && ph > 0) {
    const ratio = ph / pa
    const sqrtR = Math.sqrt(ratio)
    const la = totalGoals / (sqrtR + 1)
    const lh = totalGoals - la
    return [lh, la]
  }
  return [1.3, 1.1]
}

export function poissonInplay(
  lambdaHome: number,
  lambdaAway: number,
  homeGoals: number,
  awayGoals: number,
  minute: number,
  totalMinutes = 90
): Record<string, number> {
  const fracRemaining = Math.max(0, (totalMinutes - minute) / totalMinutes)
  const lhRem = lambdaHome * fracRemaining
  const laRem = lambdaAway * fracRemaining
  const maxG = 9

  let homeWin = 0, draw = 0, awayWin = 0
  let over15 = 0, over25 = 0, bttsYes = 0

  for (let i = 0; i < maxG; i++) {
    for (let j = 0; j < maxG; j++) {
      const p = poissonPmf(i, lhRem) * poissonPmf(j, laRem)
      const fh = homeGoals + i
      const fa = awayGoals + j
      if (fh > fa) homeWin += p
      else if (fh === fa) draw += p
      else awayWin += p
      if (fh + fa > 1.5) over15 += p
      if (fh + fa > 2.5) over25 += p
      if (fh > 0 && fa > 0) bttsYes += p
    }
  }

  const tot = homeWin + draw + awayWin
  if (tot > 0) { homeWin /= tot; draw /= tot; awayWin /= tot }

  return {
    home_win: homeWin,
    draw,
    away_win: awayWin,
    over_1_5: over15,
    under_1_5: 1 - over15,
    over_2_5: over25,
    under_2_5: 1 - over25,
    btts_yes: bttsYes,
    btts_no: 1 - bttsYes,
    minute,
    lambda_home_remaining: lhRem,
    lambda_away_remaining: laRem,
  }
}

// ── Build sharp lookup from Odds API events ─────────────────────────────────

export function buildSharpLookup(events: Array<{ home_team: string; away_team: string; commence_time: string; bookmakers: unknown[] }>, sport: string): Record<string, SharpEvent> {
  const lookup: Record<string, SharpEvent> = {}
  for (const event of events) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const sharp = vigRemove(event as any)
    if (!sharp) continue
    sharp.sport = sport
    sharp.commence_time = event.commence_time
    for (const key of matchKeys(event.home_team, event.away_team)) {
      lookup[key] = sharp
    }
  }
  return lookup
}

// ── Edge reasoning ──────────────────────────────────────────────────────────

export function edgeReasoning(
  pmPrice: number,
  sharpProb: number,
  edgePp: number,
  outcomeLabel: string,
  home: string,
  away: string,
  threshold: number
): string {
  const pmPct = (pmPrice * 100).toFixed(1)
  const sharpPct = (sharpProb * 100).toFixed(1)
  const edgeStr = edgePp.toFixed(1)

  if (Math.abs(edgePp) < threshold) {
    return `${outcomeLabel}: PM prices ${pmPct}% vs sharp consensus ${sharpPct}% — edge of ${edgeStr}pp is below the ${threshold}pp threshold. The market is fairly priced.`
  }
  if (edgePp > 0) {
    return `${outcomeLabel}: PM underprices this at ${pmPct}% while sharp books say ${sharpPct}%. That's a +${edgeStr}pp edge — PM bettors are undervaluing this outcome.`
  }
  return `${outcomeLabel}: PM overprices this at ${pmPct}% vs sharp ${sharpPct}%. Negative edge of ${edgeStr}pp — no value on the Yes side.`
}
