-- 074: the last games a Lab test would have bet on.
--
-- The Lab's result used to stop at totals: a yield, an interval, a curve. A
-- bettor reads "−2.1% on 3,412 bets" and has no picture of what was bet. This
-- returns the most recent selections of the same spec, so the page can show
-- "Bari 1-1 Palermo · 3.30 · won".
--
-- ⚠️ THE WHERE CLAUSE IS A COPY OF run_backtest()'s (live in the database; its
--    migration is on the Stage J branch, not on main). It is copied rather than
--    shared because run_backtest is one plpgsql CTE and splitting it would mean
--    redeploying the function every Lab result depends on. If a filter is
--    added there, add it here, or the list will show games the totals did not
--    count. The two are checked against each other by
--    `select (run_backtest(s)->>'n')::int` = the count of this query without
--    the LIMIT — see the bottom of this file.
--
-- Additive and read-only: STABLE, no table touched, nothing else calls it.

CREATE OR REPLACE FUNCTION public.run_backtest_recent(spec jsonb, lim int DEFAULT 8)
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
$function$;

COMMENT ON FUNCTION public.run_backtest_recent(jsonb, int) IS
  'The most recent selections of a Lab spec (same filters as run_backtest). Read-only.';

-- Check after applying (n must equal the unlimited count):
--   select (run_backtest(s)->>'n')::int,
--          jsonb_array_length(run_backtest_recent(s, 50))
--   from (select '{"market":"1x2","side":"draw","leagues":["ITA-SB"],"odds_min":3.3}'::jsonb s) x;
