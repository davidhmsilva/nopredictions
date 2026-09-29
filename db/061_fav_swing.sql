-- 061_fav_swing.sql — Favourite Swing: in on pressure, out after a goal (paper).
--
-- The rule, as the user specified it on 2026-09-29:
--   * a team that was 1.30-1.50 to win at kick-off (Polymarket's own pre-KO
--     "Will <team> win?" price, raw — the odds a bettor saw),
--   * the match is LEVEL (0-0, 1-1, …) and that team is pressing: its own
--     danger index >= 19 and at least 20 above the opponent's, the same gate as
--     s18 (fav_pressure_agent),
--   → buy "Will <team> win?" Yes at the CLOB ask, 1u, paper.
--   * ANY goal after entry starts a 5-minute clock; the clock restarts on every
--     further score change and is cancelled if the score returns to the entry
--     score (a goal that did not stand). When it runs out, sell at the CLOB bid.
--   * no goal → held to the end and settled by the market.
--
-- One row per position. The position carries its own state so the daemon can
-- restart mid-match without forgetting what it holds.

CREATE TABLE IF NOT EXISTS fav_swing_positions (
    id                 BIGSERIAL PRIMARY KEY,
    obs_version        INTEGER     NOT NULL DEFAULT 1,
    paper_trade_id     INTEGER     REFERENCES paper_trades(id),
    fixture_id         BIGINT      NOT NULL,
    league             TEXT,
    home               TEXT        NOT NULL,
    away               TEXT        NOT NULL,
    event_title        TEXT,
    fav_side           TEXT        NOT NULL CHECK (fav_side IN ('home', 'away')),
    fav_team           TEXT        NOT NULL,
    ko_price           NUMERIC(6,4) NOT NULL,     -- raw PM Yes price, last read before kick-off
    ko_price_at        TIMESTAMPTZ,
    token_id           TEXT        NOT NULL,
    condition_id       TEXT,
    -- entry
    entered_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    entry_minute       INTEGER     NOT NULL,
    entry_home_goals   INTEGER     NOT NULL,
    entry_away_goals   INTEGER     NOT NULL,
    entry_ask          NUMERIC(6,4) NOT NULL,
    entry_bid          NUMERIC(6,4),
    entry_ask_depth    NUMERIC(14,2),
    fav_pressure       NUMERIC(6,2),
    dog_pressure       NUMERIC(6,2),
    dominance          NUMERIC(6,2),
    pressure_source    TEXT,
    stats_source       TEXT,
    shares             NUMERIC(14,6) NOT NULL,    -- stake / (ask · (1 + fee·(1−ask)))
    fee_rate           NUMERIC(6,4) NOT NULL,
    -- the goal clock
    status             TEXT        NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open', 'goal_pending', 'sold', 'settled')),
    goal_seen_at       TIMESTAMPTZ,               -- last score change away from the entry score
    goal_minute        INTEGER,
    goal_home_goals    INTEGER,
    goal_away_goals    INTEGER,
    goals_reversed     INTEGER     NOT NULL DEFAULT 0,  -- times the score went back to the entry score
    last_seen_at       TIMESTAMPTZ,
    last_minute        INTEGER,
    -- exit
    exit_at            TIMESTAMPTZ,
    exit_minute        INTEGER,
    exit_bid           NUMERIC(6,4),
    exit_ask           NUMERIC(6,4),
    exit_reason        TEXT,                      -- 'sold_after_goal' | 'held_won' | 'held_lost'
    payout_units       NUMERIC(12,6),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fav_swing_status ON fav_swing_positions (status);
CREATE INDEX IF NOT EXISTS idx_fav_swing_fixture ON fav_swing_positions (fixture_id);

ALTER TABLE fav_swing_positions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE fav_swing_positions FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON TABLE fav_swing_positions FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

-- Hypothesis + strategy, owned by the operator's account so it shows on /agent.
INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT 'H-FAV-SWING — a 1.30-1.50 favourite pressing while level, sold 5 minutes after the next goal',
       'Buy "Will <favourite> win?" when the pre-KO price was 1.30-1.50, the match is level and the favourite presses (own index >= 19, dominance >= 20). Sell at the CLOB bid 5 minutes after any goal; hold to the end if none. 1u, paper.',
       'The user''s rule (2026-09-29). Priors that run against it: PM prices pressure correctly (finding_market_prices_pressure, n=723); the box-score reading adds ~0 over free state (finding_live_reading_ceiling); cashing out pays spread + fee twice (late-goals backtest). What it tests that nothing else has: the full-match win market, entered on a level score, exited on the goal rather than held.',
       'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-FAV-SWING%');

INSERT INTO strategies (hypothesis_id, name, source, owner_id, theory, interpretation, rules, run_status, promoted_at)
SELECT h.id, 'Favourite Swing — pressure in, goal out', 'agent',
       (SELECT id FROM profiles WHERE role = 'owner' LIMIT 1),
       'A 1.30-1.50 favourite that presses while the game is level is worth buying, and worth selling once a goal has moved the price.',
       'Back the team that was 1.30-1.50 at kick-off to win, when the score is level and it is pressing. Sell 5 minutes after any goal; with no goal, hold to the end. 1u at the Polymarket ask, paper.',
       '{"phase": "paper-only", "venue": "polymarket", "stake_u": 1, "self_settling": true, "obs_version": 1}'::jsonb,
       'running', now()
  FROM research_hypotheses h
 WHERE h.title LIKE 'H-FAV-SWING%'
   AND NOT EXISTS (SELECT 1 FROM strategies WHERE name = 'Favourite Swing — pressure in, goal out');
