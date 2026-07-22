-- 024_backtest_mvp.sql
-- Hypothesis Tester MVP: flattened per-match backtest features + run_backtest().
--
-- bt_features: one row per finished football match that has a Pinnacle closing
-- (PSC) 1X2 price. All derived columns are strictly pre-match (no lookahead):
-- rest days and form windows only look at matches that kicked off earlier.
--
-- Entry price convention (matches the CLV framework in CLAUDE.md):
--   *_close = Pinnacle (closing), code PSC  -> executable price / sharp benchmark
--   *_open  = Pinnacle (legacy), code PS    -> opening price, used for CLV
--   CLV per selection = open_odds / close_odds - 1  (positive = beat the close)
--
-- Refresh with:  SELECT refresh_bt_features();   (run after Stage A ingests)

CREATE TABLE IF NOT EXISTS bt_features (
    match_id        BIGINT PRIMARY KEY,
    league_code     TEXT        NOT NULL,   -- canonical leagues.code, e.g. 'ENG-PR'
    league_name     TEXT        NOT NULL,
    country         TEXT,
    tier            INTEGER,
    season_label    TEXT        NOT NULL,   -- '2023-24' or '2024'
    season_start    INTEGER,                -- 2023 for '2023-24'
    kickoff_utc     TIMESTAMPTZ NOT NULL,
    home_team       TEXT        NOT NULL,
    away_team       TEXT        NOT NULL,
    home_score      INTEGER     NOT NULL,
    away_score      INTEGER     NOT NULL,
    ht_home         INTEGER,
    ht_away         INTEGER,
    total_goals     INTEGER     NOT NULL,
    result          CHAR(1)     NOT NULL,   -- 'H' | 'D' | 'A'
    -- Pinnacle closing (PSC)
    ph_close        NUMERIC(8,3),
    pd_close        NUMERIC(8,3),
    pa_close        NUMERIC(8,3),
    over25_close    NUMERIC(8,3),
    under25_close   NUMERIC(8,3),
    -- Pinnacle opening (PS / legacy)
    ph_open         NUMERIC(8,3),
    pd_open         NUMERIC(8,3),
    pa_open         NUMERIC(8,3),
    over25_open     NUMERIC(8,3),
    under25_open    NUMERIC(8,3),
    fav_side        CHAR(1),                -- 'H' | 'A' by closing 1X2 (NULL if equal)
    -- pre-match features (NULL when insufficient history)
    home_rest_days  INTEGER,                -- days since team's previous match in DB
    away_rest_days  INTEGER,
    home_form_pts5  INTEGER,                -- points in team's previous 5 matches
    away_form_pts5  INTEGER,
    home_avg_tg5    NUMERIC(6,3),           -- avg total goals in team's previous 5
    away_avg_tg5    NUMERIC(6,3)
);

CREATE INDEX IF NOT EXISTS idx_bt_features_league  ON bt_features(league_code);
CREATE INDEX IF NOT EXISTS idx_bt_features_season  ON bt_features(season_start);
CREATE INDEX IF NOT EXISTS idx_bt_features_kickoff ON bt_features(kickoff_utc);


CREATE OR REPLACE FUNCTION refresh_bt_features() RETURNS integer AS $$
DECLARE
    n integer;
