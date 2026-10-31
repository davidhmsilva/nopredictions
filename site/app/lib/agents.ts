/** Agents — read and written on the server, never with the anon key.
 *
 *  An agent is a row in `strategies` with an owner (db/049). Two kinds:
 *
 *    source='agent'  a hand-written daemon on the operator's machine (the
 *                    pressure arms). Its owner sees its record here; it is
 *                    switched on and off on that machine, never from the site.
 *    source='lab'    built on Create from a user's idea. Its owner deploys it,
 *                    and agent/lab_strategy_runner.py (pre-match) or
 *                    agent/lab_inplay_runner.py (live rules) paper-trades it
 *                    on today's Polymarket boards.
 *
 *  Since 2026-10-28 an owner may PUBLISH a 'lab' agent to the Marketplace,
 *  where anyone can read its rule and its record and COPY it (db/082). A copy
 *  is a snapshot — its own row, its own owner, its own paper trades from the
 *  moment it runs. The original's record is never shared with it.
 *
 *  🔑 Every query carries its scope in its own WHERE clause and runs over
 *     DATABASE_URL: the owner check for a user's own agents, `is_public` for
 *     the Marketplace. Not in a component, and not in an RLS policy the client
 *     key might one day be granted around — db/050 removes the public read of
 *     strategies and paper_trades altogether.
 *
 *  🔑 The backtest stored with a saved agent is RECOMPUTED here from the spec,
 *     never accepted from the browser. A marketplace that ranks on numbers a
 *     client posted is a form anyone can fill in. A copy carries its
 *     original's stored backtest, which this file wrote in the first place.
 *
 *  🔑 An author is shown by a public name or a pseudonym, never by email.
 *
 *  Server only: imports the Postgres client.
 */

import { getSql } from './db'
import {
  FILTER_FIELDS,
  LEAGUE_CODES,
  VENUE_MARKETS,
  computeStats,
  isNbaMarket,
  marketTakesSide,
  namesItsTeam,
  verdict,
  type Spec,
} from './backtest'
import { rawBacktest } from './labRun'
import { accountOf } from './plan'
import { AGENT_LIMITS } from './planTerms'
import { cleanInplay, isInplaySpec, type InplaySpec } from './inplaySpec'

export type RunStatus = 'draft' | 'running' | 'paused'

export { AGENT_LIMITS }

/** The season the live agent is trading in, as a season start year. */
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
  /** A pre-match filter, or a live rule (kind='inplay', lib/inplaySpec). */
  spec: Spec | InplaySpec | null
  backtest: SavedBacktest | null
  run_status: RunStatus
  run_blocker: string | null
  is_public: boolean
  created_at: string
  promoted_at: string | null
  parent_strategy_id: number | null
  /** When it first went on the Marketplace; kept through an unpublish. */
  published_at: string | null
  /** The agent this one was copied from, and its name while it is still
   *  published. A copy whose original went private just says it is a copy. */
  copied_from: number | null
  copied_from_name: string | null
  /** How many times it has been copied, archived copies included. */
  copies: number
  /** Who it is shown under: a public name, NOPREDICTIONS for the operator, or
   *  a pseudonym derived from the account. Never an email. */
  author: string
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

/** A resting paper bid of a maker agent (db/073, close_maker_agent.py). Prices
 *  are probabilities; the page turns them into odds. Unfilled bids are kept
 *  after kick-off, because filled-vs-unfilled is how adverse selection shows. */
export interface AgentOrder {
  id: number
  created_at: string
  kickoff: string
  title: string | null
  team: string | null
  league: string
  status: 'resting' | 'filled' | 'expired' | 'cancelled'
  bid_price: number
  market_mid: number | null
  model_q: number
  close_mid: number | null
  payout: number | null
}

/** One Pre-Match Drift position (db/020, drift_scanner/drift_monitor). The
 *  agent buys NO on a longshot early and exits before kick-off, so a position
 *  is not a paper_trade: it has an entry and an exit price on the NO side, a
 *  take-profit target, and a realised P&L. Prices are probabilities; the page
 *  turns them into odds. Drift agents only — empty for every other agent. */
