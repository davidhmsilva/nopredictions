-- 065: ESPN match timelines (Stage L) — every goal and card with its minute,
-- for every competition ESPN covers, 2010 onwards.
--
-- Why: minute-level goal data existed for the Big 5 only (Understat, 15k
-- matches, ending 2024-25), so every in-play table — late goals, first half,
-- favourite at HT — was fitted on leagues Polymarket rarely lists, and none of
-- them knew about red cards. ESPN's scoreboard returns a whole calendar year
-- of a competition in one request, with each goal and card carrying its
-- minute and stoppage minute ("90'+5'").
--
-- Standalone, keyed by ESPN's event id; `match_id` links to our `matches`
-- where the date, the final score AND both team names agree (Stage L's
-- linker). Cups, European competitions and leagues we hold no matches for
-- are kept unlinked: they are fixtures for rest-day/congestion features and
-- rows for the in-play tables in their own right.

CREATE TABLE IF NOT EXISTS espn_matches (
    event_id        BIGINT       PRIMARY KEY,
    league_slug     TEXT         NOT NULL,            -- ESPN's, e.g. 'eng.1', 'uefa.champions'
    league_code     TEXT,                             -- our leagues.code where mapped
    season_year     INT,
    season_slug     TEXT,
    kickoff_utc     TIMESTAMPTZ  NOT NULL,            -- a real UTC instant (unlike Stage A's)
    status          TEXT,                             -- STATUS_FULL_TIME, STATUS_FINAL_AET, ...
    completed       BOOLEAN,
    home_espn_id    INT,
    away_espn_id    INT,
    home_name       TEXT         NOT NULL,
    away_name       TEXT         NOT NULL,
    home_score      SMALLINT,
    away_score      SMALLINT,
    -- Derived from the goal minutes, and only when the timeline reproduces
    -- the final score (timeline_ok). NULL is "not known", never 0-0.
    home_score_ht   SMALLINT,
    away_score_ht   SMALLINT,
    went_to_et      BOOLEAN,
    went_to_pens    BOOLEAN,
    venue           TEXT,
    attendance      INT,
    n_details       SMALLINT,
    timeline_ok     BOOLEAN,                          -- goals in the timeline == final score
    match_id        INT          REFERENCES matches(id),
    link_score      NUMERIC(4,3),
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_espn_matches_league_ko ON espn_matches (league_slug, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_espn_matches_match     ON espn_matches (match_id);

CREATE TABLE IF NOT EXISTS espn_match_events (
    event_id   BIGINT    NOT NULL REFERENCES espn_matches(event_id) ON DELETE CASCADE,
    seq        SMALLINT  NOT NULL,
    -- goal | own_goal | penalty_goal | yellow | red | shootout_goal |
    -- shootout_miss | other
    kind       TEXT      NOT NULL,
    type_text  TEXT,                 -- ESPN's own label, kept verbatim
    minute     SMALLINT,             -- base minute: 45 for 45'+2', 90 for 90'+5'
    added      SMALLINT  NOT NULL DEFAULT 0,
    period     SMALLINT,             -- 1, 2, 3-4 extra time, 5 shootout
    -- The side the event COUNTS for. ESPN credits an own goal to the team
    -- that benefits (Konaté's own goal for Brentford, 2023), which is what
    -- makes a timeline add up to the final score.
    team_side  CHAR(1)   CHECK (team_side IN ('H', 'A')),
    player     TEXT,
    PRIMARY KEY (event_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_espn_events_kind ON espn_match_events (kind);

-- Research tables: RLS on, no policies (db/045). Clients get nothing (db/048
-- default privileges); everything reads over DATABASE_URL.
ALTER TABLE espn_matches      ENABLE ROW LEVEL SECURITY;
ALTER TABLE espn_match_events ENABLE ROW LEVEL SECURITY;