BEGIN
    TRUNCATE bt_features;

    INSERT INTO bt_features (
        match_id, league_code, league_name, country, tier,
        season_label, season_start, kickoff_utc, home_team, away_team,
        home_score, away_score, ht_home, ht_away, total_goals, result,
        ph_close, pd_close, pa_close, over25_close, under25_close,
        ph_open, pd_open, pa_open, over25_open, under25_open,
        fav_side,
        home_rest_days, away_rest_days,
        home_form_pts5, away_form_pts5,
        home_avg_tg5, away_avg_tg5
    )
    WITH pin_close AS (
        SELECT DISTINCT ON (mo.match_id)
               mo.match_id, mo.home_odds, mo.draw_odds, mo.away_odds,
               mo.over_2_5_odds, mo.under_2_5_odds
        FROM match_odds mo
        JOIN bookmakers b ON b.id = mo.bookmaker_id
        WHERE b.code = 'PSC'
        ORDER BY mo.match_id, mo.observed_at DESC
    ),
    pin_open AS (
        SELECT DISTINCT ON (mo.match_id)
               mo.match_id, mo.home_odds, mo.draw_odds, mo.away_odds,
               mo.over_2_5_odds, mo.under_2_5_odds
        FROM match_odds mo
        JOIN bookmakers b ON b.id = mo.bookmaker_id
        WHERE b.code = 'PS'
        ORDER BY mo.match_id, mo.observed_at ASC
    ),
    tm AS (
        SELECT m.id AS match_id, m.kickoff_utc, m.home_team_id AS team_id,
               CASE WHEN m.home_score > m.away_score THEN 3
                    WHEN m.home_score = m.away_score THEN 1 ELSE 0 END AS pts,
               m.home_score + m.away_score AS tg
        FROM matches m
        WHERE m.status = 'finished' AND m.home_score IS NOT NULL
        UNION ALL
        SELECT m.id, m.kickoff_utc, m.away_team_id,
               CASE WHEN m.away_score > m.home_score THEN 3
                    WHEN m.home_score = m.away_score THEN 1 ELSE 0 END,
               m.home_score + m.away_score
        FROM matches m
        WHERE m.status = 'finished' AND m.home_score IS NOT NULL
    ),
    tf AS (
        SELECT match_id, team_id,
               (kickoff_utc::date - LAG(kickoff_utc::date) OVER w)  AS rest_days,
               SUM(pts) OVER w5                                     AS form_pts5,
               AVG(tg)  OVER w5                                     AS avg_tg5,
               COUNT(*) OVER w5                                     AS prev_n
        FROM tm
        WINDOW w  AS (PARTITION BY team_id ORDER BY kickoff_utc, match_id),
               w5 AS (PARTITION BY team_id ORDER BY kickoff_utc, match_id
                      ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)
    )
    SELECT m.id, l.code, l.name, l.country, l.tier,
           s.label,
           NULLIF(regexp_replace(substring(s.label from 1 for 4), '\D', '', 'g'), '')::int,
           m.kickoff_utc, th.canonical_name, ta.canonical_name,
           m.home_score, m.away_score, m.home_score_ht, m.away_score_ht,
           m.home_score + m.away_score,
           CASE WHEN m.home_score > m.away_score THEN 'H'
                WHEN m.home_score = m.away_score THEN 'D' ELSE 'A' END,
           pc.home_odds, pc.draw_odds, pc.away_odds, pc.over_2_5_odds, pc.under_2_5_odds,
           po.home_odds, po.draw_odds, po.away_odds, po.over_2_5_odds, po.under_2_5_odds,
           CASE WHEN pc.home_odds < pc.away_odds THEN 'H'
                WHEN pc.away_odds < pc.home_odds THEN 'A' END,
           hf.rest_days, af.rest_days,
           CASE WHEN hf.prev_n = 5 THEN hf.form_pts5 END,
           CASE WHEN af.prev_n = 5 THEN af.form_pts5 END,
           CASE WHEN hf.prev_n = 5 THEN ROUND(hf.avg_tg5, 3) END,
           CASE WHEN af.prev_n = 5 THEN ROUND(af.avg_tg5, 3) END
    FROM matches m
    JOIN seasons s   ON s.id = m.season_id
    JOIN leagues l   ON l.id = s.league_id AND l.code <> 'USA-NBA'
    JOIN teams th    ON th.id = m.home_team_id
    JOIN teams ta    ON ta.id = m.away_team_id
    JOIN pin_close pc ON pc.match_id = m.id AND pc.home_odds IS NOT NULL
    LEFT JOIN pin_open po ON po.match_id = m.id
    LEFT JOIN tf hf  ON hf.match_id = m.id AND hf.team_id = m.home_team_id
    LEFT JOIN tf af  ON af.match_id = m.id AND af.team_id = m.away_team_id
    WHERE m.status = 'finished' AND m.home_score IS NOT NULL;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$ LANGUAGE plpgsql;