export interface DriftPosition {
  id: number
  entry_at: string
  exit_at: string | null
  kickoff: string | null
  home: string | null
  away: string | null
  /** home_win | draw | away_win — the outcome we bet AGAINST (we hold NO). */
  outcome: string
  entry_no: number
  exit_no: number | null
  target_no: number | null
  status: 'open' | 'closed'
  exit_reason: string | null
  pl_units: number | null
}

export interface AgentDetail {
  agent: AgentSummary
  trades: AgentTrade[]
  /** Maker agents only: their bids, newest first. Empty for every other agent. */
  orders: AgentOrder[]
  /** Drift agents only: their positions, newest first. Empty for every other. */
  positions: DriftPosition[]
  /** Drift agents only: positions booked before db/076 at the wrong side of the
   *  book (bought at the NO bid, sold at the NO ask). They are left out of every
   *  number and every list; this is how many there are. 0 for every other. */
  legacy_positions: number
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

const MARKETS = ['1x2', 'ou25', ...VENUE_MARKETS, 'nba_ml', 'nba_spread', 'nba_total'] as const
const SIDES = ['home', 'draw', 'away', 'over', 'under', 'yes', 'no', 'team', 'opponent'] as const
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
  if (!marketTakesSide(String(s.market), String(s.side))) return null

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
  if (typeof s.team === 'string' && s.team.trim()) out.team = s.team.trim().slice(0, 60)
  if (s.game_type === 'regular' || s.game_type === 'playoff') out.game_type = s.game_type
  for (const k of NUMERIC_KEYS) {
    const v = s[k]
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v
  }
  const spec = out as unknown as Spec
  return namesItsTeam(spec) ? spec : null
}

/** Why this spec cannot run live, or null when it can. Said to the user as
 *  written, so it explains rather than refuses. ⚠️ Mirrored in
 *  agent/lab_strategy_runner.py (run_blocker), which is the authority: it
 *  re-checks every running spec and writes run_blocker itself. */
