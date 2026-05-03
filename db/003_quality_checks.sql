-- =============================================================================
-- Stage A — Data Quality Checks
-- Run these in Supabase SQL Editor after Stage A ingestion.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. OVERVIEW — total matches, seasons, teams
-- -----------------------------------------------------------------------------
SELECT
    (SELECT COUNT(*) FROM matches)           AS total_matches,
    (SELECT COUNT(*) FROM seasons)           AS total_seasons,
    (SELECT COUNT(*) FROM teams)             AS total_teams,
    (SELECT COUNT(*) FROM match_odds)        AS total_odds_rows,
    (SELECT COUNT(*) FROM match_stats)       AS total_stats_rows,
    (SELECT COUNT(*) FROM data_ingestion_log WHERE status = 'succeeded') AS successful_ingestions,
    (SELECT COUNT(*) FROM data_ingestion_log WHERE status = 'failed')    AS failed_ingestions;


-- -----------------------------------------------------------------------------
-- 2. MATCHES PER LEAGUE — expect 200–600 per top league per year
-- -----------------------------------------------------------------------------
SELECT
    l.code,
    l.name,
    l.country,
    COUNT(m.id)                          AS total_matches,
    COUNT(DISTINCT s.label)              AS seasons_with_data,
    MIN(m.kickoff_utc::date)             AS earliest_match,
    MAX(m.kickoff_utc::date)             AS latest_match
FROM leagues l
JOIN seasons s  ON s.league_id = l.id
JOIN matches m  ON m.season_id = s.id
GROUP BY l.id, l.code, l.name, l.country
ORDER BY l.country, l.tier, l.code;


-- -----------------------------------------------------------------------------
-- 3. MATCHES PER LEAGUE PER SEASON — spot missing seasons or thin data
-- -----------------------------------------------------------------------------
SELECT
    l.code        AS league,
    s.label       AS season,
    COUNT(m.id)   AS matches
FROM leagues l
JOIN seasons s ON s.league_id = l.id
JOIN matches m ON m.season_id = s.id
GROUP BY l.code, s.label
ORDER BY l.code, s.label;


-- -----------------------------------------------------------------------------
-- 4. ODDS COVERAGE — how many matches have Pinnacle closing (our sharp ref)
-- -----------------------------------------------------------------------------
SELECT
    l.code                                                          AS league,
    COUNT(DISTINCT m.id)                                            AS total_matches,
    COUNT(DISTINCT mo.match_id)                                     AS matches_with_psc_odds,
    ROUND(COUNT(DISTINCT mo.match_id) * 100.0
          / NULLIF(COUNT(DISTINCT m.id), 0), 1)                    AS psc_coverage_pct
FROM leagues l
JOIN seasons s  ON s.league_id = l.id
JOIN matches m  ON m.season_id = s.id
LEFT JOIN match_odds mo
    ON mo.match_id = m.id
    AND mo.bookmaker_id = (SELECT id FROM bookmakers WHERE code = 'PSC')
    AND mo.snapshot_type = 'closing'
GROUP BY l.code
ORDER BY psc_coverage_pct DESC;


-- -----------------------------------------------------------------------------
-- 5. ODDS COVERAGE BY BOOKMAKER — across all matches
-- -----------------------------------------------------------------------------
SELECT
    b.code                                                          AS bookmaker,
    b.name,
    b.is_sharp,
    COUNT(DISTINCT mo.match_id)                                     AS matches_covered,
    ROUND(COUNT(DISTINCT mo.match_id) * 100.0
          / NULLIF((SELECT COUNT(*) FROM matches), 0), 1)          AS pct_of_all_matches
FROM bookmakers b
LEFT JOIN match_odds mo ON mo.bookmaker_id = b.id
GROUP BY b.id, b.code, b.name, b.is_sharp
ORDER BY matches_covered DESC;


