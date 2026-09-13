/** A user's agents — read and written on the server, never with the anon key.
 *
 *  An agent is a row in `strategies` with an owner (db/049). Two kinds:
 *
 *    source='agent'  a hand-written daemon on the operator's machine (the
 *                    pressure arms). Its owner sees its record here; it is
 *                    switched on and off on that machine, never from the site.
 *    source='lab'    built from a Lab theory. Its owner switches it on, and
 *                    agent/lab_strategy_runner.py paper-trades it on today's
 *                    Polymarket boards.
 *
 *  🔑 Every query carries the owner check in its own WHERE clause and runs over
 *     DATABASE_URL. Not in a component, and not in an RLS policy the client key
 *     might one day be granted around — db/050 removes the public read of
 *     strategies and paper_trades altogether.
 *
 *  🔑 The backtest stored with a saved agent is RECOMPUTED here from the spec,
 *     never accepted from the browser. A leaderboard that ranks on numbers a
 *     client posted is a form anyone can fill in.
 *
 *  Server only: imports the Postgres client.
 */

import { getSql } from './db'
import {
  FILTER_FIELDS,
  LEAGUE_CODES,
  computeStats,
  isNbaMarket,
  verdict,
  type RawBacktest,
  type Spec,
} from './backtest'
import { accountOf, type Plan } from './plan'

export type RunStatus = 'draft' | 'running' | 'paused'

/** What a plan may keep and run at once. The owner's account is unlimited. */
export const AGENT_LIMITS: Record<Plan, { saved: number; running: number }> = {
  free: { saved: 5, running: 1 },
  pro: { saved: 50, running: 10 },
}

/** The season the live agent is trading in — as a Lab season start year. */
const CURRENT_SEASON = 2026

export interface SavedBacktest {
  stats: ReturnType<typeof computeStats>
  verdict: ReturnType<typeof verdict>
  ran_at: string
}

export interface AgentSummary {
  id: number
  name: string
  source: 'agent' | 'lab'
  theory: string | null
  interpretation: string | null
  spec: Spec | null
  backtest: SavedBacktest | null
  run_status: RunStatus
  run_blocker: string | null
  is_public: boolean
  created_at: string
  promoted_at: string | null
  parent_strategy_id: number | null
  // The paper record since it started running.
  n_trades: number
  n_open: number
  wins: number
  losses: number
  staked: number
  pl_units: number
  yield_pct: number | null
  avg_clv: number | null
}

export interface AgentLimits {
  unlimited: boolean
  saved: number | null
  running: number | null
  used_saved: number
  used_running: number
}

// ── the spec a client may send ──────────────────────────────────────────────

const MARKETS = ['1x2', 'ou25', 'nba_ml', 'nba_spread', 'nba_total'] as const
const SIDES = ['home', 'draw', 'away', 'over', 'under'] as const
const NUMERIC_KEYS: string[] = [
  'season_start',
  'season_end',
  ...FILTER_FIELDS.filter((f) => f !== 'season').flatMap((f) => [`${f}_min`, `${f}_max`]),
]

/** A Spec rebuilt from whatever the browser sent, keeping only the fields
 *  run_backtest understands, with the types it expects. Null when it cannot be
 *  a Lab spec at all. */
export function cleanSpec(input: unknown): Spec | null {
  if (!input || typeof input !== 'object') return null
  const s = input as Record<string, unknown>
  if (!MARKETS.includes(s.market as never) || !SIDES.includes(s.side as never)) return null

  const out: Record<string, unknown> = { market: s.market, side: s.side }
  const leagues = Array.isArray(s.leagues)
    ? s.leagues.filter((c): c is string => typeof c === 'string' && LEAGUE_CODES.has(c))
    : []
  out.leagues = leagues.length ? leagues : null
  out.fav_status = s.fav_status === 'favorite' || s.fav_status === 'underdog' ? s.fav_status : null
  for (const k of ['home_team', 'away_team']) {
    const v = s[k]
    out[k] = typeof v === 'string' && v.trim() ? v.trim().slice(0, 60) : null
  }
  if (s.game_type === 'regular' || s.game_type === 'playoff') out.game_type = s.game_type
  for (const k of NUMERIC_KEYS) {
    const v = s[k]
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v
  }
  return out as unknown as Spec
}

// Filters the live agent cannot evaluate yet. ⚠️ Mirrored in
// agent/lab_strategy_runner.py (LIVE_UNSUPPORTED), which is the authority —
// it re-checks every running spec and writes run_blocker itself.
const LIVE_UNSUPPORTED = [
  'home_rest_days',
  'away_rest_days',
  'home_form_pts5',
  'away_form_pts5',
  'home_avg_tg5',
  'away_avg_tg5',
] as const

/** Why this spec cannot run live, or null when it can. Said to the user as
 *  written, so it explains rather than refuses. */
