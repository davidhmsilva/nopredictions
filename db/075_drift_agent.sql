-- Register the Pre-Match Drift agent so its owner's Agents tab shows it.
--
-- db/020 tried to register this strategy with INSERT (name, description, active),
-- but the strategies schema has since dropped those columns (db/049 reshaped it
-- around owner_id/source/rules), so that insert never landed: there is no
-- 'Pre-Match Drift' row and all drift_positions.strategy_id are NULL.
--
-- The site lists agents from `strategies` owner-checked (lib/agents.ts); a drift
-- agent needs (1) a hypothesis it belongs to (every source<>'lab' strategy must
-- carry hypothesis_id — strategies_hypothesis_unless_lab), (2) a row owned by
-- the operator, marked rules.kind='drift' so the reader knows to pull its record
-- from drift_positions rather than paper_trades, and (3) its positions attached
-- by strategy_id.
--
-- Owner is the operator account, the same owner every source='agent' strategy
-- already carries (db/050).

insert into research_hypotheses (title, description, rationale, source, status, created_by)
select 'H-PM-DRIFT — Polymarket overprices longshots pre-match; the price drifts toward fair before kick-off',
       'Buy the NO side of a DC-model longshot 1.5–48h before kick-off and exit before the match starts, capturing the pre-match price drift. Paper only (drift_scanner.py / drift_monitor.py, db/020).',
       'PM''s YES price on a longshot drifts DOWN in the hours before kick-off, so NO rises; enter NO early, take +10% or force-close before kick-off, stop at -5%.',
       'user', 'testing', 'claude'
where not exists (select 1 from research_hypotheses where title like 'H-PM-DRIFT%');

insert into strategies (name, source, owner_id, hypothesis_id, theory, interpretation,
                        run_status, is_public, rules, created_at, promoted_at)
select
  'Pre-Match Drift',
  'agent',
  '0c2efa25-6a44-4b71-b63a-803ef80098e1',
  (select id from research_hypotheses where title like 'H-PM-DRIFT%' limit 1),
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
