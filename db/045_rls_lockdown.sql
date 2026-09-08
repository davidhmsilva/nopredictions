-- 045 — close the anon key's write access to the research tables
--
-- The anon key is published in the site's JavaScript; that is what an anon key
-- is for. What it must never carry is write access to data that cannot be
-- recollected. Measured 2026-09-08:
--
--   grantee anon on public.pressure_observations
--     -> DELETE, INSERT, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE
--   and RLS off, so nothing stood between that grant and 761,088 rows.
--
-- 🔑 The rows this protects are the ones the project cannot buy back. The
--    pressure model can only ever be fitted on data recorded FORWARD — a
--    truncate is not an outage, it is the loss of every fixture-minute since
--    2026-07-22 and the restart of a verdict gate that is already months away.
--
-- Safe by inspection, and the inspection is the point:
--   · The site reads exactly six things with the anon key — leagues,
--     match_odds, matches, paper_trades, strategies, v_strategy_performance —
--     and every one of them already had RLS on. None is touched here.
--   · The Lab reads bt_features / bt_nba over DATABASE_URL (app/lib/db.ts),
--     a direct Postgres connection, which bypasses RLS entirely.
--   · The Python agents connect the same way (agent/tools/db.py).
--
-- So: RLS on, and deliberately NO policies. Not "read-only for anon" — there
-- is no anon reader of these tables at all, and a policy written for a caller
-- that does not exist is a policy nobody will maintain.

begin;

alter table public.club_elo                     enable row level security;
alter table public.goal_events                  enable row level security;
alter table public.market_observations          enable row level security;
alter table public.convergence_shadow           enable row level security;
alter table public.convergence_path             enable row level security;
alter table public.drift_positions              enable row level security;
alter table public.wc_agent_state               enable row level security;
alter table public.bt_features                  enable row level security;
alter table public.bt_nba                       enable row level security;
alter table public.nba_odds                     enable row level security;
alter table public.pm_ticks                     enable row level security;
alter table public.live_fixture_ticks           enable row level security;
alter table public.pm_resolutions               enable row level security;
alter table public.late_goal_observations       enable row level security;
alter table public.pressure_observations        enable row level security;
alter table public.ht_pressure_observations     enable row level security;
alter table public.fav_ht_observations          enable row level security;
alter table public.settled_market_observations  enable row level security;

commit;
