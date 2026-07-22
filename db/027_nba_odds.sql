-- 027_nba_odds.sql
-- NBA closing lines + the NBA arm of the Hypothesis Tester.
--
-- Source: github.com/flancast90/sportsbookreview-scraper (MIT), a pre-scraped
-- archive of sportsbookreview.com covering seasons 2011-12 .. 2021-22. The
-- original host (sportsbookreviewsonline.com) no longer serves the files, so
-- that repo is the surviving copy and the range CANNOT currently be extended.
-- Our matches table starts at 2014-15, so the joinable window is 2014-15..2021-22.
--
-- Benchmark caveat (surface this to users): the line is sportsbookreview's
-- consensus close, NOT Pinnacle. Slightly softer than the football benchmark.
--
-- Juice caveat (surface this too): the archive carries the spread/total LINE but
-- not its price. Spread and total backtests therefore assume the market-standard
-- -110 both sides (1.9091 decimal). Moneyline uses the real archived prices.
--
-- Source data quality: ~7.4% of archive rows have the spread and total columns
-- SWAPPED. The loader repairs them (a spread never reaches 150 and a total never
-- drops below 150, so the magnitudes are unambiguous; the spread's lost sign is
-- inferred from the moneyline favourite — validated at 100% agreement over the
-- 9,176 clean rows). Post-repair the data is calibrated: total lands +0.30 pts
-- from the mean outcome, overs hit 49.8%, home covers 49.4%, home favourites
-- win 69.7%, book margin 3.84%.
--
-- Loader:  ingest/stage_h_nba_odds.py
-- Refresh: SELECT refresh_bt_nba();