export function runBlocker(spec: Spec | InplaySpec): string | null {
  // A live rule was checked field by field when it was saved (cleanInplay);
  // there is nothing about it the runner cannot compute.
  if (isInplaySpec(spec)) return null
  if (isNbaMarket(spec.market)) {
    return 'NBA ideas are backtest-only: the NBA data ends in 2021-22 and there are no NBA boards this agent trades.'
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

type SummaryRow = Omit<AgentSummary, 'spark'> & { pls: number[] | null; rules: { kind?: string } | null }

/** Is this a Pre-Match Drift agent? Its record lives in drift_positions, not
 *  paper_trades, so v_strategy_performance has nothing for it and its summary
 *  is filled in separately (driftPerf). */
function isDrift(rules: { kind?: string } | null): boolean {
  return rules?.kind === 'drift'
}

/** Whose agents a read may see: one user's own, or everything published. */
type Scope = { owner: string } | { published: true }

/** The name an author is shown under, as SQL over `profiles pr` and the
 *  strategy's owner `s.owner_id`. The pseudonym is a hash of the account id,
 *  so it is stable, says nothing about the account, and is the same on every
 *  page — publicNameOf() below uses the same expression. */
const AUTHOR_SQL = `
  case when pr.role = 'owner' then 'NOPREDICTIONS'
       when pr.public_name is not null then pr.public_name
       when s.owner_id is not null then 'Trader ' || upper(substr(md5(s.owner_id::text), 1, 4))
       else 'A NOPREDICTIONS user' end`

/** The summary rows, scoped. Membership comes from v_agent_trades (db/054),
 *  the same rule v_strategy_performance counts by — so the curve on a card
 *  always ends on the P&L printed beside it. The Marketplace sees 'lab' agents
 *  only: a hand-written daemon has no rule anyone else could copy. */
async function summaries(scope: Scope, onlyId?: number): Promise<AgentSummary[]> {
  const sql = getSql()
  const where = 'owner' in scope
    ? sql`s.owner_id = ${scope.owner}`
    : sql`s.is_public and s.source = 'lab'`
  const order = 'owner' in scope
    ? sql`(s.run_status = 'running') desc, p.latest_pick_at desc nulls last, s.created_at desc`
    : sql`coalesce(p.pl_units, 0) desc, s.published_at desc nulls last, s.id`
  const rows = await sql<SummaryRow[]>`
    select s.id, s.name, s.source, s.theory, s.interpretation, s.spec, s.backtest,
           s.run_status, s.run_blocker, s.is_public, s.created_at, s.promoted_at, s.rules,
           s.published_at, s.copied_from,
           case when o.is_public and o.retired_at is null then o.name end as copied_from_name,
           (select count(*)::int from public.strategies c where c.copied_from = s.id) as copies,
           ${sql.unsafe(AUTHOR_SQL)} as author,
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
      left join public.profiles pr on pr.id = s.owner_id
      left join public.strategies o on o.id = s.copied_from
      left join public.v_strategy_performance p on p.strategy_id = s.id
      left join lateral (
        select array_agg((coalesce(pt.payout_units, 0) - pt.stake_units)::float8
                         order by coalesce(pt.resolved_at, pt.placed_at), pt.id) as pls
          from public.v_agent_trades m
          join public.paper_trades pt on pt.id = m.paper_trade_id
         where m.strategy_id = s.id and pt.result in ('won', 'lost')
      ) curve on true
     where ${where}
       and s.retired_at is null
       ${onlyId != null ? sql`and s.id = ${onlyId}` : sql``}
     order by ${order}
  `

  // Drift agents keep their record in drift_positions, so overwrite the (empty)
  // v_strategy_performance numbers with the ones from that table.
  const driftIds = rows.filter((r) => isDrift(r.rules)).map((r) => r.id)
  const perf = driftIds.length ? await driftPerf(driftIds) : new Map<number, DriftPerf>()

  return rows.map(({ pls, rules, ...a }) => {
    const d = isDrift(rules) ? perf.get(a.id) : undefined
    if (d) {
      return {
        ...a,
        n_trades: d.n_trades, n_open: d.n_open, wins: d.wins, losses: d.losses,
        staked: d.staked, pl_units: d.pl_units, yield_pct: d.yield_pct, avg_clv: null,
        first_bet_at: d.first_bet_at, last_bet_at: d.last_bet_at,
        spark: thin(cumulative(d.pls), SPARK_POINTS),
      }
    }
    return { ...a, spark: thin(cumulative(pls ?? []), SPARK_POINTS) }
  })
}

interface DriftPerf {
  n_trades: number
  n_open: number
  wins: number
  losses: number
  staked: number
  pl_units: number
  yield_pct: number | null
  first_bet_at: string | null
  last_bet_at: string | null
  pls: number[]
}

/** A drift agent's record, read from drift_positions. A closed position counts
 *  as won (NO P&L > 0), lost (< 0) or void (= 0, a monitor miss or a voided
 *  fixture); voids sit in the list but, like a void paper trade, stay out of
 *  the staked/P&L/yield the way v_strategy_performance keeps them out. The 1u
 *  stake is $1, so realised_pnl_usd already reads as units.
 *
 *  Only price_convention = 'executable' rows count (db/076). Everything booked
 *  before it bought at the NO bid and sold at the NO ask, so its "+22%" was the
 *  spread, not a result. */
async function driftPerf(ids: number[]): Promise<Map<number, DriftPerf>> {
  const sql = getSql()
  let rows: {
    strategy_id: number; n_trades: number; n_open: number; wins: number; losses: number
    staked: number; pl_units: number; first_bet_at: string | null; last_bet_at: string | null
    pls: number[] | null
  }[]
  try {
    rows = await sql`
      select strategy_id,
             count(*) filter (where status = 'closed' and realized_pnl_usd <> 0)::int as n_trades,
             count(*) filter (where status = 'open')::int                             as n_open,
             count(*) filter (where status = 'closed' and realized_pnl_usd > 0)::int   as wins,
             count(*) filter (where status = 'closed' and realized_pnl_usd < 0)::int   as losses,
             coalesce(sum(stake_usd)        filter (where status = 'closed' and realized_pnl_usd <> 0), 0)::float8 as staked,
             coalesce(sum(realized_pnl_usd) filter (where status = 'closed' and realized_pnl_usd <> 0), 0)::float8 as pl_units,
             min(entry_at)                        as first_bet_at,
             max(coalesce(exit_at, entry_at))     as last_bet_at,
             array_agg(realized_pnl_usd::float8 order by coalesce(exit_at, entry_at), id)
               filter (where status = 'closed' and realized_pnl_usd <> 0) as pls
        from public.drift_positions
       where strategy_id in ${sql(ids)}
         and price_convention = 'executable'
       group by strategy_id
    ` as never
  } catch (err) {
    if ((err as { code?: string }).code === '42P01') return new Map()  // table absent
    throw err
  }
  const m = new Map<number, DriftPerf>()
  for (const r of rows) {
    m.set(r.strategy_id, {
      n_trades: r.n_trades, n_open: r.n_open, wins: r.wins, losses: r.losses,
      staked: r.staked, pl_units: r.pl_units,
      yield_pct: r.staked > 0 ? (r.pl_units / r.staked) * 100 : null,
      first_bet_at: r.first_bet_at, last_bet_at: r.last_bet_at,
      pls: r.pls ?? [],
    })
  }
  return m
}

export async function listAgents(userId: string): Promise<{ agents: AgentSummary[]; limits: AgentLimits }> {
  return { agents: await summaries({ owner: userId }), limits: await limitsFor(userId) }
}

/** Every published agent, best P&L first. Public: no viewer, no owner ids. */
export async function listPublished(): Promise<AgentSummary[]> {
  return summaries({ published: true })
}

/** What the viewer already has to do with the Marketplace: which published
 *  agents are their own, and which they have copied (original id → their
 *  copy's id), so a page offers "Open your copy" instead of a second copy. */
export async function viewerRelations(
  userId: string,
): Promise<{ mine: number[]; copies: Record<number, number> }> {
  const sql = getSql()
  const rows = await sql<{ id: number; copied_from: number | null; is_public: boolean }[]>`
    select id, copied_from, is_public
      from public.strategies
     where owner_id = ${userId} and source = 'lab' and retired_at is null
     order by id
  `
  const copies: Record<number, number> = {}
  for (const r of rows) {
    if (r.copied_from != null && copies[r.copied_from] == null) copies[r.copied_from] = r.id
  }
  return { mine: rows.filter((r) => r.is_public).map((r) => r.id), copies }
}

/** A maker agent's bids. Called only after the owner check in summaries(); an
 *  agent with no row in close_maker_orders gets an empty list, and so does a
 *  database the migration has not reached yet. */
async function ordersOf(id: number): Promise<AgentOrder[]> {
  const sql = getSql()
  try {
    return await sql<AgentOrder[]>`
      select o.id, o.created_at, o.kickoff, o.title, o.team_name as team, o.league, o.status,
             o.bid_price::float8 as bid_price,
             case o.side when 'home' then o.p_home when 'away' then o.p_away end::float8 as market_mid,
             o.model_q::float8 as model_q, o.close_mid::float8 as close_mid, o.payout::float8 as payout
        from public.close_maker_orders o
       where o.strategy_id = ${id}
       order by o.created_at desc, o.id desc
       limit ${TRADES_SHOWN}
    `
  } catch (err) {
    if ((err as { code?: string }).code === '42P01') return []      // relation does not exist
    throw err
  }
}

/** A drift agent's positions, newest first, and how many legacy (pre-db/076)
 *  positions were left out. Called for every agent; an agent with no
 *  drift_positions (every non-drift one) gets an empty list and 0, as does a
 *  database the migration has not reached yet. */
async function driftPositionsOf(id: number): Promise<{ positions: DriftPosition[]; legacy: number }> {
  const sql = getSql()
  try {
    const [positions, [{ legacy }]] = await Promise.all([
      sql<DriftPosition[]>`
        select id, entry_at, exit_at, kickoff_at as kickoff, home, away, outcome_key as outcome,
               entry_no_price::float8 as entry_no, exit_no_price::float8 as exit_no,
               target_no_price::float8 as target_no, status, exit_reason,
               case when status = 'closed' then realized_pnl_usd::float8 end as pl_units
          from public.drift_positions
         where strategy_id = ${id} and price_convention = 'executable'
         order by coalesce(exit_at, entry_at) desc, id desc
         limit ${TRADES_SHOWN}
      `,
      sql<{ legacy: number }[]>`
        select count(*)::int as legacy
          from public.drift_positions
         where strategy_id = ${id} and price_convention <> 'executable'
      `,
    ])
    return { positions, legacy }
  } catch (err) {
    const code = (err as { code?: string }).code
    if (code === '42P01' || code === '42703') return { positions: [], legacy: 0 }  // table / column absent
    throw err
  }
}

/** One agent with its latest trades and its whole curve, or null when it does
 *  not exist or is not this user's — the two are deliberately the same answer. */
export async function getAgent(userId: string, id: number): Promise<AgentDetail | null> {
  const [agent] = await summaries({ owner: userId }, id)
  return agent ? detailOf(agent) : null
}

/** One published agent, as the Marketplace shows it, or null when it does not
 *  exist, is private, or is archived — again one answer for all three. */
export async function getPublished(id: number): Promise<AgentDetail | null> {
  const [agent] = await summaries({ published: true }, id)
  return agent ? detailOf(agent) : null
}

/** The trades, bids, positions and curve of an agent whose scope was already
 *  checked by summaries(). Numerics are cast to float8 because postgres.js
 *  hands `numeric` back as a string, and arithmetic on "1.000" concatenates
 *  instead of adding. */
async function detailOf(agent: AgentSummary): Promise<AgentDetail> {
  const id = agent.id
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

  const { positions, legacy } = await driftPositionsOf(id)

  // A drift agent has no paper_trades; its curve is built from the closed
  // positions that counted (won or lost), oldest first — the same rows the
  // tiles and the spark count.
  if (positions.length || legacy) {
    const closed = positions
      .filter((p) => p.status === 'closed' && p.pl_units != null && p.pl_units !== 0)
      .reverse()
    const run = cumulative(closed.map((p) => p.pl_units as number))
    return {
      agent,
      orders: [],
      trades: [],
      positions,
      legacy_positions: legacy,
      curve: thin(closed.map((p, i) => ({ t: p.exit_at ?? p.entry_at, pl: run[i] })), CURVE_POINTS),
    }
  }

  const running = cumulative(settled.map((r) => r.pl))
  return {
    agent,
    orders: await ordersOf(id),
    trades: trades.map(({ ctx_home, ctx_away, entry_minute, goals_at_entry, target_line, pressure_index, ...t }) => ({
      ...t,
      context:
        entry_minute != null || ctx_home != null
          ? { home: ctx_home, away: ctx_away, entry_minute, goals_at_entry, target_line, pressure_index }
          : null,
    })),
    positions: [],
    legacy_positions: 0,
    curve: thin(settled.map((r, i) => ({ t: r.t, pl: running[i] })), CURVE_POINTS),
  }
}

// ── writes ──────────────────────────────────────────────────────────────────

type Result<T = object> = ({ ok: true } & T) | { ok: false; status: number; error: string }

/** Did this write fail on the strategies.name UNIQUE constraint? */
function nameTaken(err: unknown): boolean {
  const e = err as { code?: string; constraint_name?: string }
  return e?.code === '23505' && e.constraint_name === 'strategies_name_key'
}

/** The first free name at or after `base`: "<base>", then "<base> (2)", "(3)"…
 *
 *  ⚠️ `strategies.name` is UNIQUE across every account and must stay so: the
 *     hand-written agents find their own row by name (nfl_agent.py,
 *     pressure_agent.py, nba_agent.py…), and a user's agent sharing one of
 *     those names would have its trades booked under the wrong row. Two users
 *     with the same idea, or a copy of a published agent, therefore get a
 *     numbered name — which the owner can rename. */
async function freeName(base: string): Promise<string> {
  const sql = getSql()
  const root = (base.trim() || 'My agent').slice(0, 84)
  const like = `${root.replace(/[\\%_]/g, '\\$&')} (%)`
  const rows = await sql<{ name: string }[]>`
    select name from public.strategies where name = ${root} or name like ${like}
  `
  const taken = new Set(rows.map((r) => r.name))
  if (!taken.has(root)) return root
  for (let n = 2; n < 100_000; n++) {
    if (!taken.has(`${root} (${n})`)) return `${root} (${n})`
  }
  return `${root} (${Date.now().toString(36)})`
}

/** Insert under a free name, again if another request took it in between. */
async function insertNamed(base: string, insert: (name: string) => Promise<{ id: number }>): Promise<{ id: number }> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await insert(await freeName(base))
    } catch (err) {
      if (!nameTaken(err) || attempt >= 2) throw err
    }
  }
}

