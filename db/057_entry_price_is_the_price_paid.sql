-- 057 — record the price the trade was actually booked at
--
-- WHAT WAS WRONG
-- --------------
-- db/055 let s16 and s17 choose a venue, but every column that held a PRICE
-- still held Polymarket's. `best_bid` / `best_ask` are Polymarket's book by
-- definition and should stay that way — the whole history reads them that way
-- — so a row with venue = 'kalshi' recorded where the trade went and nowhere
-- recorded what it cost.
--
-- Worse, `open_trades` booked `paper_trades.entry_price = best_ask`. A Kalshi
-- entry would have settled against a Polymarket price: the yield of a venue
-- it never traded at.
--
-- Caught before any row entered, on the first cycle after the restart.
--
-- WHAT THESE COLUMNS MEAN
-- -----------------------
--   best_bid / best_ask   Polymarket's book. Unchanged, on every row, always.
--   entry_ask             the price the paper trade was booked at — equal to
--                         best_ask when venue = 'polymarket', Kalshi's ask
--                         when it won.
--   entry_ticker          Kalshi's market ticker when it won, so the fill is
--                         checkable against the exchange. NULL on Polymarket,
--                         where `token_id` already names the market.
--
-- 🔑 The rule this restates: a paper trade must record the price somebody
--    could have paid, at the venue they would have paid it. Booking fills at
--    prices nobody was quoting is what turned the convergence trader's +141%
--    paper into +3.4% real (db/028), and it is the same error one venue over.

BEGIN;

ALTER TABLE pressure_observations
  ADD COLUMN IF NOT EXISTS entry_ask     numeric,
  ADD COLUMN IF NOT EXISTS entry_ticker  text;

ALTER TABLE ht_pressure_observations
  ADD COLUMN IF NOT EXISTS entry_ask     numeric,
  ADD COLUMN IF NOT EXISTS entry_ticker  text;

-- Every row written before the venue choice existed was booked on Polymarket
-- at its own ask, so the two are the same number there. Backfilled rather than
-- left NULL, because "the price paid" is a question the whole table should be
-- able to answer, not only the rows written after today.
UPDATE pressure_observations
   SET entry_ask = best_ask
 WHERE entry_ask IS NULL AND best_ask IS NOT NULL;

UPDATE ht_pressure_observations
   SET entry_ask = best_ask
 WHERE entry_ask IS NULL AND best_ask IS NOT NULL;

COMMENT ON COLUMN pressure_observations.entry_ask IS
  'The price the paper trade was booked at, at the venue named by `venue`. '
  'Equal to best_ask on every row before obs_version 6, and on every later row '
  'that stayed on Polymarket.';
COMMENT ON COLUMN pressure_observations.entry_ticker IS
  'Kalshi''s market ticker where Kalshi won the price, so the fill is checkable '
  'against the exchange. NULL on Polymarket, where token_id names the market.';

COMMENT ON COLUMN ht_pressure_observations.entry_ask IS
  'The price the paper trade was booked at, at the venue named by `venue`.';
COMMENT ON COLUMN ht_pressure_observations.entry_ticker IS
  'Kalshi''s market ticker where Kalshi won the price.';

COMMIT;
