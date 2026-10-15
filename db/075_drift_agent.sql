-- Register the Pre-Match Drift agent so its owner's Agents tab shows it.
--
-- db/020 tried to register this strategy with INSERT (name, description, active),
-- but the strategies schema has since dropped those columns (db/049 reshaped it
-- around owner_id/source/rules), so that insert never landed: there is no
-- 'Pre-Match Drift' row and all drift_positions.strategy_id are NULL.
--
-- The site lists agents from `strategies` owner-checked (lib/agents.ts); a drift
-- agent needs (1) a row owned by the operator, marked rules.kind='drift' so the
-- reader knows to pull its record from drift_positions rather than paper_trades,
-- and (2) its positions attached by strategy_id.
--
-- Owner is the operator account, the same owner every source='agent' strategy
-- already carries (db/050).

insert into strategies (name, source, owner_id, theory, interpretation,
                        run_status, is_public, rules, created_at, promoted_at)
select
  'Pre-Match Drift',
  'agent',
  '0c2efa25-6a44-4b71-b63a-803ef80098e1',
  'Polymarket overprices longshots in the hours before kick-off; the price drifts toward fair as the game nears.',
  'Buy NO on a DC-model longshot 1.5–48h out; take profit at +10% on the NO price, or force-close before kick-off.',
  'running',
  false,
  '{"kind":"drift"}'::jsonb,
  now(), now()
where not exists (select 1 from strategies where rules->>'kind' = 'drift');

update drift_positions
   set strategy_id = (select id from strategies where rules->>'kind' = 'drift' limit 1),
       updated_at  = now()
 where strategy_id is null;