/** What deploying a saved agent did. Saving never fails because the run did:
 *  an agent that cannot run yet is kept as a draft, with the reason. */
export interface Deployed {
  id: number
  running: boolean
  /** Why it is not running, when it was asked to and is not. */
  note: string | null
}

/** Switch a just-saved agent on, and say plainly if it could not be. */
async function deploy(userId: string, id: number): Promise<Deployed> {
  const r = await updateAgent(userId, id, { run_status: 'running' })
  return { id, running: r.ok, note: r.ok ? null : r.error }
}

export async function saveLabAgent(
  userId: string,
  input: { hypothesis: string; interpretation: string; spec: unknown; deploy?: boolean },
): Promise<Result<Deployed>> {
  // A live rule has no history to replay: it is checked, not backtested.
  let spec: Spec | InplaySpec | null
  if (isInplaySpec(input.spec)) {
    const c = cleanInplay(input.spec, LEAGUE_CODES)
    if (!c.spec) return { ok: false, status: 400, error: c.error ?? 'That is not a rule Create produced.' }
    spec = c.spec
  } else {
    spec = cleanSpec(input.spec)
  }
  if (!spec) return { ok: false, status: 400, error: 'That is not a strategy Create produced.' }

  const limits = await limitsFor(userId)
  if (!limits.unlimited && limits.used_saved >= (limits.saved ?? 0)) {
    return {
      ok: false,
      status: 402,
      error: `Your plan keeps ${limits.saved} agents. Archive one, or upgrade to keep more.`,
    }
  }

  const sql = getSql()
  let backtest: SavedBacktest | null = null
  if (!isInplaySpec(spec)) {
    const stats = computeStats(await rawBacktest(spec))
    backtest = { stats, verdict: verdict(stats, spec.market), ran_at: new Date().toISOString() }
  }

  const theory = input.hypothesis.trim().slice(0, 500)
  const interpretation = input.interpretation.trim().slice(0, 500)
  const row = await insertNamed(theory || interpretation, async (name) => {
    const [r] = await sql<{ id: number }[]>`
      insert into public.strategies
        (name, source, owner_id, theory, interpretation, spec, backtest,
         run_status, run_blocker, promoted_at, rules)
      values
        (${name}, 'lab', ${userId}, ${theory}, ${interpretation},
         ${sql.json(spec as never)}, ${backtest ? sql.json(backtest as never) : null},
         'draft', ${runBlocker(spec)}, now(),
         ${sql.json({ kind: 'lab', live: isInplaySpec(spec) } as never)})
      returning id
    `
    return r
  })
  if (input.deploy) return { ok: true, ...(await deploy(userId, row.id)) }
  return { ok: true, id: row.id, running: false, note: null }
}

