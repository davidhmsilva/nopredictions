-- 062_lab_betfair_close.sql
-- The Lab's own match table: Pinnacle's close where Football-Data still
-- carries it, Betfair Exchange's close, net of commission, where it does not.
--
-- Why a new table and not bt_features:
--   * Football-Data stopped publishing Pinnacle. 2025-26 has no PSC* after
--     2026-01-15, and the 2026-27 files have no PS* columns at all, so
--     bt_features -- which requires a Pinnacle close -- ends there however
--     often it is refreshed. The Lab was testing theories without the season
--     being played.
--   * bt_features is also read by the strategy factory
--     (agent/factory/universes.py), which states outright that its price is
--     "the RAW Pinnacle close". Mixing a second source into it would change
--     that research silently. bt_features stays exactly as it was.
--
-- The Betfair side (Stage A: BFEX = the close, BF = the earlier Friday /
-- Tuesday snapshot) is stored net of COMMISSION on winnings:
--     net = 1 + (odds - 1) * (1 - 0.05)
-- so a 2.00 on the exchange is a 1.95 here -- the price actually received,
-- and the like-for-like against a bookmaker whose margin is inside its odds.
-- 5% is Betfair's standard market base rate; some accounts pay 2%, so this
-- is the conservative side. A match takes ONE source for all its prices,
-- never Pinnacle's 1X2 beside Betfair's total, so close_source says what
-- every price on the row is.
--
-- Refresh after Stage A:  SELECT refresh_bt_lab();

CREATE TABLE IF NOT EXISTS bt_lab_matches (LIKE bt_features INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES);
ALTER TABLE bt_lab_matches
    ADD COLUMN IF NOT EXISTS close_source TEXT NOT NULL DEFAULT 'pinnacle'
    CHECK (close_source IN ('pinnacle', 'betfair'));
CREATE INDEX IF NOT EXISTS idx_bt_lab_matches_source ON bt_lab_matches (close_source);

ALTER TABLE bt_lab_matches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE bt_lab_matches FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON TABLE bt_lab_matches FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

-- The earlier Betfair snapshot had no bookmaker of its own until Stage A
-- started storing it under the unused 'BF' code.
UPDATE bookmakers SET name = 'Betfair Exchange (pre-closing)' WHERE code = 'BF';


CREATE OR REPLACE FUNCTION refresh_bt_lab() RETURNS integer AS $$
DECLARE
    n integer;
    keep numeric := 1 - 0.05;          -- Betfair commission on winnings
