-- 079_payout_guard_partial_exits.sql
-- The db/025 guard let 'lost' carry exactly one payout: 0. That holds for a
-- token held to settlement and is wrong for a position SOLD before it.
--
-- Favourite Swing (H-FAV-SWING, db/061) sells 5 minutes after any goal. When
-- the underdog scores it sells at a loss, and fav_swing_agent.close() writes
-- result = 'lost' with the sale's gross proceeds (e.g. 0.6519 for 1u). The
-- trigger coerced every one of those to 0, so the site booked a −1u loss where
-- the agent had lost 0.35-0.57u. Measured 2026-10-25 on 26 settled positions:
-- 3 trades (pt#6374, #6556, #6617), 1.59u understated, −2.73u shown against
-- −1.14u in fav_swing_positions.
--
-- What the guard exists to catch is the stale resolver's NET loss, which is
-- −stake: negative. A payout in [0, stake) is a sale below cost and is kept.
-- A payout at or above stake on 'lost' is inconsistent and still coerced to 0,
-- as before.
--
-- ⚠️ Not changed, recorded: the 'won' repair rewrites a payout within 0.011 of
-- stake*(odds-1) to stake*odds. A position SOLD at a profit whose proceeds
-- happen to land there would be inflated to a full win. It cannot fire on
-- Favourite Swing (odds 1.3-1.75 put stake*(odds-1) below stake, and 'won'
-- needs payout > stake), but it can on any early-exit strategy at odds above 2.

CREATE OR REPLACE FUNCTION enforce_payout_units_gross()
RETURNS TRIGGER AS $$
DECLARE
    gross_payout NUMERIC;
    net_payout   NUMERIC;
BEGIN
    IF NEW.result IS NULL OR NEW.payout_units IS NULL THEN
        RETURN NEW;   -- still open, or nothing to check
    END IF;

    -- 'lost' under GROSS: 0 when held to settlement, the sale's proceeds when
    -- sold before it, so anything in [0, stake). A negative value is the old
    -- NET formula (−stake); a value at or above stake cannot be a loss.
    IF NEW.result = 'lost'
       AND (NEW.payout_units < 0
            OR NEW.payout_units >= COALESCE(NEW.stake_units, 0)) THEN
        IF NEW.payout_units <> 0 THEN
            RAISE WARNING
                'payout_units GROSS guard: trade % result=lost had payout_units=% (NET style) -> coerced to 0',
                NEW.id, NEW.payout_units;
        END IF;
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

-- Repair: the Favourite Swing trades sold at a loss take the proceeds the agent
-- recorded on its own position row. Idempotent; touches only rows still at 0.
UPDATE paper_trades pt
   SET payout_units = p.payout_units
  FROM fav_swing_positions p
 WHERE p.paper_trade_id = pt.id
   AND p.exit_reason LIKE 'sold_after_goal%'
   AND pt.result = 'lost'
   AND pt.payout_units = 0
   AND p.payout_units > 0
   AND p.payout_units < pt.stake_units;
