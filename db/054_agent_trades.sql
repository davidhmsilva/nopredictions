-- 054 — which paper trades belong to which agent, in one place
--
-- /agent now shows every agent the same way: a record, a curve and its
-- trades. The totals come from v_strategy_performance, and two of its rows are
-- not "the trades with this strategy_id":
--
--   9  Live Polymarket         every trade that went out with real money
--                              (pm_live), whichever strategy placed it
--   19 Live Pressure Overs v2  a READING of strategy 16: its entries at
--                              75-84' priced 2.00-3.00, since 2026-09-05
--
-- A trade list or an equity curve built on strategy_id alone would disagree
-- with the totals printed above it on exactly those two agents. This view is
-- the membership rule the totals use, so the site can join on it and never
-- restate the rule. ⚠️ It mirrors v_strategy_performance: change one, change
-- both. The check at the bottom of this file must return no rows.
--
-- Also: pressure_observations had no index on paper_trade_id, so the v2 branch
-- read all ~430 MB of it on every query — v_strategy_performance took 6-10 s,
-- and /agent paid that on every load. A partial index covers the ~500 rows that
-- carry a trade id. CONCURRENTLY, so the daemons writing that table are never
-- blocked; it cannot run inside a transaction, hence outside the one below.

create index concurrently if not exists pressure_observations_paper_trade_id_idx
  on public.pressure_observations (paper_trade_id)
  where paper_trade_id is not null;

begin;
set local lock_timeout = '5s';

create or replace view public.v_agent_trades as
  select pt.strategy_id, pt.id as paper_trade_id
    from public.paper_trades pt
   where pt.strategy_id <> all (array[9, 19])
  union all
  select 9, pt.id
    from public.paper_trades pt
   where pt.pm_live = true
  union all
  select 19, pt.id
    from public.paper_trades pt
    join public.pressure_observations po on po.paper_trade_id = pt.id
   where pt.strategy_id = 16
     and pt.placed_at >= '2026-09-05 17:50:00+00'
     and po.minute between 75 and 84
     and pt.entry_odds >= 2.00 and pt.entry_odds < 3.00;

comment on view public.v_agent_trades is
  'Which paper trades each agent owns, by the same rules as v_strategy_performance (id 9 = every pm_live trade, id 19 = a slice of 16). Read by site/app/lib/agents.ts over DATABASE_URL.';

-- Server-only, like everything under /api/agents (db/048, db/050).
revoke all on public.v_agent_trades from anon, authenticated;

commit;

-- Check (read-only): membership counts must equal the performance view's.
--   select p.strategy_id, p.n_trades, count(m.paper_trade_id)
--     from public.v_strategy_performance p
--     left join public.v_agent_trades m on m.strategy_id = p.strategy_id
--    group by 1, 2 having p.n_trades <> count(m.paper_trade_id);
