-- db/072 — the signal ledger: per paper trade, the edge at the signal, what
-- the line and the venue did by the close, and how much of the edge survived.
-- Phase 1b of the edge-retention plan (CLV and retention before P&L).
--
-- Almost everything was already being recorded: the three every-game agents
-- write Pinnacle's no-vig fair at entry (`sharp_consensus_price`, fair_raw),
-- Pinnacle's fair at the close (`closing_price`, read <= 12 min before
-- kick-off) and Polymarket's mid at the close (`pm_closing_price`). What was
-- missing, and is added here, is written forward only:
--   * the ask depth of the book at the signal (`*_candidates.ask_depth_usd`);
--   * the un-haircut fair on NFL/NBA candidates (`fair_raw`; UNL had it), so
--     the FIRST sharp line we saw for a token is on the same basis as the rest;
--   * the venue's bid and ask at the close (`paper_trades.pm_closing_bid/ask`),
--     so venue CLV can be ask against ask, not ask against mid.
--
-- 🔑 Edge and CLV are on ONE basis: fair_raw (no haircut, no fee) against the
-- entry ask. So   clv_sharp = edge_at_signal + line movement   exactly, and
-- retention = clv_sharp / edge_at_signal = how much of the edge the close kept.
-- The NFL `clv` once compared a haircut EV with an un-haircut close; that is
-- the defect this basis removes. `edge_net` (fee and haircut included) is kept
-- beside it for what the agent actually decided on.
--
-- ⚠️ There is no Pinnacle OPENING line: `first_sharp_fair` is the first
-- Pinnacle line WE observed for that token (agents look from 24h out), with its
-- time. The venue's own open comes from venue_market_quotes (db/071), for the
-- markets it records (PM football, both venues' US moneylines and main total).
-- ⚠️ `fair_source = 'sharp_model'` rows are priced by our model off Pinnacle's
-- main line, not by Pinnacle itself. Never pool them with `sharp_exact`.

ALTER TABLE nfl_candidates ADD COLUMN IF NOT EXISTS fair_raw      numeric;
ALTER TABLE nfl_candidates ADD COLUMN IF NOT EXISTS ask_depth_usd numeric;
ALTER TABLE nba_candidates ADD COLUMN IF NOT EXISTS fair_raw      numeric;
ALTER TABLE nba_candidates ADD COLUMN IF NOT EXISTS ask_depth_usd numeric;
ALTER TABLE unl_candidates ADD COLUMN IF NOT EXISTS ask_depth_usd numeric;
ALTER TABLE paper_trades   ADD COLUMN IF NOT EXISTS pm_closing_bid numeric;
ALTER TABLE paper_trades   ADD COLUMN IF NOT EXISTS pm_closing_ask numeric;

CREATE INDEX IF NOT EXISTS nfl_candidates_token_idx ON nfl_candidates (token_id, observed_at);
CREATE INDEX IF NOT EXISTS unl_candidates_token_idx ON unl_candidates (token_id, observed_at);
CREATE INDEX IF NOT EXISTS nba_candidates_token_idx ON nba_candidates (token_id, observed_at);
CREATE INDEX IF NOT EXISTS nfl_candidates_trade_idx ON nfl_candidates (paper_trade_id) WHERE paper_trade_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS unl_candidates_trade_idx ON unl_candidates (paper_trade_id) WHERE paper_trade_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS nba_candidates_trade_idx ON nba_candidates (paper_trade_id) WHERE paper_trade_id IS NOT NULL;

CREATE OR REPLACE VIEW v_signal_ledger WITH (security_invoker = true) AS
WITH cand AS (
    SELECT 'nfl' AS sport, id, observed_at, paper_trade_id, token_id, kickoff, market_type, line,
           side, pm_bid, pm_ask, pm_liquidity, ask_depth_usd, fair, fair_raw, fair_source, fair_book
      FROM nfl_candidates
    UNION ALL
    SELECT 'soccer_unl', id, observed_at, paper_trade_id, token_id, kickoff, market_type, line,
           side, pm_bid, pm_ask, pm_liquidity, ask_depth_usd, fair, fair_raw, fair_source, fair_book
      FROM unl_candidates
    UNION ALL
    SELECT 'nba', id, observed_at, paper_trade_id, token_id, kickoff, market_type, line,
           side, pm_bid, pm_ask, pm_liquidity, ask_depth_usd, fair, fair_raw, fair_source, fair_book
      FROM nba_candidates
),
chosen AS (                       -- the candidate row the trade was written from
    SELECT DISTINCT ON (paper_trade_id) *
      FROM cand WHERE paper_trade_id IS NOT NULL
     ORDER BY paper_trade_id, observed_at
),
first_sharp AS (                  -- the first Pinnacle-anchored line we saw for the token
    SELECT DISTINCT ON (token_id) token_id, observed_at AS first_sharp_at,
           -- before db/072 NFL/NBA kept only the haircut fair; on sharp_exact
           -- rows there is no haircut, so that one is the raw fair too
           COALESCE(fair_raw, CASE WHEN fair_source = 'sharp_exact' THEN fair END) AS first_sharp_fair
      FROM cand
     WHERE fair_source IN ('sharp_exact', 'sharp_model')
       AND COALESCE(fair_raw, CASE WHEN fair_source = 'sharp_exact' THEN fair END) IS NOT NULL
     ORDER BY token_id, observed_at
),
base AS (
    SELECT pt.id AS trade_id, pt.strategy_id, s.name AS strategy, pt.confidence AS kind,
           ch.sport, ch.market_type, ch.line, ch.side, pt.outcome AS label,
           pt.placed_at AS signal_at,
           COALESCE(ch.kickoff, vmq.kickoff) AS kickoff,
           ch.fair_source, ch.fair_book,
           pt.entry_price::float8 AS entry_price,
           ch.pm_bid::float8 AS venue_bid_at_signal,
           ch.pm_ask::float8 AS venue_ask_at_signal,
           ch.ask_depth_usd::float8 AS venue_depth_usd,
           ch.pm_liquidity::float8 AS venue_liquidity_usd,
           fs.first_sharp_at, fs.first_sharp_fair::float8 AS first_sharp_fair,
           pt.sharp_consensus_price::float8 AS fair_at_signal,
           CASE WHEN pt.clv_source LIKE '%close%' THEN pt.closing_price::float8 END AS fair_at_close,
           pt.expected_edge::float8 AS edge_net,
           pt.pm_closing_price::float8 AS venue_close_mid,
           pt.pm_closing_bid::float8 AS venue_close_bid,
           pt.pm_closing_ask::float8 AS venue_close_ask,
           pt.pm_closing_at AS venue_close_at,
           vmq.listed_at AS venue_listed_at, vmq.open_at AS venue_open_at,
           vmq.open_bid::float8 AS venue_open_bid, vmq.open_ask::float8 AS venue_open_ask,
           pt.stake_units::float8 AS stake_units, pt.result,
           pt.payout_units::float8 AS payout_units
      FROM paper_trades pt
      JOIN strategies s ON s.id = pt.strategy_id
      LEFT JOIN chosen ch ON ch.paper_trade_id = pt.id
      LEFT JOIN first_sharp fs ON fs.token_id = pt.pm_token_id
      LEFT JOIN venue_market_quotes vmq
             ON vmq.venue = 'polymarket' AND vmq.market_key = pt.pm_token_id
     WHERE ch.paper_trade_id IS NOT NULL
        -- a trade with no candidate row (the Lab runner) joins by token; an old
        -- trade on a token re-listed months later is not the same market
        OR (vmq.market_key IS NOT NULL AND pt.placed_at >= vmq.kickoff - interval '21 days')
)
SELECT b.*,
       EXTRACT(epoch FROM b.kickoff - b.signal_at) / 3600.0       AS hours_to_kickoff,
       b.venue_ask_at_signal - b.venue_bid_at_signal               AS venue_spread_at_signal,
       -- edge and CLV on one basis: fair_raw against the entry ask, before fee
       b.fair_at_signal / NULLIF(b.entry_price, 0) - 1             AS edge_at_signal,
       b.fair_at_close  / NULLIF(b.entry_price, 0) - 1             AS clv_sharp,
       b.fair_at_close  / NULLIF(b.fair_at_signal, 0) - 1          AS line_move,
       b.fair_at_signal / NULLIF(b.first_sharp_fair, 0) - 1        AS move_since_first_sharp,
       -- per row only where the edge was positive; aggregate as Σclv / Σedge
       CASE WHEN b.fair_at_signal > b.entry_price
            THEN (b.fair_at_close / b.entry_price - 1) / (b.fair_at_signal / b.entry_price - 1)
       END                                                          AS retention_sharp,
       -- the venue against itself, price against price
       b.venue_close_mid / NULLIF(b.entry_price, 0) - 1            AS venue_clv_mid,
       b.venue_close_ask / NULLIF(b.entry_price, 0) - 1            AS venue_clv_ask,
       CASE WHEN b.stake_units > 0 AND b.payout_units IS NOT NULL
            THEN b.payout_units - b.stake_units END                AS pnl_units_gross,
       -- payout_units is GROSS; PM's taker fee on a stake is stake·0.05·(1−ask)
       CASE WHEN b.stake_units > 0 AND b.payout_units IS NOT NULL
            THEN b.payout_units - b.stake_units
                 - b.stake_units * 0.05 * (1 - b.entry_price) END   AS pnl_units_net
  FROM base b;

REVOKE ALL ON v_signal_ledger FROM anon, authenticated;
