import { createClient } from '@supabase/supabase-js'

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? 'https://pnpjyrvvbzsdrwtymyzz.supabase.co'
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBucGp5cnZ2YnpzZHJ3dHlteXp6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY3ODk0ODcsImV4cCI6MjA5MjM2NTQ4N30.RWa6hRDHc3QKHU5TOwKRnJ75FmRP6iJMG2CaEPTrnso'

export const supabase = createClient(supabaseUrl, supabaseAnonKey)

// ── Types ──────────────────────────────────────────────────────────────────

export interface DbStats {
  matches: number
  oddsRecords: number
  leagues: number
  earliestMatch: string
  latestMatch: string
}

export interface Strategy {
  id: number
  hypothesis_id: number | null
  name: string
  rules: Record<string, unknown> | null
  promoted_at: string | null
  retired_at: string | null
  retirement_reason: string | null
  /** Set when this row is a SLICE of another strategy's trades rather than a
   *  strategy of its own (v2, and the synthetic Live Polymarket aggregate).
   *  Such a row must be left out of any total summed across strategies — its
   *  trades are already counted under the parent. */
  parent_strategy_id: number | null
  // aggregated stats
  total_bets?: number
  wins?: number
  losses?: number
  win_rate?: number
  avg_clv?: number | null
  total_pnl?: number
  yield_pct?: number | null
}

/** Live Pressure Overs — full-match over N.5, bought on late in-game pressure. */
export const PRESSURE_STRATEGY_ID = 16

/** Live Pressure HT Over 0.5 — the same signal, read over a goalless opening 15. */
export const HT_PRESSURE_STRATEGY_ID = 17

/** Live Pressure Favourite HT — the favourite, on top and still level at 15'. */
export const FAV_PRESSURE_STRATEGY_ID = 18

/**
 * Live Pressure Overs v2 — a READING of strategy 16, not a second agent.
 *
 * It decides nothing and buys nothing: the agent keeps entering exactly as it
 * does today, and v2 is the subset of those entries that lands in the window
 * below. So it owns no rows in paper_trades, and its P&L is a slice of 16's,
 * never an addition to it — see V2_WINDOW and db/041.
 */
export const PRESSURE_V2_STRATEGY_ID = 19

/**
 * The v2 window, and the date it starts counting.
 *
 * The window was picked as the best cell of a minute x odds grid over 112
 * settled entries, where reshuffled noise finds an equally good cell half the
 * time (p=0.390) — so the discovery sample is NOT part of the record. Anything
 * strategy 16 placed before `from` is excluded here exactly as it is in the
 * view, or the arm would open on the in-sample number it was chosen for.
 *
 * ⚠️ Duplicated from db/041 by necessity — the view cannot filter the trade
 * list the browser holds. Change both or the header count and the rows below
 * it will disagree.
 */
export const V2_WINDOW = {
  minMinute: 75,
  maxMinute: 84,
  minOdds: 2.0,
  maxOddsExclusive: 3.0,
  from: Date.parse('2026-09-05T17:50:00Z'),
} as const

/** Does this strategy-16 entry fall in the v2 window? `entryMinute` comes from
 *  v_pressure_trades — an entry without one cannot be placed and is left out. */
export function inV2Window(trade: PaperTrade, entryMinute?: number): boolean {
  if (trade.strategy_id !== PRESSURE_STRATEGY_ID) return false
  if (entryMinute == null) return false
  if (Date.parse(trade.placed_at) < V2_WINDOW.from) return false
  const odds = Number(trade.entry_odds)
  return (
    entryMinute >= V2_WINDOW.minMinute &&
    entryMinute <= V2_WINDOW.maxMinute &&
    odds >= V2_WINDOW.minOdds &&
    odds < V2_WINDOW.maxOddsExclusive
  )
}

/** All three carry per-entry match state the site shows on their detail page. */
export const PRESSURE_STRATEGY_IDS = [
  PRESSURE_STRATEGY_ID,
  HT_PRESSURE_STRATEGY_ID,
  FAV_PRESSURE_STRATEGY_ID,
  PRESSURE_V2_STRATEGY_ID,
]

// Which agents the public dashboard shows. Everything else is paused, so
// listing them would advertise P&L nothing is still producing. Filtered at the
// query, not in the components, so every view — table, totals, ticker — agrees.
export const VISIBLE_STRATEGY_IDS = PRESSURE_STRATEGY_IDS

// ── Queries ────────────────────────────────────────────────────────────────

export async function fetchDbStats(): Promise<DbStats> {
  const [matchRes, oddsRes, leagueRes, rangeRes] = await Promise.all([
    supabase.from('matches').select('*', { count: 'exact', head: true }),
    supabase.from('match_odds').select('*', { count: 'exact', head: true }),
    supabase.from('leagues').select('*', { count: 'exact', head: true }),
    supabase
      .from('matches')
      .select('kickoff_utc')
      .order('kickoff_utc', { ascending: true })
      .limit(1),
  ])

  const latestRes = await supabase
    .from('matches')
    .select('kickoff_utc')
    .order('kickoff_utc', { ascending: false })
    .limit(1)

  return {
    matches: matchRes.count ?? 0,
    oddsRecords: oddsRes.count ?? 0,
    leagues: leagueRes.count ?? 0,
    earliestMatch: rangeRes.data?.[0]?.kickoff_utc ?? '',
    latestMatch: latestRes.data?.[0]?.kickoff_utc ?? '',
  }
}

