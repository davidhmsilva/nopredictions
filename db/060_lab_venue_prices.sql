-- 060_lab_venue_prices.sql
-- The Lab's second price: the same selections, priced at Polymarket and
-- Kalshi instead of Pinnacle.
--
-- bt_venue_prices: one row per (match, venue, side) for the two markets the
-- Lab tests -- 1X2 and over/under 2.5 -- built from venue_market_history
-- (db/059, ingest/stage_j_venue_history.py). Refresh after Stage J:
--     SELECT refresh_bt_venue_prices();
--
-- Two prices per side, because they are different claims:
--   p_mid   the venue's closing MID. Nobody buys at the mid as a taker, so a
--           yield read off it is an upper bound, never a result.
--   p_exec  a price a taker could have paid at the close. Kalshi: the ask of
--           the last minute candle before kick-off, only on a real book
--           (spread <= 0.10; the 0.02/0.81 placeholder books are not one).
--           Polymarket: the last taker BUY of that side in the hour before
--           kick-off -- a price somebody actually paid -- or nothing.
-- Both are before the fee; run_backtest adds the market's own
-- fee_rate * p * (1 - p) per share on top.
--
-- `payout` is what the VENUE paid, not our result: that is the money a
-- trader would have received. run_backtest reports how often the two agree,
-- which doubles as the check on Stage J's join and its sides.

CREATE TABLE IF NOT EXISTS bt_venue_prices (
    match_id    BIGINT      NOT NULL,
    venue       TEXT        NOT NULL,
    side        TEXT        NOT NULL CHECK (side IN ('home', 'draw', 'away', 'over', 'under')),
    p_mid       NUMERIC(6,4),
    p_exec      NUMERIC(6,4),
    exec_at     TIMESTAMPTZ,
    fee_rate    NUMERIC(6,4) NOT NULL DEFAULT 0,
    payout      NUMERIC(6,4) NOT NULL,
    volume_usd  NUMERIC(16,2),
    market_id   TEXT        NOT NULL,
    PRIMARY KEY (match_id, venue, side)
);

ALTER TABLE bt_venue_prices ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE bt_venue_prices FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON TABLE bt_venue_prices FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;


CREATE OR REPLACE FUNCTION refresh_bt_venue_prices() RETURNS integer AS $$
DECLARE
    n integer;
BEGIN
    TRUNCATE bt_venue_prices;

    INSERT INTO bt_venue_prices (match_id, venue, side, p_mid, p_exec, exec_at,
                                 fee_rate, payout, volume_usd, market_id)
    WITH base AS (
        SELECT v.*,
               -- Kalshi's close is a real book only inside the placeholder cut
               (v.venue = 'kalshi' AND v.ask_close IS NOT NULL AND v.bid_close IS NOT NULL
                AND v.ask_close > v.bid_close AND v.ask_close - v.bid_close <= 0.10) AS kx_book
        FROM venue_market_history v
        WHERE v.match_id IS NOT NULL
          AND v.payout0 IS NOT NULL
          AND v.prices_status = 'ok'
    ),
    src AS (
        -- 1X2: outcome 0 of each leg is "this side wins"
        SELECT match_id, venue, subject_side AS side, mid_close AS p_mid,
               CASE WHEN venue = 'polymarket'
                         AND buy0_at >= kickoff_utc - interval '60 minutes' THEN buy0_price
                    WHEN kx_book THEN ask_close END AS p_exec,
               CASE WHEN venue = 'polymarket' THEN buy0_at ELSE close_at END AS exec_at,
               fee_rate, payout0 AS payout, volume_usd, market_id
        FROM base
        WHERE family = 'moneyline' AND subject_side IS NOT NULL
        UNION ALL
        -- over 2.5: outcome 0
        SELECT match_id, venue, 'over', mid_close,
               CASE WHEN venue = 'polymarket'
                         AND buy0_at >= kickoff_utc - interval '60 minutes' THEN buy0_price
                    WHEN kx_book THEN ask_close END,
               CASE WHEN venue = 'polymarket' THEN buy0_at ELSE close_at END,
               fee_rate, payout0, volume_usd, market_id
        FROM base
        WHERE family = 'totals' AND line = 2.5
        UNION ALL
        -- under 2.5: outcome 1, the complement -- its ask is 1 - the over's bid
        SELECT match_id, venue, 'under', 1 - mid_close,
               CASE WHEN venue = 'polymarket'
                         AND buy1_at >= kickoff_utc - interval '60 minutes' THEN buy1_price
                    WHEN kx_book THEN 1 - bid_close END,
               CASE WHEN venue = 'polymarket' THEN buy1_at ELSE close_at END,
               fee_rate, 1 - payout0, volume_usd, market_id
        FROM base
        WHERE family = 'totals' AND line = 2.5
    )
    -- A match listed twice on one venue keeps its busiest market.
    SELECT DISTINCT ON (match_id, venue, side)
           match_id, venue, side, p_mid, p_exec, exec_at, COALESCE(fee_rate, 0), payout,
           volume_usd, market_id
    FROM src
    WHERE p_mid IS NOT NULL OR p_exec IS NOT NULL
    ORDER BY match_id, venue, side, volume_usd DESC NULLS LAST, market_id;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$ LANGUAGE plpgsql;


-- run_backtest(spec) — db/024, plus `venues`, and odds <= 1 treated as missing.
--
-- venues: {polymarket: {...}, kalshi: {...}}, each over the selections that
-- venue listed with a usable price:
--   listed                         selections the venue had at all
--   n, wins, pnl, pnl_sq, avg_odds at p_exec + fee (what a taker paid)
--   n_mid, pnl_mid, pnl_mid_sq, avg_odds_mid     at p_mid + fee (upper bound)
--   pin_pnl, pin_avg_odds / pin_pnl_mid, pin_avg_odds_mid
--                                  the SAME games at Pinnacle's close -- the
--                                  only fair comparison, since the venues cover
--                                  2024-08 onward and Pinnacle 2012 onward
--   agree / compared               venue payout vs our own result
--   dropped                        prices more than 20pp from Pinnacle's
--                                  implied probability: a broken book or a
--                                  wrong join, never a price
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
$$ LANGUAGE plpgsql STABLE;

REVOKE ALL ON FUNCTION run_backtest(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION refresh_bt_venue_prices() FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON FUNCTION run_backtest(jsonb) FROM anon, authenticated;
    REVOKE ALL ON FUNCTION refresh_bt_venue_prices() FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
