-- =============================================================================
-- Goal events — minute-by-minute goal records
-- =============================================================================
-- Powers late-goal strategy and any timing-based research:
--   * Late-goal index per team (% of goals scored in 75'+)
--   * Late-goal conceded index
--   * Goal-rate distribution by minute bucket
--
-- One row per goal. Own goals are attributed to the SCORING side
-- (i.e. the team that benefits), with is_own_goal = TRUE flagged.
-- =============================================================================

CREATE TABLE IF NOT EXISTS goal_events (
    id            BIGSERIAL   PRIMARY KEY,
    match_id      BIGINT      NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    minute        SMALLINT    NOT NULL,                       -- 1..90+ (stoppage encoded as 45 or 90+)
    team_side     CHAR(1)     NOT NULL CHECK (team_side IN ('h','a')),
    is_own_goal   BOOLEAN     NOT NULL DEFAULT FALSE,
    is_penalty    BOOLEAN     NOT NULL DEFAULT FALSE,
    xg_at_shot    NUMERIC(6,4),                                -- xG of the shot (NULL if source has none)
    player_name   TEXT,
    source        TEXT        NOT NULL,                        -- 'understat' | 'fbref' | 'api-football'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (match_id, minute, team_side, player_name, source)
);

CREATE INDEX IF NOT EXISTS idx_goal_events_match  ON goal_events(match_id);
CREATE INDEX IF NOT EXISTS idx_goal_events_minute ON goal_events(minute);

COMMENT ON COLUMN goal_events.team_side IS
    'Side that benefits from the goal: ''h'' or ''a''. For own goals the side is flipped from the shooter.';
COMMENT ON COLUMN goal_events.minute IS
    'Minute the goal was scored (Understat clamps stoppage to 45 / 90).';
