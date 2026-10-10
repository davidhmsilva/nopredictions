-- 067: Transfermarkt (Stage N) — every game of ~45 competitions 2006-2026
-- with referee, attendance, managers and formations, plus two line-up
-- features nothing here could compute before: what the starting XI was worth
-- on the day, and how many of them the club changed since its last game.
--
-- Source: dcaribou/transfermarkt-datasets (CC0), the public R2 bucket it
-- publishes (pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/*.csv.gz).
-- No account, no key. The dataset is refreshed by its maintainer; on
-- 2026-10-03 it ended at 2026-07-06, so it serves history, not live context.
--
-- Cups and European games are kept for themselves: the rest days and the
-- rotation of a league match depend on the Thursday before it.

CREATE TABLE IF NOT EXISTS tm_games (
    game_id           BIGINT       PRIMARY KEY,
    competition_id    TEXT         NOT NULL,     -- TM's: GB1, ES1, CL, FAC, ...
    competition_type  TEXT,                      -- domestic_league | domestic_cup | international_cup | other
    season            INT,                       -- start year: 2024 = 2024-25
    round             TEXT,
    game_date         DATE         NOT NULL,     -- local date, no time
    home_club_id      INT,
    away_club_id      INT,
    home_club_name    TEXT,
    away_club_name    TEXT,
    home_goals        SMALLINT,
    away_goals        SMALLINT,
    home_position     SMALLINT,                  -- league position as Transfermarkt records it
    away_position     SMALLINT,
    home_manager      TEXT,
    away_manager      TEXT,
    home_formation    TEXT,
    away_formation    TEXT,
    stadium           TEXT,
    attendance        INT,
    referee           TEXT,
    aggregate         TEXT,
    match_id          INT          REFERENCES matches(id),
    link_score        NUMERIC(4,3)
);

CREATE INDEX IF NOT EXISTS idx_tm_games_comp_date ON tm_games (competition_id, game_date);
CREATE INDEX IF NOT EXISTS idx_tm_games_home      ON tm_games (home_club_id, game_date);
CREATE INDEX IF NOT EXISTS idx_tm_games_away      ON tm_games (away_club_id, game_date);
CREATE INDEX IF NOT EXISTS idx_tm_games_match     ON tm_games (match_id);

CREATE TABLE IF NOT EXISTS tm_lineup_features (
    game_id            BIGINT    NOT NULL REFERENCES tm_games(game_id) ON DELETE CASCADE,
    club_id            INT       NOT NULL,
    n_starters         SMALLINT,
    n_starters_valued  SMALLINT,             -- starters with a market value on or before the day
    xi_value_eur       BIGINT,               -- sum of those values (as of the game date, never after)
    bench_value_eur    BIGINT,
    xi_changes         SMALLINT,             -- starters who did not start the club's previous game
    prev_game_id       BIGINT,               -- that previous game, any competition in the dataset
    days_since_prev    INT,
    PRIMARY KEY (game_id, club_id)
);

-- Transfermarkt club id -> our team, learned by Stage N's linker.
CREATE TABLE IF NOT EXISTS tm_club_map (
    tm_club_id   INT   PRIMARY KEY,
    team_id      INT   NOT NULL REFERENCES teams(id),
    tm_name      TEXT,
    n_links      INT
);

ALTER TABLE tm_games           ENABLE ROW LEVEL SECURITY;
ALTER TABLE tm_lineup_features ENABLE ROW LEVEL SECURITY;
ALTER TABLE tm_club_map        ENABLE ROW LEVEL SECURITY;