CREATE TABLE IF NOT EXISTS nba_odds (
    match_id            BIGINT PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    source              TEXT        NOT NULL DEFAULT 'sbr-archive',
    close_ml_home       NUMERIC(8,3),   -- decimal odds
    close_ml_away       NUMERIC(8,3),
    open_spread_home    NUMERIC(6,2),   -- home line, e.g. -4.5
    close_spread_home   NUMERIC(6,2),
    open_total          NUMERIC(6,2),
    close_total         NUMERIC(6,2),
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE nba_odds IS
  'NBA closing lines from the sportsbookreview archive (2014-15..2021-22). '
  'Consensus close, not Pinnacle. Spread/total prices are not archived — '
  'backtests assume -110.';


-- Flattened per-game NBA features, mirroring bt_features for football.
CREATE TABLE IF NOT EXISTS bt_nba (
    match_id          BIGINT PRIMARY KEY,
    season_label      TEXT        NOT NULL,   -- '2014-15'
    season_start      INTEGER     NOT NULL,   -- 2014
    tipoff_utc        TIMESTAMPTZ NOT NULL,
    home_team         TEXT        NOT NULL,
    away_team         TEXT        NOT NULL,
    home_score        INTEGER     NOT NULL,
    away_score        INTEGER     NOT NULL,
    total_points      INTEGER     NOT NULL,
    margin            INTEGER     NOT NULL,   -- home_score - away_score
    close_ml_home     NUMERIC(8,3),
    close_ml_away     NUMERIC(8,3),
    open_spread_home  NUMERIC(6,2),
    close_spread_home NUMERIC(6,2),
    open_total        NUMERIC(6,2),
    close_total       NUMERIC(6,2),
    fav_side          CHAR(1),                -- 'H' | 'A' by closing moneyline
    is_playoff        BOOLEAN     NOT NULL DEFAULT false,
    -- pre-game features (NULL when insufficient history); no lookahead
    home_rest_days    INTEGER,
    away_rest_days    INTEGER,
    home_form_w5      INTEGER,                -- wins in team's previous 5 games (0..5)
    away_form_w5      INTEGER,
    home_avg_tp5      NUMERIC(7,3),           -- avg combined points, team's previous 5
    away_avg_tp5      NUMERIC(7,3)
);

CREATE INDEX IF NOT EXISTS idx_bt_nba_season ON bt_nba(season_start);
CREATE INDEX IF NOT EXISTS idx_bt_nba_tipoff ON bt_nba(tipoff_utc);


CREATE OR REPLACE FUNCTION refresh_bt_nba() RETURNS integer AS $$
DECLARE
    n integer;
BEGIN
    TRUNCATE bt_nba;

    INSERT INTO bt_nba (
        match_id, season_label, season_start, tipoff_utc,
        home_team, away_team, home_score, away_score, total_points, margin,
        close_ml_home, close_ml_away, open_spread_home, close_spread_home,
        open_total, close_total, fav_side, is_playoff,
        home_rest_days, away_rest_days, home_form_w5, away_form_w5,
        home_avg_tp5, away_avg_tp5
    )
    WITH nba AS (
        SELECT m.id, m.kickoff_utc, m.home_team_id, m.away_team_id,
               m.home_score, m.away_score, s.id AS season_id, s.label
        FROM matches m
        JOIN seasons s ON s.id = m.season_id
        JOIN leagues l ON l.id = s.league_id AND l.code = 'USA-NBA'
        WHERE m.status = 'finished' AND m.home_score IS NOT NULL
    ),
    -- Playoffs by schedule density, not calendar month: the regular season is
    -- any date on which >= 20 distinct teams played. A month-based rule misfires
    -- on the COVID seasons (2019-20 ran into October; 2020-21's regular season
    -- ran into May). This yields 78-85 playoff games per season, as expected.
    day_density AS (
        SELECT season_id, kickoff_utc::date AS d,
               COUNT(DISTINCT home_team_id) + COUNT(DISTINCT away_team_id) AS teams
        FROM nba GROUP BY 1, 2
    ),
    reg_end AS (
        SELECT season_id, MAX(d) AS last_regular_day
        FROM day_density WHERE teams >= 20 GROUP BY season_id
    ),
    tm AS (
        SELECT id AS match_id, kickoff_utc, home_team_id AS team_id,
               (home_score > away_score)::int AS win,
               home_score + away_score AS tp
        FROM nba
        UNION ALL
        SELECT id, kickoff_utc, away_team_id,
               (away_score > home_score)::int,
               home_score + away_score
        FROM nba
    ),
    tf AS (
        SELECT match_id, team_id,
               (kickoff_utc::date - LAG(kickoff_utc::date) OVER w) AS rest_days,
               SUM(win) OVER w5                                    AS form_w5,
               AVG(tp)  OVER w5                                    AS avg_tp5,
               COUNT(*) OVER w5                                    AS prev_n
        FROM tm
        WINDOW w  AS (PARTITION BY team_id ORDER BY kickoff_utc, match_id),
               w5 AS (PARTITION BY team_id ORDER BY kickoff_utc, match_id
                      ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)
    )
    SELECT g.id, g.label,
           substring(g.label from 1 for 4)::int,
           g.kickoff_utc, th.canonical_name, ta.canonical_name,
           g.home_score, g.away_score,
           g.home_score + g.away_score,
           g.home_score - g.away_score,
           o.close_ml_home, o.close_ml_away,
           o.open_spread_home, o.close_spread_home,
           o.open_total, o.close_total,
           CASE WHEN o.close_ml_home < o.close_ml_away THEN 'H'
                WHEN o.close_ml_away < o.close_ml_home THEN 'A' END,
           COALESCE(g.kickoff_utc::date > re.last_regular_day, false),
           hf.rest_days, af.rest_days,
           CASE WHEN hf.prev_n = 5 THEN hf.form_w5 END,
           CASE WHEN af.prev_n = 5 THEN af.form_w5 END,
           CASE WHEN hf.prev_n = 5 THEN ROUND(hf.avg_tp5, 3) END,
           CASE WHEN af.prev_n = 5 THEN ROUND(af.avg_tp5, 3) END
    FROM nba g
    JOIN nba_odds o ON o.match_id = g.id
    JOIN teams th ON th.id = g.home_team_id
    JOIN teams ta ON ta.id = g.away_team_id
    LEFT JOIN reg_end re ON re.season_id = g.season_id
    LEFT JOIN tf hf ON hf.match_id = g.id AND hf.team_id = g.home_team_id
    LEFT JOIN tf af ON af.match_id = g.id AND af.team_id = g.away_team_id;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$ LANGUAGE plpgsql;


-- run_backtest_nba(spec) — flat 1u backtest over bt_nba.
--
-- spec:
--   market: 'nba_ml' | 'nba_spread' | 'nba_total'
--   side:   'home' | 'away'        (nba_ml, nba_spread)
--           'over' | 'under'       (nba_total)
--   season_start, season_end        int years on season start (2014..2021)
--   odds_min, odds_max              decimal odds on the backed side
--   fav_status: 'favorite' | 'underdog'   (by closing moneyline)
--   home_team, away_team            substring on canonical team name
--   game_type: 'regular' | 'playoff'
--   spread_min, spread_max          home closing spread range (negative = home favoured)
--   total_min, total_max            closing total range
--   home_rest_days_min/max, away_rest_days_min/max
--   home_form_w5_min/max, away_form_w5_min/max      (0..5 wins in last 5)
--   home_avg_tp5_min/max, away_avg_tp5_min/max      (avg combined points, last 5)
--
-- Pushes (margin exactly on the spread, total exactly on the line) are returned
-- as stake-back: they count in n but contribute 0 pnl, matching sportsbook rules.
CREATE OR REPLACE FUNCTION run_backtest_nba(spec jsonb) RETURNS jsonb AS $$
DECLARE
    v_market text := spec->>'market';
    v_side   text := spec->>'side';
    -- market-standard -110 on spreads and totals; the archive has no price column
    v_juice  numeric := 1.9091;
    result   jsonb;
BEGIN
    IF v_market NOT IN ('nba_ml','nba_spread','nba_total') THEN
        RAISE EXCEPTION 'invalid nba market: %', v_market;
    END IF;
    IF (v_market IN ('nba_ml','nba_spread') AND v_side NOT IN ('home','away'))
       OR (v_market = 'nba_total' AND v_side NOT IN ('over','under')) THEN
        RAISE EXCEPTION 'invalid side % for market %', v_side, v_market;
    END IF;

    WITH base AS (
        SELECT f.*,
            CASE
              WHEN v_market = 'nba_ml' THEN
                   CASE v_side WHEN 'home' THEN f.close_ml_home ELSE f.close_ml_away END
              ELSE v_juice
            END AS oc,
            -- push flag: exact landing on the line
            CASE
              WHEN v_market = 'nba_spread'
                   THEN (f.margin + f.close_spread_home) = 0
              WHEN v_market = 'nba_total'
                   THEN f.total_points = f.close_total
              ELSE false
            END AS is_push,
            CASE
              WHEN v_market = 'nba_ml' THEN
                   CASE v_side WHEN 'home' THEN f.margin > 0 ELSE f.margin < 0 END
              WHEN v_market = 'nba_spread' THEN
                   CASE v_side WHEN 'home' THEN (f.margin + f.close_spread_home) > 0
                               ELSE (f.margin + f.close_spread_home) < 0 END
              WHEN v_market = 'nba_total' THEN
                   CASE v_side WHEN 'over'  THEN f.total_points > f.close_total
                               ELSE f.total_points < f.close_total END
            END AS won
        FROM bt_nba f
        WHERE ((spec->>'season_start') IS NULL OR f.season_start >= (spec->>'season_start')::int)
          AND ((spec->>'season_end')   IS NULL OR f.season_start <= (spec->>'season_end')::int)
          AND ((spec->>'home_team') IS NULL OR f.home_team ILIKE '%' || (spec->>'home_team') || '%')
          AND ((spec->>'away_team') IS NULL OR f.away_team ILIKE '%' || (spec->>'away_team') || '%')
          AND ((spec->>'game_type') IS NULL
               OR ((spec->>'game_type') = 'playoff' AND f.is_playoff)
               OR ((spec->>'game_type') = 'regular' AND NOT f.is_playoff))
          AND (
                (spec->>'fav_status') IS NULL
                OR ((spec->>'fav_status') = 'favorite' AND
                    ((v_side = 'home' AND f.fav_side = 'H') OR (v_side = 'away' AND f.fav_side = 'A')))
                OR ((spec->>'fav_status') = 'underdog' AND
                    ((v_side = 'home' AND f.fav_side = 'A') OR (v_side = 'away' AND f.fav_side = 'H')))
              )
          AND ((spec->>'spread_min') IS NULL OR f.close_spread_home >= (spec->>'spread_min')::numeric)
          AND ((spec->>'spread_max') IS NULL OR f.close_spread_home <= (spec->>'spread_max')::numeric)
          AND ((spec->>'total_min')  IS NULL OR f.close_total >= (spec->>'total_min')::numeric)
          AND ((spec->>'total_max')  IS NULL OR f.close_total <= (spec->>'total_max')::numeric)
          AND ((spec->>'home_rest_days_min') IS NULL OR f.home_rest_days >= (spec->>'home_rest_days_min')::int)
          AND ((spec->>'home_rest_days_max') IS NULL OR f.home_rest_days <= (spec->>'home_rest_days_max')::int)
          AND ((spec->>'away_rest_days_min') IS NULL OR f.away_rest_days >= (spec->>'away_rest_days_min')::int)
          AND ((spec->>'away_rest_days_max') IS NULL OR f.away_rest_days <= (spec->>'away_rest_days_max')::int)
          AND ((spec->>'home_form_w5_min') IS NULL OR f.home_form_w5 >= (spec->>'home_form_w5_min')::int)
          AND ((spec->>'home_form_w5_max') IS NULL OR f.home_form_w5 <= (spec->>'home_form_w5_max')::int)
          AND ((spec->>'away_form_w5_min') IS NULL OR f.away_form_w5 >= (spec->>'away_form_w5_min')::int)
          AND ((spec->>'away_form_w5_max') IS NULL OR f.away_form_w5 <= (spec->>'away_form_w5_max')::int)
          AND ((spec->>'home_avg_tp5_min') IS NULL OR f.home_avg_tp5 >= (spec->>'home_avg_tp5_min')::numeric)
          AND ((spec->>'home_avg_tp5_max') IS NULL OR f.home_avg_tp5 <= (spec->>'home_avg_tp5_max')::numeric)
          AND ((spec->>'away_avg_tp5_min') IS NULL OR f.away_avg_tp5 >= (spec->>'away_avg_tp5_min')::numeric)
          AND ((spec->>'away_avg_tp5_max') IS NULL OR f.away_avg_tp5 <= (spec->>'away_avg_tp5_max')::numeric)
    ),
    sel AS (
        SELECT *,
               CASE WHEN is_push THEN 0
                    WHEN won     THEN oc - 1
                    ELSE -1 END AS pnl_c
        FROM base
        WHERE oc IS NOT NULL
          AND (v_market <> 'nba_spread' OR close_spread_home IS NOT NULL)
          AND (v_market <> 'nba_total'  OR close_total IS NOT NULL)
          AND ((spec->>'odds_min') IS NULL OR oc >= (spec->>'odds_min')::numeric)
          AND ((spec->>'odds_max') IS NULL OR oc <= (spec->>'odds_max')::numeric)
    ),
    agg AS (
        SELECT COUNT(*)                            AS n,
               COUNT(*) FILTER (WHERE won AND NOT is_push) AS wins,
               COUNT(*) FILTER (WHERE is_push)     AS pushes,
               COALESCE(SUM(pnl_c), 0)             AS pnl,
               COALESCE(SUM(pnl_c * pnl_c), 0)     AS pnl_sq,
               AVG(oc)                             AS avg_odds,
               MIN(tipoff_utc)                     AS first_match,
               MAX(tipoff_utc)                     AS last_match
        FROM sel
    ),
    by_season AS (
        SELECT season_start, COUNT(*) AS n,
               COUNT(*) FILTER (WHERE won AND NOT is_push) AS wins,
               SUM(pnl_c) AS pnl
        FROM sel GROUP BY season_start
    ),
    by_month AS (
        SELECT to_char(date_trunc('month', tipoff_utc), 'YYYY-MM') AS month,
               COUNT(*) AS n, SUM(pnl_c) AS pnl
        FROM sel GROUP BY 1
    )
    SELECT jsonb_build_object(
        'n', agg.n,
        'wins', agg.wins,
        'pushes', agg.pushes,
        'pnl', ROUND(agg.pnl::numeric, 3),
        'pnl_sq', ROUND(agg.pnl_sq::numeric, 4),
        'avg_odds', ROUND(agg.avg_odds::numeric, 3),
        -- no archived opening price for NBA -> no CLV arm
        'n_open', 0,
        'pnl_open', NULL,
        'clv_avg', NULL,
        'first_match', agg.first_match,
        'last_match', agg.last_match,
        'seasons', (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                        'season', season_start, 'n', n, 'wins', wins,
                        'pnl', ROUND(pnl::numeric, 3)) ORDER BY season_start), '[]'::jsonb)
                    FROM by_season),
        'monthly', (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                        'month', month, 'n', n,
                        'pnl', ROUND(pnl::numeric, 3)) ORDER BY month), '[]'::jsonb)
                    FROM by_month)
    )
    INTO result
    FROM agg;

    RETURN result;
END;
$$ LANGUAGE plpgsql STABLE;


REVOKE ALL ON FUNCTION run_backtest_nba(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION refresh_bt_nba() FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON FUNCTION run_backtest_nba(jsonb) FROM anon, authenticated;
    REVOKE ALL ON FUNCTION refresh_bt_nba() FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
REVOKE ALL ON TABLE bt_nba   FROM PUBLIC;
REVOKE ALL ON TABLE nba_odds FROM PUBLIC;
