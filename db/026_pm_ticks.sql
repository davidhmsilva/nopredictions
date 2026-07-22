-- 026_pm_ticks.sql
-- High-frequency order-book capture for Polymarket football.
--
-- Why: every pre-match edge we tested in 2026-07-21 died the same death — the
-- signal was ~0.5-2pp and the round-trip spread was ~1-2.5pp. The only place
-- where price moves dwarf the spread is in-play (a goal moves 1x2 by 20-50pp).
-- But market_observations polls every ~30 min, which is blind to exactly that
-- microstructure. This table is the fix: top-of-book every ~60s.
--
-- Deliberately raw and un-joined. We record PM books and live fixture state
-- into SEPARATE tables and reconcile offline by name, so a name-matching miss
-- never costs us the underlying data.

-- (a) Polymarket top-of-book snapshots.
CREATE TABLE IF NOT EXISTS pm_ticks (
    id            BIGSERIAL PRIMARY KEY,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    token_id      TEXT        NOT NULL,
    condition_id  TEXT,
    event_title   TEXT,                 -- "Arsenal vs. Chelsea"
    question      TEXT,                 -- "Will Arsenal win on 2026-07-21?"
    outcome       TEXT,                 -- PM outcome label for this token

    best_bid      NUMERIC,
    best_ask      NUMERIC,
    bid_depth_usd NUMERIC,              -- notional across top 5 bid levels
    ask_depth_usd NUMERIC,
    bid_levels    JSONB,                -- [[price,size],...] top 5
    ask_levels    JSONB,

    end_date      TIMESTAMPTZ           -- PM market endDate (kickoff proxy)
);

CREATE INDEX IF NOT EXISTS idx_pm_ticks_token_time ON pm_ticks (token_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_pm_ticks_time       ON pm_ticks (observed_at);
CREATE INDEX IF NOT EXISTS idx_pm_ticks_event      ON pm_ticks (event_title, observed_at);

COMMENT ON TABLE pm_ticks IS
    'High-frequency PM top-of-book snapshots (~60s) for football. Written by tick_recorder.py.';

-- (b) Live fixture state, recorded independently of any PM match.
CREATE TABLE IF NOT EXISTS live_fixture_ticks (
    id           BIGSERIAL PRIMARY KEY,
    observed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    fixture_id   BIGINT      NOT NULL,
    home         TEXT        NOT NULL,
    away         TEXT        NOT NULL,
    league       TEXT,
    status       TEXT,                  -- 1H / HT / 2H / ET / FT ...
    elapsed      INT,
    home_goals   INT,
    away_goals   INT,
    home_reds    INT DEFAULT 0,
    away_reds    INT DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_lft_fixture_time ON live_fixture_ticks (fixture_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_lft_time         ON live_fixture_ticks (observed_at);
CREATE INDEX IF NOT EXISTS idx_lft_teams        ON live_fixture_ticks (home, away, observed_at);

COMMENT ON TABLE live_fixture_ticks IS
    'Live score/minute snapshots from api-football, recorded raw. Joined to pm_ticks offline by team name.';
