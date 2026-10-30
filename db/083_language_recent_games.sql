-- 083: what a rule can ask about a team's recent games, widened.
--
-- People write "after a loss", "unbeaten in 5", "strong at home", "a leaky
-- defence". A rule could only say rest days, points in the last 5 and total goals
-- in the last 5, so those ideas were either approximated or refused. Everything
-- here comes from results alone: no new price and no new source, and the runner
-- reads each figure live from ESPN by the same definition (`form_of` in
-- agent/lab_strategy_runner.py, parity checked against `matches`).
--
-- New on bt_features, each for the home and the away team, all strictly before
-- kick-off, league games only, NULL where the history cannot answer:
--
--   last_gd        its goal difference in its previous game ("after a loss" is
--                  <= -1, "after a draw" is 0, "a heavy defeat" <= -3)
--   avg_gf5/ga5    goals it scored / conceded a game in its previous 5
--   venue_pts5     points in its previous 5 games at this end: the home team's
--                  home games, the away team's away games
--   win_run        games in a row won, before this one
--   unbeaten_run   games in a row without defeat
--   loss_run       games in a row lost
--   winless_run    games in a row without a win
--
-- 🔑 A run is counted at most 10 games back (RUN_CAP, on both sides) and a longer
--    one reads as 10, so the runner never needs more than two seasons of a team.
--    A run the history ends inside of is NULL: three wins and nothing before them
--    may be a run of three or of thirty.
--
-- ⚠️ ORDER. A site that sends one of these keys to the old run_backtest gets a
--    result for a BROADER rule, silently; so does a runner that does not know it.
--      1. this file
--      2. SELECT refresh_bt_features();    -- run it off-peak: TRUNCATE + INSERT
--      3. the server pulls the runner
--      4. the site deploys
--
-- Additive: old specs carry none of these keys and read exactly as before.

