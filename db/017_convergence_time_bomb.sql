-- 017_convergence_time_bomb.sql
-- Extend the convergence shadow ledger to support backtesting multiple exit rules
-- and to record whether each entry was in a "time bomb" zone (fast-movement odds
-- band where price compression is faster, per the time-bomb trading concept).
--
-- Four exit rules tracked per entry:
--   1. CONVERGED  — current rule: bid approaches fair (EXIT_BUFFER_PP)
--   2. HOLD       — let it resolve (settle_result / settle_pnl_usd, already present)
--   3. AT_FAIR    — first time bid >= entry_fair (model repriced to our entry fair)
--   4. AT_TARGET  — first time bid >= entry * (1 + target_pct), e.g. +20%
--   5. TB_EXIT    — entered in a fast zone; exit when bid crosses out of that zone
--
-- Time bomb zones (PM probability = yes price):
--   fast_1:  0.45 – 0.56   (Betfair odds ≈ 1.79–2.22)
--   fast_2:  0.33 – 0.40   (Betfair odds ≈ 2.50–3.03)
-- The "exit" of each zone is the upper boundary:
--   fast_1 → exit when bid > 0.56 (enters medium zone 0.56–0.67)
--   fast_2 → exit when bid > 0.40 (enters slow zone 0.40–0.45)

ALTER TABLE convergence_shadow
    -- entry classification
    ADD COLUMN IF NOT EXISTS in_time_bomb       BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS time_bomb_zone     TEXT,       -- 'fast_1' | 'fast_2' | NULL

    -- exit rule 3: at model fair
    ADD COLUMN IF NOT EXISTS exit_at_fair_price  NUMERIC,   -- bid when bid >= entry_fair (first time)
    ADD COLUMN IF NOT EXISTS exit_at_fair_minute INT,

    -- exit rule 4: at target % gain
    ADD COLUMN IF NOT EXISTS exit_at_target_price  NUMERIC, -- bid when bid >= entry * (1+pct)
    ADD COLUMN IF NOT EXISTS exit_at_target_pct    NUMERIC, -- the pct used, e.g. 0.20
    ADD COLUMN IF NOT EXISTS exit_at_target_minute INT,

    -- exit rule 5: ride the time bomb wave out
    ADD COLUMN IF NOT EXISTS exit_tb_out_price  NUMERIC,    -- bid when bid exits the fast zone
    ADD COLUMN IF NOT EXISTS exit_tb_out_minute INT;

-- Track whether each path snapshot is inside a fast zone (helps visualise the wave)
ALTER TABLE convergence_path
    ADD COLUMN IF NOT EXISTS in_fast_zone BOOLEAN;