/** Copy a published agent into this user's account, and deploy it if asked.
 *
 *  The copy takes the original's rule, its words and its stored backtest —
 *  every one of them written by this server — and nothing of its record. One
 *  live copy per original per user: asking again returns the copy they have. */
export async function copyAgent(
  userId: string,
  sourceId: number,
  opts: { deploy: boolean },
): Promise<Result<Deployed & { existing: boolean }>> {
  const sql = getSql()
  const [src] = await sql<{
    id: number; name: string; owner_id: string | null; theory: string | null
    interpretation: string | null; spec: Spec | InplaySpec | null; backtest: SavedBacktest | null
    rules: Record<string, unknown> | null
  }[]>`
    select id, name, owner_id, theory, interpretation, spec, backtest, rules
      from public.strategies
     where id = ${sourceId} and is_public and source = 'lab' and retired_at is null
  `
  if (!src || !src.spec) return { ok: false, status: 404, error: 'That agent is not on the Marketplace.' }
  if (src.owner_id === userId) return { ok: false, status: 409, error: 'This agent is already yours.' }

  const [have] = await sql<{ id: number; run_status: RunStatus }[]>`
    select id, run_status
      from public.strategies
     where owner_id = ${userId} and copied_from = ${sourceId} and retired_at is null
     order by id
     limit 1
  `
  if (have) return { ok: true, id: have.id, running: have.run_status === 'running', note: null, existing: true }

  const limits = await limitsFor(userId)
  if (!limits.unlimited && limits.used_saved >= (limits.saved ?? 0)) {
    return {
      ok: false,
      status: 402,
      error: `Your plan keeps ${limits.saved} agents. Archive one, or upgrade to keep more.`,
    }
  }

  const spec = src.spec
  const rules = { ...(src.rules ?? {}), kind: 'lab', live: isInplaySpec(spec) }
  const blocker = runBlocker(spec)
  // The original keeps its name; the copy takes the first free "<name> (n)".
  const row = await insertNamed(src.name, async (name) => {
    const [r] = await sql<{ id: number }[]>`
      insert into public.strategies
        (name, source, owner_id, theory, interpretation, spec, backtest,
         run_status, run_blocker, promoted_at, rules, copied_from)
      values
        (${name}, 'lab', ${userId}, ${src.theory}, ${src.interpretation},
         ${sql.json(spec as never)}, ${src.backtest ? sql.json(src.backtest as never) : null},
         'draft', ${blocker}, now(), ${sql.json(rules as never)}, ${src.id})
      returning id
    `
    return r
  })
  const d = opts.deploy ? await deploy(userId, row.id) : { id: row.id, running: false, note: null }
  return { ok: true, ...d, existing: false }
}