ALTER TABLE bt_features
    ADD COLUMN IF NOT EXISTS home_avg_gf5     NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS away_avg_gf5     NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS home_avg_ga5     NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS away_avg_ga5     NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS home_venue_pts5  INTEGER,
    ADD COLUMN IF NOT EXISTS away_venue_pts5  INTEGER,
    ADD COLUMN IF NOT EXISTS home_last_gd     INTEGER,
    ADD COLUMN IF NOT EXISTS away_last_gd     INTEGER,
    ADD COLUMN IF NOT EXISTS home_win_run     INTEGER,
    ADD COLUMN IF NOT EXISTS away_win_run     INTEGER,
    ADD COLUMN IF NOT EXISTS home_unbeaten_run INTEGER,
    ADD COLUMN IF NOT EXISTS away_unbeaten_run INTEGER,
    ADD COLUMN IF NOT EXISTS home_loss_run    INTEGER,
    ADD COLUMN IF NOT EXISTS away_loss_run    INTEGER,
    ADD COLUMN IF NOT EXISTS home_winless_run INTEGER,
    ADD COLUMN IF NOT EXISTS away_winless_run INTEGER;


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
        home_avg_tg5, away_avg_tg5,
        home_avg_gf5, away_avg_gf5,
        home_avg_ga5, away_avg_ga5,
        home_venue_pts5, away_venue_pts5,
        home_last_gd, away_last_gd,
        home_win_run, away_win_run,
        home_unbeaten_run, away_unbeaten_run,
        home_loss_run, away_loss_run,
        home_winless_run, away_winless_run
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
        SELECT m.id AS match_id, m.kickoff_utc, m.home_team_id AS team_id, TRUE AS at_home,
               CASE WHEN m.home_score > m.away_score THEN 3
                    WHEN m.home_score = m.away_score THEN 1 ELSE 0 END AS pts,
               m.home_score + m.away_score AS tg,
               m.home_score AS gf, m.away_score AS ga
        FROM matches m
        WHERE m.status = 'finished' AND m.home_score IS NOT NULL
        UNION ALL
        SELECT m.id, m.kickoff_utc, m.away_team_id, FALSE,
               CASE WHEN m.away_score > m.home_score THEN 3
                    WHEN m.home_score = m.away_score THEN 1 ELSE 0 END,
               m.home_score + m.away_score,
               m.away_score, m.home_score
        FROM matches m
        WHERE m.status = 'finished' AND m.home_score IS NOT NULL
    ),
    tn AS (
        SELECT tm.*, ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY kickoff_utc, match_id) AS rn
        FROM tm
    ),
    tf AS (
        SELECT match_id, team_id,
               (kickoff_utc::date - LAG(kickoff_utc::date) OVER w)  AS rest_days,
               SUM(pts) OVER w5                                     AS form_pts5,
               AVG(tg)  OVER w5                                     AS avg_tg5,
               AVG(gf)  OVER w5                                     AS avg_gf5,
               AVG(ga)  OVER w5                                     AS avg_ga5,
               COUNT(*) OVER w5                                     AS prev_n,
               SUM(pts) OVER v5                                     AS venue_pts5,
               COUNT(*) OVER v5                                     AS venue_n,
               LAG(gf - ga) OVER w                                  AS last_gd,
               -- Games since the last one that broke the run, over the previous
               -- 10. No such game in a full frame is a run of 10 or more; in a
               -- short frame the history ended first, and the run is unknown.
               COUNT(*) OVER w10                                    AS run_n,
               rn - 1 - MAX(rn) FILTER (WHERE pts <> 3) OVER w10    AS win_run,
               rn - 1 - MAX(rn) FILTER (WHERE pts =  0) OVER w10    AS unbeaten_run,
               rn - 1 - MAX(rn) FILTER (WHERE pts <> 0) OVER w10    AS loss_run,
               rn - 1 - MAX(rn) FILTER (WHERE pts =  3) OVER w10    AS winless_run
        FROM tn
        WINDOW w   AS (PARTITION BY team_id ORDER BY kickoff_utc, match_id),
               w5  AS (PARTITION BY team_id ORDER BY kickoff_utc, match_id
                       ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
               v5  AS (PARTITION BY team_id, at_home ORDER BY kickoff_utc, match_id
                       ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
               w10 AS (PARTITION BY team_id ORDER BY kickoff_utc, match_id
                       ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
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
           CASE WHEN af.prev_n = 5 THEN ROUND(af.avg_tg5, 3) END,
           CASE WHEN hf.prev_n = 5 THEN ROUND(hf.avg_gf5, 3) END,
           CASE WHEN af.prev_n = 5 THEN ROUND(af.avg_gf5, 3) END,
           CASE WHEN hf.prev_n = 5 THEN ROUND(hf.avg_ga5, 3) END,
           CASE WHEN af.prev_n = 5 THEN ROUND(af.avg_ga5, 3) END,
           CASE WHEN hf.venue_n = 5 THEN hf.venue_pts5 END,
           CASE WHEN af.venue_n = 5 THEN af.venue_pts5 END,
           hf.last_gd, af.last_gd,
           COALESCE(hf.win_run,      CASE WHEN hf.run_n = 10 THEN 10 END),
           COALESCE(af.win_run,      CASE WHEN af.run_n = 10 THEN 10 END),
           COALESCE(hf.unbeaten_run, CASE WHEN hf.run_n = 10 THEN 10 END),
           COALESCE(af.unbeaten_run, CASE WHEN af.run_n = 10 THEN 10 END),
           COALESCE(hf.loss_run,     CASE WHEN hf.run_n = 10 THEN 10 END),
           COALESCE(af.loss_run,     CASE WHEN af.run_n = 10 THEN 10 END),
           COALESCE(hf.winless_run,  CASE WHEN hf.run_n = 10 THEN 10 END),
           COALESCE(af.winless_run,  CASE WHEN af.run_n = 10 THEN 10 END)
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


-- 🔑 The bounds on the figures above, in ONE place. run_backtest and
--    run_backtest_recent both call this, so the next condition is added here and
--    the list of games can never show what the totals did not count (db/074 had
--    to copy its WHERE clause). Numeric casts throughout: a model that writes
--    "after a 2.5" must get an empty result, not an engine error.
CREATE OR REPLACE FUNCTION bt_recent_games_fit(f bt_features, spec jsonb) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT ((spec->>'home_avg_gf5_min') IS NULL OR f.home_avg_gf5 >= (spec->>'home_avg_gf5_min')::numeric)
       AND ((spec->>'home_avg_gf5_max') IS NULL OR f.home_avg_gf5 <= (spec->>'home_avg_gf5_max')::numeric)
       AND ((spec->>'away_avg_gf5_min') IS NULL OR f.away_avg_gf5 >= (spec->>'away_avg_gf5_min')::numeric)
       AND ((spec->>'away_avg_gf5_max') IS NULL OR f.away_avg_gf5 <= (spec->>'away_avg_gf5_max')::numeric)
       AND ((spec->>'home_avg_ga5_min') IS NULL OR f.home_avg_ga5 >= (spec->>'home_avg_ga5_min')::numeric)
       AND ((spec->>'home_avg_ga5_max') IS NULL OR f.home_avg_ga5 <= (spec->>'home_avg_ga5_max')::numeric)
       AND ((spec->>'away_avg_ga5_min') IS NULL OR f.away_avg_ga5 >= (spec->>'away_avg_ga5_min')::numeric)
       AND ((spec->>'away_avg_ga5_max') IS NULL OR f.away_avg_ga5 <= (spec->>'away_avg_ga5_max')::numeric)
       AND ((spec->>'home_venue_pts5_min') IS NULL OR f.home_venue_pts5 >= (spec->>'home_venue_pts5_min')::numeric)
       AND ((spec->>'home_venue_pts5_max') IS NULL OR f.home_venue_pts5 <= (spec->>'home_venue_pts5_max')::numeric)
       AND ((spec->>'away_venue_pts5_min') IS NULL OR f.away_venue_pts5 >= (spec->>'away_venue_pts5_min')::numeric)
       AND ((spec->>'away_venue_pts5_max') IS NULL OR f.away_venue_pts5 <= (spec->>'away_venue_pts5_max')::numeric)
       AND ((spec->>'home_last_gd_min') IS NULL OR f.home_last_gd >= (spec->>'home_last_gd_min')::numeric)
       AND ((spec->>'home_last_gd_max') IS NULL OR f.home_last_gd <= (spec->>'home_last_gd_max')::numeric)
       AND ((spec->>'away_last_gd_min') IS NULL OR f.away_last_gd >= (spec->>'away_last_gd_min')::numeric)
       AND ((spec->>'away_last_gd_max') IS NULL OR f.away_last_gd <= (spec->>'away_last_gd_max')::numeric)
       AND ((spec->>'home_win_run_min') IS NULL OR f.home_win_run >= (spec->>'home_win_run_min')::numeric)
       AND ((spec->>'home_win_run_max') IS NULL OR f.home_win_run <= (spec->>'home_win_run_max')::numeric)
       AND ((spec->>'away_win_run_min') IS NULL OR f.away_win_run >= (spec->>'away_win_run_min')::numeric)
       AND ((spec->>'away_win_run_max') IS NULL OR f.away_win_run <= (spec->>'away_win_run_max')::numeric)
       AND ((spec->>'home_unbeaten_run_min') IS NULL OR f.home_unbeaten_run >= (spec->>'home_unbeaten_run_min')::numeric)
       AND ((spec->>'home_unbeaten_run_max') IS NULL OR f.home_unbeaten_run <= (spec->>'home_unbeaten_run_max')::numeric)
       AND ((spec->>'away_unbeaten_run_min') IS NULL OR f.away_unbeaten_run >= (spec->>'away_unbeaten_run_min')::numeric)
       AND ((spec->>'away_unbeaten_run_max') IS NULL OR f.away_unbeaten_run <= (spec->>'away_unbeaten_run_max')::numeric)
       AND ((spec->>'home_loss_run_min') IS NULL OR f.home_loss_run >= (spec->>'home_loss_run_min')::numeric)
       AND ((spec->>'home_loss_run_max') IS NULL OR f.home_loss_run <= (spec->>'home_loss_run_max')::numeric)
       AND ((spec->>'away_loss_run_min') IS NULL OR f.away_loss_run >= (spec->>'away_loss_run_min')::numeric)
       AND ((spec->>'away_loss_run_max') IS NULL OR f.away_loss_run <= (spec->>'away_loss_run_max')::numeric)
       AND ((spec->>'home_winless_run_min') IS NULL OR f.home_winless_run >= (spec->>'home_winless_run_min')::numeric)
       AND ((spec->>'home_winless_run_max') IS NULL OR f.home_winless_run <= (spec->>'home_winless_run_max')::numeric)
       AND ((spec->>'away_winless_run_min') IS NULL OR f.away_winless_run >= (spec->>'away_winless_run_min')::numeric)
       AND ((spec->>'away_winless_run_max') IS NULL OR f.away_winless_run <= (spec->>'away_winless_run_max')::numeric)
$$;

REVOKE ALL ON FUNCTION bt_recent_games_fit(bt_features, jsonb) FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON FUNCTION bt_recent_games_fit(bt_features, jsonb) FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;


-- The live run_backtest, with one line added: the bounds above.
CREATE OR REPLACE FUNCTION public.run_backtest(spec jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
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
          AND bt_recent_games_fit(f, spec)
    ),
    sel AS (
        -- Odds of 1.00 or less are not prices: Football-Data carries a few
        -- zeros (3 over/under closes in 2024-26), and one of them in a
        -- selection used to divide the CLV by zero and fail the whole test.
        SELECT *,
               CASE WHEN won THEN oc - 1 ELSE -1 END AS pnl_c,
               CASE WHEN oo > 1 THEN CASE WHEN won THEN oo - 1 ELSE -1 END END AS pnl_o
        FROM base
        WHERE oc > 1
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
               AVG(oo / oc - 1) FILTER (WHERE oo > 1)     AS clv_avg,
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
    ),
    vsel AS (
        SELECT v.venue, s.won, s.oc, s.pnl_c, s.kickoff_utc, v.payout,
               v.p_mid, v.p_exec,
               CASE WHEN v.p_mid BETWEEN 0.01 AND 0.99 AND abs(v.p_mid - 1 / s.oc) <= 0.20
                    THEN v.p_mid + v.fee_rate * v.p_mid * (1 - v.p_mid) END AS c_mid,
               CASE WHEN v.p_exec BETWEEN 0.01 AND 0.99 AND abs(v.p_exec - 1 / s.oc) <= 0.20
                    THEN v.p_exec + v.fee_rate * v.p_exec * (1 - v.p_exec) END AS c_exec
        FROM sel s
        JOIN bt_venue_prices v ON v.match_id = s.match_id AND v.side = v_side
    ),
    vpnl AS (
        SELECT *,
               payout / c_exec - 1 AS pnl_x,
               payout / c_mid - 1  AS pnl_m
        FROM vsel
    ),
    vagg AS (
        SELECT venue,
               COUNT(*)                                           AS listed,
               COUNT(c_exec)                                      AS n,
               COUNT(c_exec) FILTER (WHERE payout = 1)            AS wins,
               COALESCE(SUM(pnl_x), 0)                            AS pnl,
               COALESCE(SUM(pnl_x * pnl_x), 0)                    AS pnl_sq,
               AVG(1 / c_exec)                                    AS avg_odds,
               SUM(pnl_c) FILTER (WHERE c_exec IS NOT NULL)       AS pin_pnl,
               AVG(oc)    FILTER (WHERE c_exec IS NOT NULL)       AS pin_avg_odds,
               COUNT(c_mid)                                       AS n_mid,
               COUNT(c_mid) FILTER (WHERE payout = 1)             AS wins_mid,
               COALESCE(SUM(pnl_m), 0)                            AS pnl_mid,
               COALESCE(SUM(pnl_m * pnl_m), 0)                    AS pnl_mid_sq,
               AVG(1 / c_mid)                                     AS avg_odds_mid,
               SUM(pnl_c) FILTER (WHERE c_mid IS NOT NULL)        AS pin_pnl_mid,
               AVG(oc)    FILTER (WHERE c_mid IS NOT NULL)        AS pin_avg_odds_mid,
               COUNT(*) FILTER (WHERE payout IN (0, 1) AND (payout = 1) = won) AS agree,
               COUNT(*) FILTER (WHERE payout IN (0, 1))           AS compared,
               COUNT(*) FILTER (WHERE (p_mid IS NOT NULL AND c_mid IS NULL)
                                   OR (p_exec IS NOT NULL AND c_exec IS NULL)) AS dropped,
               MIN(kickoff_utc)                                   AS first_match,
               MAX(kickoff_utc)                                   AS last_match
        FROM vpnl GROUP BY venue
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
                    FROM by_month),
        'venues', (SELECT COALESCE(jsonb_object_agg(venue, jsonb_build_object(
                        'listed', listed,
                        'n', n, 'wins', wins,
                        'pnl', ROUND(pnl::numeric, 3),
                        'pnl_sq', ROUND(pnl_sq::numeric, 4),
                        'avg_odds', ROUND(avg_odds::numeric, 3),
                        'pin_pnl', ROUND(pin_pnl::numeric, 3),
                        'pin_avg_odds', ROUND(pin_avg_odds::numeric, 3),
                        'n_mid', n_mid, 'wins_mid', wins_mid,
                        'pnl_mid', ROUND(pnl_mid::numeric, 3),
                        'pnl_mid_sq', ROUND(pnl_mid_sq::numeric, 4),
                        'avg_odds_mid', ROUND(avg_odds_mid::numeric, 3),
                        'pin_pnl_mid', ROUND(pin_pnl_mid::numeric, 3),
                        'pin_avg_odds_mid', ROUND(pin_avg_odds_mid::numeric, 3),
                        'agree', agree, 'compared', compared, 'dropped', dropped,
                        'first_match', first_match, 'last_match', last_match)), '{}'::jsonb)
                    FROM vagg)
    )
    INTO result
    FROM agg;

    RETURN result;
END;
$function$
;

-- The live run_backtest_recent, with one line added: the bounds above.
CREATE OR REPLACE FUNCTION public.run_backtest_recent(spec jsonb, lim integer DEFAULT 8)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
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
          AND bt_recent_games_fit(f, spec)
    ),
    sel AS (
        SELECT * FROM base
        WHERE oc > 1
          AND ((spec->>'odds_min') IS NULL OR oc >= (spec->>'odds_min')::numeric)
          AND ((spec->>'odds_max') IS NULL OR oc <= (spec->>'odds_max')::numeric)
        ORDER BY kickoff_utc DESC, match_id DESC
        LIMIT GREATEST(1, LEAST(lim, 50))
    )
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'kickoff', kickoff_utc,
               'league', league_name,
               'home', home_team,
               'away', away_team,
               'home_score', home_score,
               'away_score', away_score,
               'odds', ROUND(oc::numeric, 2),
               'won', won,
               'pnl', ROUND((CASE WHEN won THEN oc - 1 ELSE -1 END)::numeric, 2)
           ) ORDER BY kickoff_utc DESC, match_id DESC), '[]'::jsonb)
    INTO result
    FROM sel;

    RETURN result;
END;
$function$
;