export function runBlocker(spec: Spec): string | null {
  if (isNbaMarket(spec.market)) {
    return 'NBA theories are backtest-only: the NBA data ends in 2021-22 and there are no NBA boards this agent trades.'
  }
  const s = spec as unknown as Record<string, unknown>
  const used = LIVE_UNSUPPORTED.filter((f) => s[`${f}_min`] != null || s[`${f}_max`] != null)
  if (used.length) {
    return `Uses ${used.map((f) => f.replace(/_/g, ' ')).join(', ')} — the live agent cannot compute those yet, so it would be trading a different rule from the one you tested.`
  }
  if (spec.season_end != null && spec.season_end < CURRENT_SEASON - 1) {
    return `Tested only up to the ${spec.season_end}-${String(spec.season_end + 1).slice(2)} season; running it today would trade outside what was tested.`
  }
  return null
}

// ── reads ───────────────────────────────────────────────────────────────────

async function limitsFor(userId: string): Promise<AgentLimits> {
  const sql = getSql()
  const [{ saved, running }] = await sql<{ saved: number; running: number }[]>`
    select count(*) filter (where source = 'lab')::int                          as saved,
           count(*) filter (where source = 'lab' and run_status = 'running')::int as running
      from public.strategies
     where owner_id = ${userId} and retired_at is null
  `
  const { plan, role } = await accountOf(userId)
  if (role === 'owner') {
    return { unlimited: true, saved: null, running: null, used_saved: saved, used_running: running }
  }
  const cap = AGENT_LIMITS[plan]
  return { unlimited: false, saved: cap.saved, running: cap.running, used_saved: saved, used_running: running }
}

export async function listAgents(userId: string): Promise<{ agents: AgentSummary[]; limits: AgentLimits }> {
  const sql = getSql()
  const rows = await sql<AgentSummary[]>`
    select s.id, s.name, s.source, s.theory, s.interpretation, s.spec, s.backtest,
           s.run_status, s.run_blocker, s.is_public, s.created_at, s.promoted_at,
           p.parent_strategy_id,
           coalesce(p.n_trades, 0)::int            as n_trades,
           coalesce(p.n_open, 0)::int              as n_open,
           coalesce(p.n_wins, 0)::int              as wins,
           coalesce(p.n_losses, 0)::int            as losses,
           coalesce(p.total_staked, 0)::float8     as staked,
           coalesce(p.pl_units, 0)::float8         as pl_units,
           p.yield_pct::float8                     as yield_pct,
           p.avg_clv::float8                       as avg_clv
      from public.strategies s
      left join public.v_strategy_performance p on p.strategy_id = s.id
     where s.owner_id = ${userId}
       and s.retired_at is null
     order by (s.run_status = 'running') desc, s.created_at desc
  `
  return { agents: rows, limits: await limitsFor(userId) }
}

/** Every paper trade of the user's agents, in the shape the record components
 *  already take (lib/supabase PaperTrade), plus the per-entry match state of
 *  the pressure arms. Numerics are cast to float8 because postgres.js hands
 *  `numeric` back as a string, and a component doing arithmetic on "1.000"
 *  concatenates instead of adding. */
export async function agentTrades(userId: string) {
  const sql = getSql()
  const trades = await sql`
    select pt.id, pt.strategy_id, pt.market_id, pt.outcome,
           pt.entry_price::float8 as entry_price, pt.entry_odds::float8 as entry_odds,
           pt.stake_units::float8 as stake_units,
           pt.model_probability::float8 as model_probability,
           pt.expected_edge::float8 as expected_edge,
           pt.reasoning, pt.critic_assessment,
           pt.confidence, pt.placed_at, pt.result,
           pt.payout_units::float8 as payout_units,
           pt.closing_price::float8 as closing_price, pt.clv::float8 as clv,
           pt.resolved_at, pt.sharp_consensus_sources,
           pt.pm_live, pt.pm_order_status,
           pt.pm_order_size::float8 as pm_order_size, pt.pm_order_price::float8 as pm_order_price,
           pt.pm_size_matched::float8 as pm_size_matched, pt.pm_executed_at,
           pt.pm_current_value::float8 as pm_current_value,
           pt.pm_cash_pnl::float8 as pm_cash_pnl, pt.pm_percent_pnl::float8 as pm_percent_pnl,
           coalesce(pm.title, '—') as market_title,
           s.name as strategy_name,
           pm.resolution_time as game_time
      from public.paper_trades pt
      join public.strategies s on s.id = pt.strategy_id
      left join public.pm_markets pm on pm.id = pt.market_id
     where s.owner_id = ${userId}
     order by pt.placed_at desc
     limit 1000
  `
  const ids = trades.map((t) => t.id as number)
  const pressure = ids.length
    ? await sql`
        select paper_trade_id, home, away, entry_minute, goals_at_entry,
               target_line::float8 as target_line, entry_price::float8 as entry_price,
               pressure_index::float8 as pressure_index, goal_minute, goal_minute_source,
               won, final_goals
          from (
            select * from public.v_pressure_trades
            union all select * from public.v_ht_pressure_trades
            union all select * from public.v_fav_ht_trades
          ) v
         where paper_trade_id = any(${ids})
      `
    : []
  return { trades, pressure }
}

