-- 029 — late-goal observation layer (paper only, no orders)
--
-- Question being measured: at minute 70-88 of a live football match, does
-- Polymarket's price on the "one more goal" over line beat the empirical fair
-- value derived from goal_events (16,479 clean matches, minute-level)?
--
-- We already know the fair side (db-derived, see agent/late_goals_table.py):
-- conditional on the goals scored so far, the PRE-MATCH total still predicts a
-- late goal monotonically — 39.5% in the lowest bucket vs 53.6% in the highest,
-- CIs disjoint over n=4,444. What is NOT known is whether PM misprices it.
-- pm_ticks only starts 2026-07-21, so there is no history to mine. This table
-- accumulates it.
--
-- Deliberately records EVERY eligible live poll, including states we would not
-- bet (edge negative, score ambiguous, book one-sided). Filtering happens at
-- analysis time; a row not written is a row we can never analyse. Same lesson
-- as the observer endDate bug and tick_recorder's separate-tables design.

CREATE TABLE IF NOT EXISTS late_goal_observations (
    id               bigserial PRIMARY KEY,
    observed_at      timestamptz NOT NULL DEFAULT now(),
    phase            text        NOT NULL,          -- 'prematch' | 'live'

    -- fixture identity (PM-side; matches.id is NULL for future fixtures)
    event_title      text        NOT NULL,
    condition_id     text,
    token_id         text,
    kickoff_utc      timestamptz,

    -- clock. Derived from gameStartTime, NOT from a live feed: api-football's
    -- daily quota is routinely exhausted (live_fixture_ticks had 46 rows total
    -- on 2026-07-22). wall_minute is kept raw so the halftime assumption baked
    -- into game_minute can be recalibrated later against real elapsed values.
    wall_minute      integer,
    game_minute      integer,
    minute_source    text,                          -- 'clock' | 'api'

    -- score state inferred from the over-line ladder: "Over N.5" quoted at
    -- >= 0.97 means the match is already past N goals, so a settled line gives
    -- a lower bound and the cheapest unsettled line gives an upper bound. Two
    -- adjacent lines pin the total exactly.
    goals_lower      integer,
    goals_upper      integer,
    goals_certain    boolean     NOT NULL DEFAULT false,
    score_source     text,                          -- 'ladder' | 'api' | 'both'
    api_goals        integer,                       -- when api-football answered
    ladder           jsonb,                         -- {line: over_mid} as seen

    -- the pre-match "market believed in goals" signal the strategy keys on.
    -- PM's own de-vigged over-2.5 at/just before kickoff. NOTE: the backtest
    -- buckets on vig-free PINNACLE, so this is a proxy — the mapping between
    -- the two is itself something this table is collecting evidence for.
    pre_over25       numeric,
    pre_over25_src   text,                          -- 'self' | 'pm_ticks'

    -- the market actually under observation: Over (goals_so_far + 0.5),
    -- i.e. the line that needs exactly ONE more goal. Chosen over the
    -- two-goals-away line on purpose: buying the leveraged line to cash out on
    -- the first goal is EV-neutral by construction (the price is a martingale)
    -- and pays fee + spread twice, ~15-18% of stake.
    target_line      numeric,
    best_bid         numeric,
    best_ask         numeric,
    bid_depth_usd    numeric,
    ask_depth_usd    numeric,

    -- verdict at observation time
    fair_prob        numeric,                       -- empirical table lookup
    fair_n           integer,                       -- cell support
    fee_pp           numeric,                       -- taker fee at best_ask
    edge_pp          numeric,                       -- 100*(fair - ask) - fee
    would_enter      boolean     NOT NULL DEFAULT false,

    -- settlement, backfilled once the match is over
    final_goals      integer,
    goal_after       boolean,                       -- did the target line hit
    settled_at       timestamptz
);

CREATE INDEX IF NOT EXISTS idx_lgo_event   ON late_goal_observations (event_title, observed_at);
CREATE INDEX IF NOT EXISTS idx_lgo_live    ON late_goal_observations (phase, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_lgo_pending ON late_goal_observations (settled_at) WHERE settled_at IS NULL;

COMMENT ON TABLE late_goal_observations IS
    'Paper-only observation of PM live over-line prices at minute 70-88 vs the '
    'empirical late-goal fair value. No orders are ever placed from this table.';
COMMENT ON COLUMN late_goal_observations.goals_certain IS
    'TRUE only when a settled line and the cheapest unsettled line are adjacent, '
    'pinning the total exactly. Ambiguous rows are still recorded — filter at '
    'analysis time, never at collection time.';
COMMENT ON COLUMN late_goal_observations.would_enter IS
    'What the strategy WOULD have done. No money moves. Any P&L computed from '
    'this column is a paper exit price, not a verified fill (see db/028).';