BEGIN
    TRUNCATE bt_lab_matches;

    INSERT INTO bt_lab_matches (
        match_id, league_code, league_name, country, tier,
        season_label, season_start, kickoff_utc, home_team, away_team,
        home_score, away_score, ht_home, ht_away, total_goals, result,
        ph_close, pd_close, pa_close, over25_close, under25_close,
        ph_open, pd_open, pa_open, over25_open, under25_open,
        fav_side,
        home_rest_days, away_rest_days,
        home_form_pts5, away_form_pts5,
        home_avg_tg5, away_avg_tg5,
        close_source
    )
    WITH snap AS (
        -- One snapshot per match per book; odds of 1.00 or less are not prices.
        SELECT DISTINCT ON (mo.match_id, b.code)
               mo.match_id, b.code,
               CASE WHEN mo.home_odds      > 1 THEN mo.home_odds END      AS h,
               CASE WHEN mo.draw_odds      > 1 THEN mo.draw_odds END      AS d,
               CASE WHEN mo.away_odds      > 1 THEN mo.away_odds END      AS a,
               CASE WHEN mo.over_2_5_odds  > 1 THEN mo.over_2_5_odds END  AS o,
               CASE WHEN mo.under_2_5_odds > 1 THEN mo.under_2_5_odds END AS u
        FROM match_odds mo
        JOIN bookmakers b ON b.id = mo.bookmaker_id
        WHERE b.code IN ('PSC', 'PS', 'BFEX', 'BF')
        ORDER BY mo.match_id, b.code, mo.observed_at DESC
    ),
    px AS (
        SELECT m.id AS match_id,
               (pc.h IS NULL) AS bf,
               pc.h AS pch, pc.d AS pcd, pc.a AS pca, pc.o AS pco, pc.u AS pcu,
               po.h AS poh, po.d AS pod, po.a AS poa, po.o AS poo, po.u AS pou,
               bc.h AS bch, bc.d AS bcd, bc.a AS bca, bc.o AS bco, bc.u AS bcu,
               bo.h AS boh, bo.d AS bod, bo.a AS boa, bo.o AS boo, bo.u AS bou
        FROM matches m
        LEFT JOIN snap pc ON pc.match_id = m.id AND pc.code = 'PSC'
        LEFT JOIN snap po ON po.match_id = m.id AND po.code = 'PS'
        LEFT JOIN snap bc ON bc.match_id = m.id AND bc.code = 'BFEX'
        LEFT JOIN snap bo ON bo.match_id = m.id AND bo.code = 'BF'
        WHERE m.status = 'finished' AND m.home_score IS NOT NULL
          AND (pc.h IS NOT NULL OR bc.h IS NOT NULL)
    ),
    price AS (
        SELECT match_id,
               CASE WHEN bf THEN 'betfair' ELSE 'pinnacle' END AS src,
               CASE WHEN bf THEN ROUND(1 + (bch - 1) * keep, 3) ELSE pch END AS ph_close,
               CASE WHEN bf THEN ROUND(1 + (bcd - 1) * keep, 3) ELSE pcd END AS pd_close,
               CASE WHEN bf THEN ROUND(1 + (bca - 1) * keep, 3) ELSE pca END AS pa_close,
               CASE WHEN bf THEN ROUND(1 + (bco - 1) * keep, 3) ELSE pco END AS over25_close,
               CASE WHEN bf THEN ROUND(1 + (bcu - 1) * keep, 3) ELSE pcu END AS under25_close,
               CASE WHEN bf THEN ROUND(1 + (boh - 1) * keep, 3) ELSE poh END AS ph_open,
               CASE WHEN bf THEN ROUND(1 + (bod - 1) * keep, 3) ELSE pod END AS pd_open,
               CASE WHEN bf THEN ROUND(1 + (boa - 1) * keep, 3) ELSE poa END AS pa_open,
               CASE WHEN bf THEN ROUND(1 + (boo - 1) * keep, 3) ELSE poo END AS over25_open,
               CASE WHEN bf THEN ROUND(1 + (bou - 1) * keep, 3) ELSE pou END AS under25_open
        FROM px
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
           p.ph_close, p.pd_close, p.pa_close, p.over25_close, p.under25_close,
           p.ph_open, p.pd_open, p.pa_open, p.over25_open, p.under25_open,
           CASE WHEN p.ph_close < p.pa_close THEN 'H'
                WHEN p.pa_close < p.ph_close THEN 'A' END,
           hf.rest_days, af.rest_days,
           CASE WHEN hf.prev_n = 5 THEN hf.form_pts5 END,
           CASE WHEN af.prev_n = 5 THEN af.form_pts5 END,
           CASE WHEN hf.prev_n = 5 THEN ROUND(hf.avg_tg5, 3) END,
           CASE WHEN af.prev_n = 5 THEN ROUND(af.avg_tg5, 3) END,
           p.src
    FROM price p
    JOIN matches m   ON m.id = p.match_id
    JOIN seasons s   ON s.id = m.season_id
    JOIN leagues l   ON l.id = s.league_id AND l.code <> 'USA-NBA'
    JOIN teams th    ON th.id = m.home_team_id
    JOIN teams ta    ON ta.id = m.away_team_id
    LEFT JOIN tf hf  ON hf.match_id = m.id AND hf.team_id = m.home_team_id
    LEFT JOIN tf af  ON af.match_id = m.id AND af.team_id = m.away_team_id
    -- The same entry condition as bt_features (a home close), so the
    -- Pinnacle part of this table is bt_features row for row.
    WHERE p.ph_close IS NOT NULL;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$ LANGUAGE plpgsql;

REVOKE ALL ON FUNCTION refresh_bt_lab() FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON FUNCTION refresh_bt_lab() FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;


-- Filled here, so the table is complete before db/063 points run_backtest at
-- it: the Lab in production never reads an empty table.
SELECT refresh_bt_lab();
