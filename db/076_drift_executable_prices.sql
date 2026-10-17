-- db/076 — Pre-Match Drift books its paper trades on the side of the book that
-- would actually fill, and the old record is flagged rather than shown.
--
-- What was wrong (audit 2026-10-16, reports/drift_accounting_fix_2026-10-16.md):
-- drift_scanner bought NO at 1 − Gamma's YES ask, which is the NO BID, and
-- drift_monitor sold at 1 − CLOB /price?side=BUY, which returns the YES BID, so
-- the exit was the NO ASK. Every position bought at the bid and sold at the ask,
-- so it banked the whole spread on the way in and out. On thin draw books that
-- is 31-39c, and a +10% "target" fired on the first monitor cycle with no price
-- move at all. Buying NO costs the NO ask (= 1 − YES bid); selling it receives
-- the NO bid (= 1 − YES ask).
--
-- Every row written before this migration, and any row the old code writes
-- until the server pulls the fix, carries the DEFAULT 'yes_side_inverted'. New
-- code writes 'executable' explicitly, so a row can only claim real execution
-- if the code that wrote it priced it that way. Nothing is deleted.
--
-- entry_yes_price keeps its old meaning: the YES price the SIGNAL read (Gamma's
-- YES ask, compared with the DC fair for edge_pp). entry_no_price is now the
-- price PAID for the NO shares, and exit_no_price the price RECEIVED.

ALTER TABLE drift_positions
    ADD COLUMN IF NOT EXISTS price_convention   TEXT NOT NULL DEFAULT 'yes_side_inverted',
    ADD COLUMN IF NOT EXISTS no_token_id        TEXT,
    -- the CLOB book at entry, both tokens (top of book)
    ADD COLUMN IF NOT EXISTS entry_yes_bid      NUMERIC,
    ADD COLUMN IF NOT EXISTS entry_yes_ask      NUMERIC,
    ADD COLUMN IF NOT EXISTS entry_no_bid       NUMERIC,
    ADD COLUMN IF NOT EXISTS entry_no_ask       NUMERIC,
    ADD COLUMN IF NOT EXISTS entry_no_ask_size  NUMERIC,   -- shares resting at the best NO ask
    ADD COLUMN IF NOT EXISTS entry_no_depth_usd NUMERIC,   -- USD resting at the best NO ask
    -- the CLOB book at exit
    ADD COLUMN IF NOT EXISTS exit_no_bid        NUMERIC,
    ADD COLUMN IF NOT EXISTS exit_no_ask        NUMERIC,
    ADD COLUMN IF NOT EXISTS exit_no_bid_size   NUMERIC;   -- shares resting at the best NO bid

COMMENT ON COLUMN drift_positions.price_convention IS
  'executable = bought at the NO ask, sold at the NO bid (db/076). yes_side_inverted = '
  'booked before db/076: bought at the NO bid, sold at the NO ask, so the P&L includes '
  'the round-trip spread as profit. Never read a result off yes_side_inverted rows.';

-- One executable position per token, ever. The old guard (db/021) only held
-- while a position was OPEN, so a token re-entered on every 10-minute scan once
-- the previous position closed (32 entries per token in October 2026). The old
-- rows hold those duplicates, so the index covers executable rows only; the
-- scanner also refuses any token that already appears in the table.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_drift_executable_per_token
    ON drift_positions (token_id)
    WHERE price_convention = 'executable';