-- run_backtest(spec) — flat 1u stake backtest over bt_features.
--
-- spec fields (all optional except market+side):
--   market:  '1x2' | 'ou25'
--   side:    'home' | 'draw' | 'away'  (1x2)  |  'over' | 'under'  (ou25)
--   leagues: ["ENG-PR", ...]            canonical league codes
--   season_start, season_end:           int years (inclusive, on season start year)
--   odds_min, odds_max:                 decimal odds range on the backed side (closing)
--   fav_status: 'favorite' | 'underdog' (only for side home/away)
--   home_team, away_team:               substring match on canonical team name
--   home_rest_days_min/max, away_rest_days_min/max
--   home_form_pts5_min/max, away_form_pts5_min/max   (0..15)
--   home_avg_tg5_min/max,  away_avg_tg5_min/max      (avg total goals, last 5)
--
-- Returns jsonb: n, wins, pnl (units @ closing), pnl_sq, avg_odds,
--   n_open, pnl_open, clv_avg, first_match, last_match, seasons[], monthly[]

CREATE OR REPLACE FUNCTION run_backtest(spec jsonb) RETURNS jsonb AS $$
DECLARE
    v_side text := spec->>'side';
    result jsonb;
BEGIN
    IF v_side NOT IN ('home','draw','away','over','under') THEN
        RAISE EXCEPTION 'invalid side: %', v_side;
    END IF;

    WITH base AS (
        SELECT f.*,
            CASE v_side WHEN 'home' THEN f.ph_close
                        WHEN 'draw' THEN f.pd_close
                        WHEN 'away' THEN f.pa_close
                        WHEN 'over' THEN f.over25_close
                        WHEN 'under' THEN f.under25_close END AS oc,
            CASE v_side WHEN 'home' THEN f.ph_open
                        WHEN 'draw' THEN f.pd_open
                        WHEN 'away' THEN f.pa_open
                        WHEN 'over' THEN f.over25_open
                        WHEN 'under' THEN f.under25_open END AS oo,
            CASE v_side WHEN 'home' THEN f.result = 'H'
                        WHEN 'draw' THEN f.result = 'D'
                        WHEN 'away' THEN f.result = 'A'
                        WHEN 'over' THEN f.total_goals >= 3
                        WHEN 'under' THEN f.total_goals <= 2 END AS won
        FROM bt_features f
        WHERE (spec->'leagues' IS NULL OR jsonb_typeof(spec->'leagues') = 'null'
               OR f.league_code IN (SELECT jsonb_array_elements_text(spec->'leagues')))
          AND ((spec->>'season_start') IS NULL OR f.season_start >= (spec->>'season_start')::int)
          AND ((spec->>'season_end')   IS NULL OR f.season_start <= (spec->>'season_end')::int)
          AND ((spec->>'home_team') IS NULL OR f.home_team ILIKE '%' || (spec->>'home_team') || '%')
          AND ((spec->>'away_team') IS NULL OR f.away_team ILIKE '%' || (spec->>'away_team') || '%')
          AND (
                (spec->>'fav_status') IS NULL
                OR ((spec->>'fav_status') = 'favorite' AND
                    ((v_side = 'home' AND f.fav_side = 'H') OR (v_side = 'away' AND f.fav_side = 'A')))
                OR ((spec->>'fav_status') = 'underdog' AND
                    ((v_side = 'home' AND f.fav_side = 'A') OR (v_side = 'away' AND f.fav_side = 'H')))
              )
          AND ((spec->>'home_rest_days_min') IS NULL OR f.home_rest_days >= (spec->>'home_rest_days_min')::int)
          AND ((spec->>'home_rest_days_max') IS NULL OR f.home_rest_days <= (spec->>'home_rest_days_max')::int)
          AND ((spec->>'away_rest_days_min') IS NULL OR f.away_rest_days >= (spec->>'away_rest_days_min')::int)
          AND ((spec->>'away_rest_days_max') IS NULL OR f.away_rest_days <= (spec->>'away_rest_days_max')::int)
          AND ((spec->>'home_form_pts5_min') IS NULL OR f.home_form_pts5 >= (spec->>'home_form_pts5_min')::int)
          AND ((spec->>'home_form_pts5_max') IS NULL OR f.home_form_pts5 <= (spec->>'home_form_pts5_max')::int)
          AND ((spec->>'away_form_pts5_min') IS NULL OR f.away_form_pts5 >= (spec->>'away_form_pts5_min')::int)
          AND ((spec->>'away_form_pts5_max') IS NULL OR f.away_form_pts5 <= (spec->>'away_form_pts5_max')::int)
          AND ((spec->>'home_avg_tg5_min') IS NULL OR f.home_avg_tg5 >= (spec->>'home_avg_tg5_min')::numeric)
          AND ((spec->>'home_avg_tg5_max') IS NULL OR f.home_avg_tg5 <= (spec->>'home_avg_tg5_max')::numeric)
          AND ((spec->>'away_avg_tg5_min') IS NULL OR f.away_avg_tg5 >= (spec->>'away_avg_tg5_min')::numeric)
          AND ((spec->>'away_avg_tg5_max') IS NULL OR f.away_avg_tg5 <= (spec->>'away_avg_tg5_max')::numeric)
    ),
    sel AS (
        SELECT *,
               CASE WHEN won THEN oc - 1 ELSE -1 END AS pnl_c,
               CASE WHEN oo IS NOT NULL THEN CASE WHEN won THEN oo - 1 ELSE -1 END END AS pnl_o
        FROM base
        WHERE oc IS NOT NULL
          AND ((spec->>'odds_min') IS NULL OR oc >= (spec->>'odds_min')::numeric)
          AND ((spec->>'odds_max') IS NULL OR oc <= (spec->>'odds_max')::numeric)
    ),
    agg AS (
        SELECT COUNT(*)                                  AS n,
               COUNT(*) FILTER (WHERE won)               AS wins,
               COALESCE(SUM(pnl_c), 0)                   AS pnl,
               COALESCE(SUM(pnl_c * pnl_c), 0)           AS pnl_sq,
               AVG(oc)                                   AS avg_odds,
               COUNT(pnl_o)                              AS n_open,
               SUM(pnl_o)                                AS pnl_open,
               AVG(oo / oc - 1) FILTER (WHERE oo IS NOT NULL) AS clv_avg,
               MIN(kickoff_utc)                          AS first_match,
               MAX(kickoff_utc)                          AS last_match
        FROM sel
    ),
    by_season AS (
        SELECT season_start, COUNT(*) AS n,
               COUNT(*) FILTER (WHERE won) AS wins,
               SUM(pnl_c) AS pnl
        FROM sel GROUP BY season_start
    ),
    by_month AS (
        SELECT to_char(date_trunc('month', kickoff_utc), 'YYYY-MM') AS month,
               COUNT(*) AS n, SUM(pnl_c) AS pnl
        FROM sel GROUP BY 1
    )
    SELECT jsonb_build_object(
        'n', agg.n,
        'wins', agg.wins,
        'pnl', ROUND(agg.pnl::numeric, 3),
        'pnl_sq', ROUND(agg.pnl_sq::numeric, 4),
        'avg_odds', ROUND(agg.avg_odds::numeric, 3),
        'n_open', agg.n_open,
        'pnl_open', ROUND(agg.pnl_open::numeric, 3),
        'clv_avg', ROUND(agg.clv_avg::numeric, 5),
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

-- Server-side only: the Next.js API route calls this over the direct
-- Postgres connection. Never expose to the public PostgREST roles.
REVOKE ALL ON FUNCTION run_backtest(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION refresh_bt_features() FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON FUNCTION run_backtest(jsonb) FROM anon, authenticated;
    REVOKE ALL ON FUNCTION refresh_bt_features() FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
REVOKE ALL ON TABLE bt_features FROM PUBLIC;
