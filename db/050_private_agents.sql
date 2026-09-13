-- 050 — strategies and paper trades stop being public
--
-- Until 2026-09-11 the site's anon key read every strategy and every paper
-- trade, and /agent/ours rendered them as a public record. The product changed
-- shape: an agent belongs to a user and is shown to that user only, over
-- /api/agents on DATABASE_URL with the owner check in the SQL (site/app/lib/
-- agents.ts). The operator's pressure arms included — his agents, not an
-- exhibit.
--
-- ⚠️ Apply AFTER the site that reads through /api/agents is deployed. In the
--    other order the old /agent/ours page loses its data in production.
--
-- Views are revoked by name, not left to the tables' RLS: a view runs as its
-- owner, so a GRANT SELECT on a view reads straight past the policies of the
-- table beneath it (db/048).

begin;
set local lock_timeout = '5s';

drop policy if exists "Public read" on public.paper_trades;
drop policy if exists "Public read" on public.strategies;

revoke select on
  public.paper_trades,
  public.strategies,
  public.v_strategy_performance,
  public.v_pressure_trades,
  public.v_ht_pressure_trades,
  public.v_fav_ht_trades,
  public.v_open_paper_trades,
  public.v_settled_paper_trades
from anon, authenticated;

commit;
