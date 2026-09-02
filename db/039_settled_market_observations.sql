-- 039_settled_market_observations.sql
--
-- H-SETTLED-SWEEP — observation only, no money.
--
-- WHY THIS EXISTS
-- ---------------
-- Wallet 0xec5723df…560fa7 (GSX-) made +$85,423 in 105 days on a book that never
-- exceeded $29k, and **half of it in the 20 minutes after the final whistle**.
-- The decomposition of its post-whistle cheap buys:
--
--     bought the eventual WINNER   n=  323  cost $ 3,565  ->  +$27,931  (+783%)
--     bought the eventual LOSER    n=2,631  cost $19,030  ->     -$690  (  -4%)
--
-- The whole return is the winner leg, and the winner leg needs no model: at that
-- point the result is public and the token is worth exactly 1. What is unknown
-- is not whether such quotes exist — GSX got filled on 62 markets — but how
-- often they appear, how deep they are, and whether OUR settlement logic can be
-- trusted to identify them. This table answers all three, with nothing at risk.
--
-- Blind hold-vs-flip on those same lots was +158% vs +124%, so exit speed does
-- not matter: the strategy is buy-and-redeem, not scalp. That is what puts it
-- inside reach of this stack.
--
-- WHAT A ROW IS
-- -------------
-- One reading of one PM token whose outcome is ALREADY ARITHMETICALLY DETERMINED
-- by the score, taken from api-football's status/score and never from our own
-- tape (see db/038 for what a tape maximum did to strategy 16).
--
-- `rule_correct` is the point of the whole table. Our biggest risk is not the
-- market, it is our own settlement rule being wrong about what a market pays —
-- the one measured instance is a PM "end in a draw?" market that GSX bought at
-- 0.003 and that resolved NO. Every row is checked back against PM's own
-- resolution, so the error rate of the rule set is measured before a cent moves.

CREATE TABLE IF NOT EXISTS settled_market_observations (
    id              BIGSERIAL PRIMARY KEY,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    obs_version     SMALLINT    NOT NULL DEFAULT 1,

    -- fixture identity (api-football is the authority on which side is which)
    fixture_id      INTEGER,
    league          TEXT,
    home            TEXT,
    away            TEXT,
    pm_title        TEXT,

    -- match state at the reading. api-football only.
    af_status       TEXT        NOT NULL,   -- 1H / HT / 2H / FT
    minute          INTEGER,
    goals_home      SMALLINT,
    goals_away      SMALLINT,
    ht_home         SMALLINT,
    ht_away         SMALLINT,
    phase           TEXT        NOT NULL,   -- in_match | halftime | post_whistle
    -- Minutes since THIS PROCESS first saw the outcome determined. It is not
    -- "minutes since the goal": a restart resets it, and a fixture picked up
    -- late never gets a true zero. Named for what it measures.
    mins_since_first_seen INTEGER,

    -- the market
    condition_id    TEXT,
    token_id        TEXT        NOT NULL,
    question        TEXT,
    rule            TEXT        NOT NULL,   -- which settlement rule fired
    winning_outcome TEXT        NOT NULL,   -- the outcome we say is worth 1
    gamma_price     NUMERIC,                -- Gamma's quote for that token

    -- the executable book on the winning token.
    -- ⚠️ ask-only books are KEPT. A determined token routinely loses its bid
    -- side (makers pull it), and the standard _fetch_book helper returns None
    -- for exactly those — which would blind this table to its own subject.
    best_ask        NUMERIC,
    best_bid        NUMERIC,
    ask_depth_usd   NUMERIC,
    ask_levels      JSONB,
    book_missing    BOOLEAN     NOT NULL DEFAULT FALSE,  -- no ask at any price

    -- what a sweeper would have got, walking the real ladder
    would_enter     BOOLEAN     NOT NULL DEFAULT FALSE,
    entry_shares    NUMERIC,
    entry_cost_usd  NUMERIC,
    entry_vwap      NUMERIC,
    fee_pp          NUMERIC,                -- PM taker fee at that vwap
    edge_pp         NUMERIC,                -- (1 - vwap)*100 - fee_pp

    -- settlement: did our rule agree with PM's own resolution?
    settled_at      TIMESTAMPTZ,
    pm_winner       TEXT,
    rule_correct    BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_sm_obs_time   ON settled_market_observations (observed_at);
CREATE INDEX IF NOT EXISTS idx_sm_obs_token  ON settled_market_observations (token_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_sm_obs_settle ON settled_market_observations (settled_at)
    WHERE settled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sm_obs_rule   ON settled_market_observations (rule, phase);
CREATE INDEX IF NOT EXISTS idx_sm_obs_enter  ON settled_market_observations (would_enter, observed_at)
    WHERE would_enter;

INSERT INTO research_hypotheses (title, description, source, status, created_by, created_at)
SELECT
  'H-SETTLED-SWEEP',
  'A PM football market whose outcome is already arithmetically determined by '
  'the score is sometimes still quoted with a live ask well below 1. '
  'PRE-REGISTERED CLAIMS, forward-only: (1) such quotes appear at a measurable '
  'rate; (2) our deterministic settlement rule agrees with PM''s own resolution '
  'on >99% of rows (rule_correct); (3) the ask on the determined winner, taken '
  'and held to redemption, yields a CI clear of zero after the taker fee. '
  'PRIMARY TEST is on rule_correct, not on yield: the strategy has no model risk '
  'and no price risk, only settlement-logic risk, and that is what must be '
  'measured first. NULL: determined markets either lose their book entirely or '
  'are quoted at 0.97+, where GSX- earned +1.1%. Verdict gate: n>=200 '
  'would_enter rows, rule_correct >= 0.99, and a yield CI clear of zero after '
  'the fee. Motivated by the GSX- wallet decomposition (2026-09-02).',
  'wallet GSX- decomposition 2026-09-02', 'pre_registered', 'claude', now()
WHERE NOT EXISTS (
  SELECT 1 FROM research_hypotheses WHERE title = 'H-SETTLED-SWEEP'
);
