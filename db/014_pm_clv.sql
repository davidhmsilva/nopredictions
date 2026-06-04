-- 014_pm_clv.sql
-- Polymarket closing-line value (PM-CLV): did our entry price beat Polymarket's
-- OWN closing price near kickoff?
--
-- Why this is new and necessary:
--   * paper_trades.clv (see 010_clv_source.sql) measures CLV vs the SHARP line
--     (Pinnacle / Betfair closing). But we trade on Polymarket, not Pinnacle.
--   * The most direct edge test for a PM-only trader is: did the price we paid on
--     PM beat PM's own closing price at kickoff? That is PM-CLV. We could not
--     measure it before because we had no historical PM price series.
--   * Now we do, from two sources (see agent/pm_clv_backfill.py), in priority:
--       'observed'     — the last pre-kickoff snapshot the observer (observer.py /
--                        market_observations) already recorded for the bought
--                        token. Fine-grained, free, in our own DB.
--       'clob_history' — Polymarket CLOB /prices-history for the bought token,
--                        last point at/just before kickoff. Backfills the period
--                        before the observer existed. NOTE: PM only serves >=12h
--                        granularity once a market resolves, so this source is
--                        coarse for already-settled markets (pm_closing_at shows
--                        exactly how stale the point used is).
--
-- Convention (identical units to clv): pm_clv = entry_odds * pm_closing_price - 1,
-- computed on the token we actually bought, so it is correct for YES and NO bets.

ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS pm_closing_price NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS pm_closing_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pm_clv           NUMERIC,
    ADD COLUMN IF NOT EXISTS pm_clv_source    TEXT;

COMMENT ON COLUMN paper_trades.pm_clv IS
    'Polymarket closing-line value: entry_odds * pm_closing_price - 1, on the bought token. NULL unless a plausible PM closing price was found. See pm_clv_source.';
COMMENT ON COLUMN paper_trades.pm_closing_price IS
    'Polymarket price of the BOUGHT token at/just before kickoff (the PM closing line), 0<p<1.';
COMMENT ON COLUMN paper_trades.pm_closing_at IS
    'Timestamp of the PM snapshot used as the closing line — shows staleness/provenance.';
COMMENT ON COLUMN paper_trades.pm_clv_source IS
    'observed | clob_history | suspect';