export async function fetchStrategies(): Promise<Strategy[]> {
  const { data, error } = await supabase
    .from('strategies')
    .select('*')
    .order('promoted_at', { ascending: false })

  if (error) {
    console.error('fetchStrategies error:', error)
    return []
  }
  return data ?? []
}

export async function fetchLeaderboard(): Promise<Strategy[]> {
  const { data, error } = await supabase
    .from('v_strategy_performance')
    .select('*')
    .in('strategy_id', VISIBLE_STRATEGY_IDS)
    .order('promoted_at', { ascending: false })

  if (error || !data?.length) return []

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return data.map((r: any) => ({
    id:                r.strategy_id,
    hypothesis_id:     null,
    name:              r.strategy_name,
    rules:             null,
    promoted_at:       r.promoted_at,
    retired_at:        r.retired_at,
    retirement_reason: r.retirement_reason,
    parent_strategy_id: r.parent_strategy_id != null ? Number(r.parent_strategy_id) : null,
    total_bets:        Number(r.n_wins) + Number(r.n_losses),
    wins:              Number(r.n_wins),
    losses:            Number(r.n_losses),
    win_rate:          (Number(r.n_wins) + Number(r.n_losses)) > 0 ? Number(r.n_wins) / (Number(r.n_wins) + Number(r.n_losses)) : 0,
    avg_clv:           r.avg_clv != null ? Number(r.avg_clv) : null,
    total_pnl:         Number(r.pl_units),
    yield_pct:         r.yield_pct != null ? Number(r.yield_pct) : null,
  }))
}

// ── Paper Trades ────────────────────────────────────────────────────────────

export interface PaperTrade {
  id: number
  strategy_id: number
  market_id: number
  outcome: string
  entry_price: number
  entry_odds: number
  stake_units: number
  model_probability: number
  expected_edge: number   // already in percentage points (e.g. 29.69)
  reasoning: string | null
  critic_assessment: string | null
  confidence: number
  placed_at: string
  result: string | null   // 'won' | 'lost' | null
  payout_units: number | null
  closing_price: number | null
  clv: number | null
  resolved_at: string | null
  sharp_consensus_sources: Record<string, unknown> | null
  // PM live trading fields (set when order was actually submitted on-chain)
  pm_live: boolean | null
  pm_order_status: string | null
  pm_order_size: number | null
  pm_order_price: number | null
  pm_size_matched: number | null
  pm_executed_at: string | null
  pm_current_value: number | null
  pm_cash_pnl: number | null
  pm_percent_pnl: number | null
  // joined
  market_title: string
  strategy_name: string
  game_time: string | null
}

/** Synthetic id for the aggregated "Live Polymarket" strategy (see db/008). */
export const LIVE_POLYMARKET_STRATEGY_ID = 9

/**
 * Per-entry match state for the two pressure agents.
 *
 * paper_trades carries no game clock at all, so the minute a bet was placed
 * and the minute the goal arrived both live on pressure_observations. The
 * v_pressure_trades view (db/032) is the public projection of exactly those
 * columns — the observations table itself is 60+ columns of in-progress
 * research and is not something to hand the anon key.
 */
export interface PressureTrade {
  paper_trade_id: number
  home: string | null
  away: string | null
  entry_minute: number
  goals_at_entry: number | null
  target_line: number | null
  entry_price: number | null
  pressure_index: number | null
  goal_minute: number | null
  /** 'api' = exact, includes stoppage time. 'poll' = our 60s tape, caps at 90. */
  goal_minute_source: string | null
  won: boolean | null
  final_goals: number | null
}

export async function fetchPressureTrades(): Promise<Map<number, PressureTrade>> {
  // Three views, one shape (db/033 and db/034 keep the half-time views
  // column-compatible with v_pressure_trades). paper_trade_id is unique across
  // all of them, so one map serves every agent's detail page.
  const results = await Promise.all(
    ['v_pressure_trades', 'v_ht_pressure_trades', 'v_fav_ht_trades'].map((view) =>
      supabase.from(view).select('*').order('paper_trade_id', { ascending: false }).limit(500)
    )
  )

  const out = new Map<number, PressureTrade>()
  for (const { data, error } of results) {
    if (error) {
      console.error('fetchPressureTrades error:', error)
      continue
    }
    for (const r of (data ?? []) as PressureTrade[]) out.set(r.paper_trade_id, r)
  }
  return out
}

export async function fetchPaperTrades(): Promise<PaperTrade[]> {
  const { data, error } = await supabase
    .from('paper_trades')
    .select(`
      *,
      pm_markets!market_id ( title, resolution_time ),
      strategies!strategy_id ( name )
    `)
    .in('strategy_id', VISIBLE_STRATEGY_IDS)
    .order('placed_at', { ascending: false })
    .limit(500)

  if (error) {
    console.error('fetchPaperTrades error:', error)
    return []
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (data ?? []).map((r: any) => ({
    ...r,
    market_title: r.pm_markets?.title ?? '—',
    strategy_name: r.strategies?.name ?? '—',
    game_time: r.pm_markets?.resolution_time ?? null,
  }))
}
