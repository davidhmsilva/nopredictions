-- 078_fav_swing_ticks_control.sql — Favourite Swing: instrumentation only (2026-10-20).
--
-- The rule (db/061, obs_version 2) does not change. Two measurements are added,
-- by the operator's decision, before any change to entry or exit is considered:
--
--   1. fav_swing_ticks — the book of the token held, read on EVERY poll while a
--      position is open. Before this the price was read only at entry and at the
--      sale, so "sell at minute X if still level" or "sell the moment the goal is
--      in" could not be replayed on our own positions. One row per position per
--      poll; a one-sided book is a row with the empty side NULL, because the
--      book pulling after a goal is itself the thing to measure.
--
--   2. fav_swing_positions.arm — 'pressure' is the rule as specified. 'control'
--      is the same rule WITHOUT the pressure gate: the same 1.30-1.50 kick-off
--      band, level score, minute window, book gates, goal clock and exit, bought
--      at the first poll that clears them. It is a shadow: no paper_trade, never
--      shown as a bet. Its pressure reading is still recorded when the feed has
--      stats (NULL when not), so "does the gate select better fixtures than
--      buying every level band favourite" is answerable from the rows, and the
--      gate can be re-applied to control rows at any threshold.
--
-- Every row before this migration is 'pressure'. Never pool the two arms.

ALTER TABLE fav_swing_positions
    ADD COLUMN IF NOT EXISTS arm TEXT NOT NULL DEFAULT 'pressure';

DO $$ BEGIN
    ALTER TABLE fav_swing_positions
        ADD CONSTRAINT fav_swing_positions_arm_check CHECK (arm IN ('pressure', 'control'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN fav_swing_positions.arm IS
  '''pressure'' = the H-FAV-SWING rule (paper trade). ''control'' = the same rule without the pressure gate (shadow, no paper trade, db/078).';

CREATE INDEX IF NOT EXISTS idx_fav_swing_arm ON fav_swing_positions (arm, status);

CREATE TABLE IF NOT EXISTS fav_swing_ticks (
    id              BIGSERIAL PRIMARY KEY,
    position_id     BIGINT      NOT NULL REFERENCES fav_swing_positions(id),
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    minute          INTEGER,                    -- NULL: the fixture was not in this poll (half time, feed gap)
    home_goals      INTEGER,
    away_goals      INTEGER,
    status          TEXT        NOT NULL,       -- the position's status AFTER this poll's step
    best_bid        NUMERIC(6,4),               -- NULL: no bids on the book
    best_ask        NUMERIC(6,4),               -- NULL: no asks on the book
    bid_depth_usd   NUMERIC(14,2),              -- top 5 levels, as late_goals_observer._fetch_book
    ask_depth_usd   NUMERIC(14,2)
);

CREATE INDEX IF NOT EXISTS idx_fav_swing_ticks_pos ON fav_swing_ticks (position_id, observed_at);

ALTER TABLE fav_swing_ticks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE fav_swing_ticks FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON TABLE fav_swing_ticks FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
DO $$ BEGIN
    REVOKE ALL ON SEQUENCE fav_swing_ticks_id_seq FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
