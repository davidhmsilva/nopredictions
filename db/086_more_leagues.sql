-- 086_more_leagues.sql — 11 more leagues in the backtest (MLS, Argentina, Brasileirão,
-- Liga MX, J1, Allsvenskan, Eliteserien, Russia, China, Denmark, Austria).
--
-- Stage K holds their results and Pinnacle closes since 2012. Measured on 2026-10-31:
-- Polymarket lists all 11 with books the runner accepts near kick-off, and ESPN's feed
-- is current on each (`leagues[0].season.year`, not the 200).
--
-- ⚠️ These rows have a 1X2 close and nothing else: no Over/Under, no opening price.
--    So an Over/Under test over "all leagues" keeps its 22 leagues and its old answer,
--    the runner will not buy Over/Under on these (NO_OU_HISTORY), and CLV over "all
--    leagues" is read from the old leagues only.
-- ⚠️ A 1X2 test over "all leagues" counts them, so its answer changes. A rule naming its
--    leagues does not. Every running rule names them (checked 2026-10-31).
--
-- Only the league list in refresh_bt_features changes. After applying, off-peak:
--     SELECT refresh_bt_features();    -- must return 145036; a test waits while it runs

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
    -- The Lab's leagues, by name. Six more hold closes and stay out on purpose:
    -- ROU-L1, SUI-SL, IRL-PD, FIN-VL and POL-EK because ESPN cannot confirm a side
    -- there (frozen seasons, or 504), so "all leagues" would count bets the runner
    -- cannot place; ARG-CLP because the competition ended in 2024. A new league joins
    -- LEAGUES, PM_COMPETITIONS and this list together.
    JOIN leagues l   ON l.id = s.league_id AND l.code IN (
        'ENG-PR', 'ENG-CH', 'ENG-L1', 'ENG-L2', 'ENG-CON',
        'ESP-LL', 'ESP-L2', 'ITA-SA', 'ITA-SB', 'GER-BL1', 'GER-BL2',
        'FRA-L1', 'FRA-L2', 'NED-ED', 'POR-PL', 'BEL-JPL', 'TUR-SL', 'GRE-SL',
        'SCO-PR', 'SCO-CH', 'SCO-L1', 'SCO-L2',
        'USA-MLS', 'ARG-PD', 'BRA-SA', 'MEX-LMX', 'JPN-J1', 'SWE-AL', 'NOR-EL',
        'RUS-PL', 'CHN-CSL', 'DEN-SL', 'AUT-BL')
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
