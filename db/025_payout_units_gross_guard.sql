-- 025_payout_units_gross_guard.sql
-- Enforce the payout_units GROSS convention at the database level.
--
-- Convention (project-wide):
--   result = 'lost' -> payout_units = 0
--   result = 'won'  -> payout_units = stake_units * entry_odds
--   result = 'void' -> payout_units = stake_units   (refund)
-- P&L is always derived as (payout_units - stake_units).
--
-- WHY A DB-LEVEL GUARD, NOT JUST A CODE FIX:
-- agent/resolver.py was fixed on 2026-05-17 (commit 4ceb036) to write GROSS
-- payouts, yet NET-style rows appeared again between 2026-05-29 and 2026-06-19.
-- Root cause: several stale git worktrees under .claude/worktrees/ still carry
-- the pre-4ceb036 resolver, which computes
--       won  -> stake * (entry_odds - 1)      (net profit)
--       lost -> -stake                        (net loss)
-- Every checkout shares the same Supabase DATABASE_URL, so running the resolver
-- from ANY stale worktree settles open trades with the old formula. Fixing the
-- resolver in the main checkout cannot prevent that -- only a constraint that
-- every writer must pass through can. Two separate batches of corruption
-- (2026-05-27 and 2026-07-20) confirm code-only fixes do not hold.
--
-- Behaviour: this trigger COERCES rather than rejects, so a stale resolver still
-- completes its run instead of crashing mid-settlement, but cannot persist a
-- NET-style payout. Each coercion emits a WARNING so it is visible in logs.

CREATE OR REPLACE FUNCTION enforce_payout_units_gross()
RETURNS TRIGGER AS $$
DECLARE
    gross_payout NUMERIC;
    net_payout   NUMERIC;
BEGIN
    IF NEW.result IS NULL OR NEW.payout_units IS NULL THEN
        RETURN NEW;   -- still open, or nothing to check
    END IF;

    -- 'lost' has exactly one valid value under GROSS: 0. Hard invariant.
    IF NEW.result = 'lost' AND NEW.payout_units <> 0 THEN
        RAISE WARNING
            'payout_units GROSS guard: trade % result=lost had payout_units=% (NET style) -> coerced to 0',
            NEW.id, NEW.payout_units;
        NEW.payout_units := 0;
        RETURN NEW;
    END IF;

    -- 'won' is repaired ONLY when the value carries the exact signature of the
    -- old NET formula, stake*(entry_odds-1). Deliberately narrow: we do not
    -- blanket-rewrite winning payouts, we only undo the known bug. For any one
    -- row stake*(odds-1) and stake*odds differ by exactly `stake`, so this test
    -- cannot misfire on an already-correct GROSS row.
    IF NEW.result = 'won'
       AND NEW.entry_odds IS NOT NULL AND NEW.entry_odds > 1.05
       AND NEW.stake_units IS NOT NULL THEN

        gross_payout := ROUND(NEW.stake_units * NEW.entry_odds, 4);
        net_payout   := ROUND(NEW.stake_units * (NEW.entry_odds - 1), 4);

        IF ABS(NEW.payout_units - net_payout) < 0.011
           AND ABS(NEW.payout_units - gross_payout) >= 0.011 THEN
            RAISE WARNING
                'payout_units GROSS guard: trade % result=won had payout_units=% (NET style) -> coerced to %',
                NEW.id, NEW.payout_units, gross_payout;
            NEW.payout_units := gross_payout;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_paper_trades_payout_gross ON paper_trades;

CREATE TRIGGER trg_paper_trades_payout_gross
    BEFORE INSERT OR UPDATE OF result, payout_units ON paper_trades
    FOR EACH ROW
    EXECUTE FUNCTION enforce_payout_units_gross();
