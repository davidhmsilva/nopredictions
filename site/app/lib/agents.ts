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
  first_bet_at: string | null
  last_bet_at: string | null
  /** Cumulative P&L after each settled bet, oldest first, thinned to at most
   *  SPARK_POINTS so a card can draw it without shipping every trade. */
  spark: number[]
}

/** One paper trade as the agent page lists it. `context` is the match state
 *  at entry for the in-play arms that record it (minute, score, pressure). */
export interface AgentTrade {
  id: number
  placed_at: string
  resolved_at: string | null
  game_time: string | null
  event: string
  pick: string
  entry_odds: number | null
  stake_units: number
  result: 'won' | 'lost' | 'void' | null
  pl_units: number | null
  clv: number | null
  live_money: boolean
  context: {
    home: string | null
    away: string | null
    entry_minute: number | null
    goals_at_entry: number | null
    target_line: number | null
    pressure_index: number | null
  } | null
}

export interface AgentDetail {
  agent: AgentSummary
  trades: AgentTrade[]
  /** Cumulative P&L after each settled bet, with its date, thinned to at most
   *  CURVE_POINTS. */
  curve: { t: string; pl: number }[]
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

const SPARK_POINTS = 40
const CURVE_POINTS = 240
const TRADES_SHOWN = 500

/** Keep at most `n` points of a series, always including the last one — the
 *  number a curve ends on is the number printed next to it. */
function thin<T>(xs: T[], n: number): T[] {
  if (xs.length <= n) return xs
  const out: T[] = []
  const step = (xs.length - 1) / (n - 1)
  for (let i = 0; i < n; i++) out.push(xs[Math.round(i * step)])
  return out
}

function cumulative(pls: number[]): number[] {
  let run = 0
  return pls.map((x) => (run += x))
}

type SummaryRow = Omit<AgentSummary, 'spark'> & { pls: number[] | null }

/** The summary rows, owner-checked. Membership comes from v_agent_trades
 *  (db/054), the same rule v_strategy_performance counts by — so the curve on
 *  a card always ends on the P&L printed beside it. */
async function summaries(userId: string, onlyId?: number): Promise<AgentSummary[]> {
  const sql = getSql()
  const rows = await sql<SummaryRow[]>`
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
           p.avg_clv::float8                       as avg_clv,
           p.first_pick_at                         as first_bet_at,
           p.latest_pick_at                        as last_bet_at,
           curve.pls
      from public.strategies s
      left join public.v_strategy_performance p on p.strategy_id = s.id
      left join lateral (
        select array_agg((coalesce(pt.payout_units, 0) - pt.stake_units)::float8
                         order by coalesce(pt.resolved_at, pt.placed_at), pt.id) as pls
          from public.v_agent_trades m
          join public.paper_trades pt on pt.id = m.paper_trade_id
         where m.strategy_id = s.id and pt.result in ('won', 'lost')
      ) curve on true
     where s.owner_id = ${userId}
       and s.retired_at is null
       ${onlyId != null ? sql`and s.id = ${onlyId}` : sql``}
     order by (s.run_status = 'running') desc, p.latest_pick_at desc nulls last, s.created_at desc
  `
  return rows.map(({ pls, ...a }) => ({ ...a, spark: thin(cumulative(pls ?? []), SPARK_POINTS) }))
}

export async function listAgents(userId: string): Promise<{ agents: AgentSummary[]; limits: AgentLimits }> {
  return { agents: await summaries(userId), limits: await limitsFor(userId) }
}

/** One agent with its latest trades and its whole curve, or null when it does
 *  not exist or is not this user's — the two are deliberately the same answer.
 *  Numerics are cast to float8 because postgres.js hands `numeric` back as a
 *  string, and arithmetic on "1.000" concatenates instead of adding. */
export async function getAgent(userId: string, id: number): Promise<AgentDetail | null> {
  const [agent] = await summaries(userId, id)
  if (!agent) return null

  const sql = getSql()
  const [trades, settled] = await Promise.all([
    sql<(Omit<AgentTrade, 'context'> & { ctx_home: string | null; ctx_away: string | null;
          entry_minute: number | null; goals_at_entry: number | null;
          target_line: number | null; pressure_index: number | null })[]>`
      select pt.id, pt.placed_at, pt.resolved_at,
             pm.resolution_time                               as game_time,
             coalesce(pm.title, '—')                          as event,
             pt.outcome                                       as pick,
             pt.entry_odds::float8                            as entry_odds,
             pt.stake_units::float8                           as stake_units,
             pt.result,
             case when pt.result in ('won', 'lost')
                  then (coalesce(pt.payout_units, 0) - pt.stake_units)::float8 end as pl_units,
             pt.clv::float8                                   as clv,
             coalesce(pt.pm_live, false)                      as live_money,
             v.home as ctx_home, v.away as ctx_away, v.entry_minute, v.goals_at_entry,
             v.target_line::float8 as target_line, v.pressure_index::float8 as pressure_index
        from public.v_agent_trades m
        join public.paper_trades pt on pt.id = m.paper_trade_id
        left join public.pm_markets pm on pm.id = pt.market_id
        left join (
          select paper_trade_id, home, away, entry_minute, goals_at_entry, target_line, pressure_index
            from public.v_pressure_trades
          union all
          select paper_trade_id, home, away, entry_minute, goals_at_entry, target_line, pressure_index
            from public.v_ht_pressure_trades
          union all
          select paper_trade_id, home, away, entry_minute, goals_at_entry, target_line, pressure_index
            from public.v_fav_ht_trades
        ) v on v.paper_trade_id = pt.id
       where m.strategy_id = ${id}
       order by pt.placed_at desc, pt.id desc
       limit ${TRADES_SHOWN}
    `,
    sql<{ t: string; pl: number }[]>`
      select coalesce(pt.resolved_at, pt.placed_at) as t,
             (coalesce(pt.payout_units, 0) - pt.stake_units)::float8 as pl
        from public.v_agent_trades m
        join public.paper_trades pt on pt.id = m.paper_trade_id
       where m.strategy_id = ${id} and pt.result in ('won', 'lost')
       order by coalesce(pt.resolved_at, pt.placed_at), pt.id
    `,
  ])

  const running = cumulative(settled.map((r) => r.pl))
  return {
    agent,
    trades: trades.map(({ ctx_home, ctx_away, entry_minute, goals_at_entry, target_line, pressure_index, ...t }) => ({
      ...t,
      context:
        entry_minute != null || ctx_home != null
          ? { home: ctx_home, away: ctx_away, entry_minute, goals_at_entry, target_line, pressure_index }
          : null,
    })),
    curve: thin(settled.map((r, i) => ({ t: r.t, pl: running[i] })), CURVE_POINTS),
  }
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