-- -----------------------------------------------------------------------------
-- 6. MATCH STATS COVERAGE — shots, corners etc (FD has these for most leagues)
-- -----------------------------------------------------------------------------
SELECT
    l.code                                                          AS league,
    COUNT(DISTINCT m.id)                                            AS total_matches,
    COUNT(DISTINCT ms.match_id)                                     AS matches_with_stats,
    ROUND(COUNT(DISTINCT ms.match_id) * 100.0
          / NULLIF(COUNT(DISTINCT m.id), 0), 1)                    AS stats_coverage_pct,
    -- spot which fields are mostly null
    ROUND(SUM(CASE WHEN ms.home_shots IS NOT NULL THEN 1 ELSE 0 END) * 100.0
          / NULLIF(COUNT(DISTINCT ms.match_id), 0), 0)             AS shots_pct,
    ROUND(SUM(CASE WHEN ms.home_corners IS NOT NULL THEN 1 ELSE 0 END) * 100.0
          / NULLIF(COUNT(DISTINCT ms.match_id), 0), 0)             AS corners_pct
FROM leagues l
JOIN seasons s  ON s.league_id = l.id
JOIN matches m  ON m.season_id = s.id
LEFT JOIN match_stats ms ON ms.match_id = m.id
GROUP BY l.code
ORDER BY stats_coverage_pct DESC;


-- -----------------------------------------------------------------------------
-- 7. SCORE SANITY — unusual scorelines, nulls, duplicates
-- -----------------------------------------------------------------------------

-- Matches with NULL scores (should be 0 — all FD matches have results)
SELECT COUNT(*) AS matches_with_null_score
FROM matches
WHERE home_score IS NULL OR away_score IS NULL;

-- Top 20 highest-scoring matches (data corruption check)
SELECT
    l.code,
    s.label,
    ht.canonical_name  AS home_team,
    at.canonical_name  AS away_team,
    m.home_score,
    m.away_score,
    m.home_score + m.away_score AS total_goals,
    m.kickoff_utc::date
FROM matches m
JOIN seasons s  ON s.id = m.season_id
JOIN leagues l  ON l.id = s.league_id
JOIN teams ht   ON ht.id = m.home_team_id
JOIN teams at   ON at.id = m.away_team_id
ORDER BY total_goals DESC
LIMIT 20;

-- Matches where home_score = away_score = 0 in suspicious volume
SELECT
    l.code,
    COUNT(*) AS scoreless_draws
FROM matches m
JOIN seasons s ON s.id = m.season_id
JOIN leagues l ON l.id = s.league_id
WHERE m.home_score = 0 AND m.away_score = 0
GROUP BY l.code
ORDER BY scoreless_draws DESC;


-- -----------------------------------------------------------------------------
-- 8. SEASON COMPLETENESS — flag seasons with far fewer matches than expected
--    (Premier League should have 380 per season)
-- -----------------------------------------------------------------------------
WITH expected AS (
    SELECT code,
           CASE
               WHEN code IN ('ENG-PR','GER-BL1','ITA-SA','ESP-LL','FRA-L1') THEN 300
               WHEN code LIKE 'SCO%' THEN 100
               ELSE 150
           END AS min_expected
    FROM leagues
)
SELECT
    l.code,
    s.label,
    COUNT(m.id)      AS match_count,
    e.min_expected,
    CASE WHEN COUNT(m.id) < e.min_expected THEN '⚠ LOW' ELSE 'OK' END AS flag
FROM leagues l
JOIN expected e  ON e.code = l.code
JOIN seasons s   ON s.league_id = l.id
JOIN matches m   ON m.season_id = s.id
GROUP BY l.code, s.label, e.min_expected
HAVING COUNT(m.id) < e.min_expected
ORDER BY l.code, s.label;


-- -----------------------------------------------------------------------------
-- 9. INGESTION LOG — see what failed (if anything)
-- -----------------------------------------------------------------------------
SELECT
    source,
    resource,
    status,
    rows_ingested,
    error_message,
    started_at::date
FROM data_ingestion_log
WHERE status = 'failed'
ORDER BY started_at DESC;

-- Summary by status
SELECT status, COUNT(*) AS count, SUM(rows_ingested) AS total_rows
FROM data_ingestion_log
GROUP BY status;


-- -----------------------------------------------------------------------------
-- 10. RECENT INGESTION — last 10 resources processed
-- -----------------------------------------------------------------------------
SELECT
    resource,
    status,
    rows_ingested,
    finished_at - started_at AS duration
FROM data_ingestion_log
ORDER BY started_at DESC
LIMIT 10;