// ── writes ──────────────────────────────────────────────────────────────────

type Result<T = object> = ({ ok: true } & T) | { ok: false; status: number; error: string }

export async function saveLabAgent(
  userId: string,
  input: { hypothesis: string; interpretation: string; spec: unknown },
): Promise<Result<{ id: number }>> {
  const spec = cleanSpec(input.spec)
  if (!spec) return { ok: false, status: 400, error: 'That is not a spec the Lab produced.' }

  const limits = await limitsFor(userId)
  if (!limits.unlimited && limits.used_saved >= (limits.saved ?? 0)) {
    return {
      ok: false,
      status: 402,
      error: `Your plan keeps ${limits.saved} agents. Archive one, or upgrade to keep more.`,
    }
  }

  const sql = getSql()
  const rows = isNbaMarket(spec.market)
    ? await sql`select run_backtest_nba(${sql.json(spec as never)}) as r`
    : await sql`select run_backtest(${sql.json(spec as never)}) as r`
  const stats = computeStats(rows[0].r as RawBacktest)
  const backtest: SavedBacktest = { stats, verdict: verdict(stats), ran_at: new Date().toISOString() }

  const theory = input.hypothesis.trim().slice(0, 500)
  const interpretation = input.interpretation.trim().slice(0, 500)
  const name = (theory || interpretation).slice(0, 90)
  const [row] = await sql<{ id: number }[]>`
    insert into public.strategies
      (name, source, owner_id, theory, interpretation, spec, backtest,
       run_status, run_blocker, promoted_at, rules)
    values
      (${name}, 'lab', ${userId}, ${theory}, ${interpretation},
       ${sql.json(spec as never)}, ${sql.json(backtest as never)},
       'draft', ${runBlocker(spec)}, now(), ${sql.json({ kind: 'lab' } as never)})
    returning id
  `
  return { ok: true, id: row.id }
}

export async function updateAgent(
  userId: string,
  id: number,
  patch: { run_status?: RunStatus; is_public?: boolean; name?: string },
): Promise<Result> {
  const sql = getSql()
  const [s] = await sql<{ source: string; spec: Spec | null; run_status: RunStatus }[]>`
    select source, spec, run_status
      from public.strategies
     where id = ${id} and owner_id = ${userId} and retired_at is null
  `
  if (!s) return { ok: false, status: 404, error: 'No such agent.' }

  if (s.source !== 'lab' && (patch.run_status !== undefined || patch.is_public !== undefined)) {
    return {
      ok: false,
      status: 409,
      error: 'Hand-written agents are switched on the machine they run on, not from the site.',
    }
  }

  if (patch.run_status === 'running' && s.run_status !== 'running') {
    const blocker = s.spec ? runBlocker(s.spec) : 'This agent has no spec to run.'
    if (blocker) return { ok: false, status: 409, error: blocker }
    const limits = await limitsFor(userId)
    if (!limits.unlimited && limits.used_running >= (limits.running ?? 0)) {
      return {
        ok: false,
        status: 402,
        error: `Your plan runs ${limits.running} agent${limits.running === 1 ? '' : 's'} at a time. Pause one first, or upgrade.`,
      }
    }
  }

  const name = patch.name?.trim().slice(0, 90) || null
  await sql`
    update public.strategies
       set run_status = coalesce(${patch.run_status ?? null}::text, run_status),
           is_public  = coalesce(${patch.is_public ?? null}::boolean, is_public),
           name       = coalesce(${name}::text, name),
           run_blocker = case when ${patch.run_status ?? null}::text = 'running'
                              then null else run_blocker end
     where id = ${id} and owner_id = ${userId}
  `
  return { ok: true }
}

/** Archive, never delete: its paper trades reference it, and a record that can
 *  be erased after a bad month is not a record. */
export async function archiveAgent(userId: string, id: number): Promise<Result> {
  const sql = getSql()
  const rows = await sql`
    update public.strategies
       set retired_at = now(), run_status = 'paused', is_public = false,
           retirement_reason = 'archived by its owner'
     where id = ${id} and owner_id = ${userId} and source = 'lab' and retired_at is null
    returning id
  `
  if (!rows.length) return { ok: false, status: 404, error: 'No such agent, or it cannot be archived.' }
  return { ok: true }
}
