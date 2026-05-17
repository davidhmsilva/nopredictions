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
  // aggregated stats
  total_bets?: number
  wins?: number
  win_rate?: number
  avg_clv?: number
  total_pnl?: number
  yield_pct?: number
}

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
    total_bets:        Number(r.n_trades),
    wins:              Number(r.n_wins),
    win_rate:          r.n_trades > 0 ? Number(r.n_wins) / Number(r.n_trades) : 0,
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
  // joined
  market_title: string
  strategy_name: string
}

export async function fetchPaperTrades(): Promise<PaperTrade[]> {
  const { data, error } = await supabase
    .from('paper_trades')
    .select(`
      *,
      pm_markets!market_id ( title ),
      strategies!strategy_id ( name )
    `)
    .order('placed_at', { ascending: false })
    .limit(30)

  if (error) {
    console.error('fetchPaperTrades error:', error)
    return []
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (data ?? []).map((r: any) => ({
    ...r,
    market_title: r.pm_markets?.title ?? '—',
    strategy_name: r.strategies?.name ?? '—',
  }))
}