export async function updateAgent(
  userId: string,
  id: number,
  patch: { run_status?: RunStatus; is_public?: boolean; name?: string },
): Promise<Result> {
  const sql = getSql()
  const [s] = await sql<{ source: string; spec: Spec | InplaySpec | null; run_status: RunStatus; copied_from: number | null }[]>`
    select source, spec, run_status, copied_from
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

  // The Marketplace credits whoever had the idea. A copy is the same rule
  // under a second name, so it stays private however well it does.
  if (patch.is_public === true && s.copied_from != null) {
    return {
      ok: false,
      status: 409,
      error: 'A copy cannot be published — the original is already on the Marketplace. Publish the agents you built.',
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
  try {
    await sql`
      update public.strategies
         set run_status = coalesce(${patch.run_status ?? null}::text, run_status),
             is_public  = coalesce(${patch.is_public ?? null}::boolean, is_public),
             -- The first publish dates the listing; a republish keeps that date.
             published_at = case when ${patch.is_public ?? null}::boolean
                                 then coalesce(published_at, now()) else published_at end,
             name       = coalesce(${name}::text, name),
             run_blocker = case when ${patch.run_status ?? null}::text = 'running'
                                then null else run_blocker end
       where id = ${id} and owner_id = ${userId}
    `
  } catch (err) {
    // Names are unique across every account (see freeName).
    if (nameTaken(err)) {
      return { ok: false, status: 409, error: 'Another agent already has that name — try a different one.' }
    }
    throw err
  }
  return { ok: true }
}

// ── the name an author publishes under ───────────────────────────────────────

const PUBLIC_NAME = /^[\p{L}\p{N}][\p{L}\p{N} ._'-]{1,31}$/u

/** The name this user's published agents carry, and the one they would carry
 *  if they set none — the same expression the Marketplace reads (AUTHOR_SQL). */
export async function publicNameOf(userId: string): Promise<{ public_name: string | null; shown_as: string }> {
  const sql = getSql()
  const [r] = await sql<{ public_name: string | null; shown_as: string }[]>`
    select pr.public_name, ${sql.unsafe(AUTHOR_SQL)} as shown_as
      from public.profiles pr
      cross join (select ${userId}::uuid as owner_id) s
     where pr.id = ${userId}
  `
  return r ?? { public_name: null, shown_as: 'A NOPREDICTIONS user' }
}

/** Set or clear the public name. "NOPREDICTIONS" is the operator's: a user
 *  publishing under it would be passing their agents off as the house's. */
export async function setPublicName(userId: string, raw: string | null): Promise<Result<{ public_name: string | null; shown_as: string }>> {
  const name = (raw ?? '').trim().replace(/\s+/g, ' ')
  if (name) {
    if (!PUBLIC_NAME.test(name)) {
      return { ok: false, status: 400, error: '2 to 32 characters: letters, numbers, spaces, dots, dashes or apostrophes.' }
    }
    const { role } = await accountOf(userId)
    if (role !== 'owner' && /no\s*predictions/i.test(name)) {
      return { ok: false, status: 400, error: 'That name is reserved.' }
    }
  }
  const sql = getSql()
  await sql`update public.profiles set public_name = ${name || null} where id = ${userId}`
  return { ok: true, ...(await publicNameOf(userId)) }
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
