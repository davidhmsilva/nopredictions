-- 085: both teams to score and over/under 1.5, 3.5 and 4.5 goals, pre-match.
--
-- Polymarket lists all of them on every fixture in the Lab's leagues and, over a
-- year, the other goal lines trade about 1.4x O/U 2.5. run_backtest cannot replay
-- them: bt_features has Pinnacle's 1X2 and O/U 2.5 only. venue_market_history
-- (db/059 on feat/venue-history-lab, live) has what takers really paid, so these
-- replay at Polymarket's OWN prices, over about a year, not 14 years at the close.
--
-- 🔑 NOT joined to bt_features for the figures. bt_features holds only games with a
--    Pinnacle close, and Football-Data stopped carrying those on 2026-01-15, so that
--    join would quietly drop 9 of the 12 months. The figures are computed here from
--    `matches`, by the same definitions (db/083; parity checked on the overlap).
--
-- One row per (match, market, side): a price the runner could really have paid.
--
--   price      the last taker BUY of this side in the hour before kick-off, before
--              the fee. A price somebody paid, or no row at all. The same hour as
--              bt_venue_prices (db/060); the runner enters in the last 45 minutes.
--   cost       price + fee_rate * price * (1 - price), the market's own schedule:
--              no fee before ~2026-03, then 0.03, then 0.05.
--   mid        the book mid at the close, for this side. 🔑 Not a price anyone paid.
--              It is reported only as a ceiling, and it is here as a check: a fill
--              more than 5pp from it is not a price the runner's spread gate would
--              have bought at (MAX_SPREAD, agent/lab_strategy_runner.py).
--   won        what the VENUE paid, which is what settles a live trade.
--   agrees     did our own result say the same? False means the link to `matches`
--              is suspect, and with it every figure on the row, so the replay skips
--              it. 0 of 16,652 markets disagreed on 2026-10-31.
--
-- Also dropped, each a gate the runner applies: a price outside 0.02-0.98. Not
-- replayable: the runner's $50 of ask depth, because a past book is not on record.
-- A voided market (payout 0.5) is not a bet: 0 in these leagues.
--
-- 21 of the Lab's 33 leagues. The other 12 are held out by name, here, on the site
-- (VENUE_NO_HISTORY) and in the runner (NO_VENUE_HISTORY), so "all leagues" never counts
-- a bet the runner would refuse. The Scottish Championship, League One and League Two
-- were never on the board. J1, Allsvenskan, Eliteserien, Russia, China, Denmark and
-- Austria are on it, but Stage J linked none of their markets, and priced only linked
-- ones. MLS (9 bets) and the Brasileirão (34) are under 50 bets over all eight sides,
-- which is not a test of a league. Argentina (53) and Greece (57) just clear it, and a
-- page's first and last dates say how little that is. Liga MX has 723, up to May 2026.
--
-- Side and line come from venue_market_history's outcome labels and `line`, never
-- from the position of an outcome or the text of a question.
--
-- ⚠️ ORDER. Nothing reads this table until the site deploys.
--      1. this file
--      2. SELECT refresh_bt_venue_lines();      -- ~20s; readers keep the old rows
--                                                  until it commits
--      3. the server pulls the runner, then the site deploys
--    The daily Stage J job (np-stage-j.timer) calls the refresh from then on.
--
-- Additive. Touches no existing table or function.

CREATE TABLE IF NOT EXISTS bt_venue_lines (
    match_id          BIGINT      NOT NULL,
    market            TEXT        NOT NULL CHECK (market IN ('btts', 'ou15', 'ou35', 'ou45')),
    side              TEXT        NOT NULL CHECK (side IN ('yes', 'no', 'over', 'under')),
    market_id         TEXT        NOT NULL,   -- PM conditionId
    league_code       TEXT        NOT NULL,
    league_name       TEXT        NOT NULL,
    season_start      INTEGER,
    kickoff_utc       TIMESTAMPTZ NOT NULL,   -- the venue's, already corrected by Stage J
    home_team         TEXT        NOT NULL,
    away_team         TEXT        NOT NULL,
    home_score        INTEGER     NOT NULL,
    away_score        INTEGER     NOT NULL,
    price             NUMERIC(6,4) NOT NULL,
    price_at          TIMESTAMPTZ NOT NULL,
    fee_rate          NUMERIC(6,4) NOT NULL,
    cost              NUMERIC(8,6) NOT NULL,
    mid               NUMERIC(6,4) NOT NULL,
    cost_mid          NUMERIC(8,6) NOT NULL,
    won               BOOLEAN     NOT NULL,
    agrees            BOOLEAN     NOT NULL,
    home_rest_days    INTEGER,
    away_rest_days    INTEGER,
    home_form_pts5    INTEGER,
    away_form_pts5    INTEGER,
    home_avg_tg5      NUMERIC(6,3),
    away_avg_tg5      NUMERIC(6,3),
    home_avg_gf5      NUMERIC(6,3),
    away_avg_gf5      NUMERIC(6,3),
    home_avg_ga5      NUMERIC(6,3),
    away_avg_ga5      NUMERIC(6,3),
    home_venue_pts5   INTEGER,
    away_venue_pts5   INTEGER,
    home_last_gd      INTEGER,
    away_last_gd      INTEGER,
    home_win_run      INTEGER,
    away_win_run      INTEGER,
    home_unbeaten_run INTEGER,
    away_unbeaten_run INTEGER,
    home_loss_run     INTEGER,
    away_loss_run     INTEGER,
    home_winless_run  INTEGER,
    away_winless_run  INTEGER,
    PRIMARY KEY (match_id, market, side)
);

CREATE INDEX IF NOT EXISTS idx_bt_venue_lines_market ON bt_venue_lines (market, side);

-- Research data, read by the site over DATABASE_URL only. Same posture as db/045
-- and db/059: RLS on, no policies, nothing granted to the client roles.
ALTER TABLE bt_venue_lines ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE bt_venue_lines FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON TABLE bt_venue_lines FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;


CREATE OR REPLACE FUNCTION refresh_bt_venue_lines() RETURNS integer AS $$
DECLARE
    n integer;
BEGIN
    -- DELETE, not TRUNCATE: a test running now keeps reading the old rows.
    DELETE FROM bt_venue_lines;

    INSERT INTO bt_venue_lines (
        match_id, market, side, market_id, league_code, league_name, season_start, kickoff_utc,
        home_team, away_team, home_score, away_score,
        price, price_at, fee_rate, cost, mid, cost_mid, won, agrees,
        home_rest_days, away_rest_days, home_form_pts5, away_form_pts5,
        home_avg_tg5, away_avg_tg5, home_avg_gf5, away_avg_gf5, home_avg_ga5, away_avg_ga5,
        home_venue_pts5, away_venue_pts5, home_last_gd, away_last_gd,
        home_win_run, away_win_run, home_unbeaten_run, away_unbeaten_run,
        home_loss_run, away_loss_run, home_winless_run, away_winless_run
    )
    WITH
    -- ⚠️ tm, tn and tf are refresh_bt_features' (db/083), word for word but for the
    --    start date. A figure changed there changes here, or a live rule and its
    --    replay part ways.
    -- 🔑 The start date is not only speed. A club back after years in a division we
    --    do not hold has a "last 5" that old without it: Cottbus in August 2026 read
    --    4,473 days of rest. With it those figures are NULL and fail the rule, as they
    --    do for the runner, which reads ESPN and has no such history. 53 of 3,664
    --    fixtures change; the 616 that overlap bt_features have 0 differences.
    tm AS (
        SELECT m.id AS match_id, m.kickoff_utc, m.home_team_id AS team_id, TRUE AS at_home,
               CASE WHEN m.home_score > m.away_score THEN 3
                    WHEN m.home_score = m.away_score THEN 1 ELSE 0 END AS pts,
               m.home_score + m.away_score AS tg,
               m.home_score AS gf, m.away_score AS ga
        FROM matches m
        WHERE m.status = 'finished' AND m.home_score IS NOT NULL
          AND m.kickoff_utc >= timestamptz '2023-07-01'
        UNION ALL
        SELECT m.id, m.kickoff_utc, m.away_team_id, FALSE,
               CASE WHEN m.away_score > m.home_score THEN 3
                    WHEN m.home_score = m.away_score THEN 1 ELSE 0 END,
               m.home_score + m.away_score,
               m.away_score, m.home_score
        FROM matches m
        WHERE m.status = 'finished' AND m.home_score IS NOT NULL
          AND m.kickoff_utc >= timestamptz '2023-07-01'
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
    ),
    mkt AS (
        SELECT v.*,
               CASE WHEN v.family = 'btts' THEN 'btts'
                    WHEN v.line = 1.5 THEN 'ou15'
                    WHEN v.line = 3.5 THEN 'ou35'
                    WHEN v.line = 4.5 THEN 'ou45' END AS market
        FROM venue_market_history v
        WHERE v.venue = 'polymarket'
          AND v.match_id IS NOT NULL
          AND v.prices_status = 'ok'
          AND v.payout0 IN (0, 1)
          AND (v.family = 'btts' OR (v.family = 'totals' AND v.line IN (1.5, 3.5, 4.5)))
    ),
    -- Everything on a venue row is in outcome 0's terms. Which outcome is which
    -- comes from its label.
    sided AS (
        SELECT mkt.*, lower(outcome0) AS side, buy0_price AS price, buy0_at AS price_at,
               mid_close AS side_mid, payout0 = 1 AS side_won
        FROM mkt
        UNION ALL
        SELECT mkt.*, lower(outcome1), buy1_price, buy1_at, 1 - mid_close, payout0 = 0
        FROM mkt
    ),
    paid AS (
        SELECT sided.*, COALESCE(fee_rate, 0) AS fee
        FROM sided
        WHERE ((market = 'btts' AND side IN ('yes', 'no'))
               OR (market <> 'btts' AND side IN ('over', 'under')))
          AND price > 0.02 AND price < 0.98
          AND price_at >= kickoff_utc - interval '60 minutes'
          AND price_at < kickoff_utc
          AND side_mid IS NOT NULL AND side_mid BETWEEN 0.01 AND 0.99
          AND abs(price - side_mid) <= 0.05
    )
    -- A match listed twice keeps its busiest market, as bt_venue_prices does.
    SELECT DISTINCT ON (m.id, p.market, p.side)
           m.id, p.market, p.side, p.market_id, l.code, l.name,
           NULLIF(regexp_replace(substring(s.label from 1 for 4), '\D', '', 'g'), '')::int,
           p.kickoff_utc, th.canonical_name, ta.canonical_name, m.home_score, m.away_score,
           p.price, p.price_at, p.fee,
           p.price + p.fee * p.price * (1 - p.price),
           p.side_mid,
           p.side_mid + p.fee * p.side_mid * (1 - p.side_mid),
           p.side_won,
           p.side_won = CASE p.side
               WHEN 'yes'   THEN m.home_score > 0 AND m.away_score > 0
               WHEN 'no'    THEN NOT (m.home_score > 0 AND m.away_score > 0)
               WHEN 'over'  THEN m.home_score + m.away_score > p.line
               WHEN 'under' THEN m.home_score + m.away_score < p.line END,
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
    FROM paid p
    JOIN matches m   ON m.id = p.match_id AND m.status = 'finished' AND m.home_score IS NOT NULL
    JOIN seasons s   ON s.id = m.season_id
    -- Whichever leagues the Lab covers, so a league added there arrives here on its
    -- next refresh, but for those with no usable history (see the top). A league leaves
    -- this list together with the other two.
    JOIN leagues l   ON l.id = s.league_id
                    AND l.code IN (SELECT DISTINCT league_code FROM bt_features)
                    AND l.code <> ALL (ARRAY['SCO-CH', 'SCO-L1', 'SCO-L2', 'USA-MLS', 'BRA-SA',
                                             'JPN-J1', 'SWE-AL', 'NOR-EL', 'RUS-PL', 'CHN-CSL',
                                             'DEN-SL', 'AUT-BL'])
    JOIN teams th    ON th.id = m.home_team_id
    JOIN teams ta    ON ta.id = m.away_team_id
    LEFT JOIN tf hf  ON hf.match_id = m.id AND hf.team_id = m.home_team_id
    LEFT JOIN tf af  ON af.match_id = m.id AND af.team_id = m.away_team_id
    -- matches.kickoff_utc is UK local time labelled UTC, so it is never EARLIER than
    -- the real kick-off. A venue start later than that is a stale listing, and its
    -- "last price before kick-off" could be from inside the match. 0 on 2026-10-31.
    WHERE p.kickoff_utc <= m.kickoff_utc
    ORDER BY m.id, p.market, p.side, p.volume_usd DESC NULLS LAST, p.market_id;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
-- The planner has no row counts for a CTE, reads this one as a few rows, and loops
-- over all of it for every bet: 172s against 16s with hash joins, on 2026-10-31.
$$ LANGUAGE plpgsql SET enable_nestloop = off;


-- 🔑 Every condition that is not about the price, in ONE place: the totals and the
--    list of games both read this, so the list can never show what the totals did
--    not count (db/074 had to copy its WHERE clause).
--
--    The recent-game bounds are bt_recent_games_fit's own: same names, same reading.
--    A column it learns before this table does reads as NULL and fails the bound, so
--    the replay comes back empty rather than testing a broader rule.
--
--    Favourite and underdog need a backed team, and these markets back none: a spec
--    asking for one gets no rows, as an over 2.5 does in run_backtest.
CREATE OR REPLACE FUNCTION bt_venue_fit(l bt_venue_lines, spec jsonb) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT (spec->'leagues' IS NULL OR jsonb_typeof(spec->'leagues') = 'null'
            OR l.league_code IN (SELECT jsonb_array_elements_text(spec->'leagues')))
       AND ((spec->>'season_start') IS NULL OR l.season_start >= (spec->>'season_start')::numeric)
       AND ((spec->>'season_end')   IS NULL OR l.season_start <= (spec->>'season_end')::numeric)
       AND ((spec->>'home_team') IS NULL OR l.home_team ILIKE '%' || (spec->>'home_team') || '%')
       AND ((spec->>'away_team') IS NULL OR l.away_team ILIKE '%' || (spec->>'away_team') || '%')
       AND (spec->>'fav_status') IS NULL
       AND ((spec->>'home_rest_days_min') IS NULL OR l.home_rest_days >= (spec->>'home_rest_days_min')::numeric)
       AND ((spec->>'home_rest_days_max') IS NULL OR l.home_rest_days <= (spec->>'home_rest_days_max')::numeric)
       AND ((spec->>'away_rest_days_min') IS NULL OR l.away_rest_days >= (spec->>'away_rest_days_min')::numeric)
       AND ((spec->>'away_rest_days_max') IS NULL OR l.away_rest_days <= (spec->>'away_rest_days_max')::numeric)
       AND ((spec->>'home_form_pts5_min') IS NULL OR l.home_form_pts5 >= (spec->>'home_form_pts5_min')::numeric)
       AND ((spec->>'home_form_pts5_max') IS NULL OR l.home_form_pts5 <= (spec->>'home_form_pts5_max')::numeric)
       AND ((spec->>'away_form_pts5_min') IS NULL OR l.away_form_pts5 >= (spec->>'away_form_pts5_min')::numeric)
       AND ((spec->>'away_form_pts5_max') IS NULL OR l.away_form_pts5 <= (spec->>'away_form_pts5_max')::numeric)
       AND ((spec->>'home_avg_tg5_min') IS NULL OR l.home_avg_tg5 >= (spec->>'home_avg_tg5_min')::numeric)
       AND ((spec->>'home_avg_tg5_max') IS NULL OR l.home_avg_tg5 <= (spec->>'home_avg_tg5_max')::numeric)
       AND ((spec->>'away_avg_tg5_min') IS NULL OR l.away_avg_tg5 >= (spec->>'away_avg_tg5_min')::numeric)
       AND ((spec->>'away_avg_tg5_max') IS NULL OR l.away_avg_tg5 <= (spec->>'away_avg_tg5_max')::numeric)
       AND bt_recent_games_fit(jsonb_populate_record(NULL::bt_features, to_jsonb(l)), spec)
       -- Bounded on the price before the fee, because that is what the runner reads
       -- off the book (book_verdict).
       AND ((spec->>'odds_min') IS NULL OR 1 / l.price >= (spec->>'odds_min')::numeric)
       AND ((spec->>'odds_max') IS NULL OR 1 / l.price <= (spec->>'odds_max')::numeric)
$$;


-- RawBacktest's shape (site/app/lib/backtest.ts), plus the mid as a ceiling. A bet is
-- bought at the last price before kick-off, so there is no later price to measure
-- closing-line value against, and no opening price in this table: n_open is 0.
CREATE OR REPLACE FUNCTION run_backtest_venue(spec jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_market text := spec->>'market';
    v_side   text := spec->>'side';
    result   jsonb;
BEGIN
    IF NOT ((v_market = 'btts' AND v_side IN ('yes', 'no'))
            OR (v_market IN ('ou15', 'ou35', 'ou45') AND v_side IN ('over', 'under'))) THEN
        RAISE EXCEPTION 'invalid market and side: % %', v_market, v_side;
    END IF;

    WITH sel AS (
        SELECT l.*,
               CASE WHEN l.won THEN 1 / l.cost - 1 ELSE -1 END     AS pnl_x,
               CASE WHEN l.won THEN 1 / l.cost_mid - 1 ELSE -1 END AS pnl_m
        FROM bt_venue_lines l
        WHERE l.market = v_market AND l.side = v_side AND l.agrees
          AND bt_venue_fit(l, spec)
    ),
    agg AS (
        SELECT COUNT(*)                        AS n,
               COUNT(*) FILTER (WHERE won)     AS wins,
               COALESCE(SUM(pnl_x), 0)         AS pnl,
               COALESCE(SUM(pnl_x * pnl_x), 0) AS pnl_sq,
               AVG(1 / cost)                   AS avg_odds,
               COALESCE(SUM(pnl_m), 0)         AS pnl_mid,
               AVG(1 / cost_mid)               AS avg_odds_mid,
               MIN(kickoff_utc)                AS first_match,
               MAX(kickoff_utc)                AS last_match
        FROM sel
    ),
    by_season AS (
        SELECT season_start, COUNT(*) AS n, COUNT(*) FILTER (WHERE won) AS wins, SUM(pnl_x) AS pnl
        FROM sel GROUP BY season_start
    ),
    by_month AS (
        SELECT to_char(date_trunc('month', kickoff_utc), 'YYYY-MM') AS month,
               COUNT(*) AS n, SUM(pnl_x) AS pnl
        FROM sel GROUP BY 1
    )
    SELECT jsonb_build_object(
        'n', agg.n,
        'wins', agg.wins,
        'pnl', ROUND(agg.pnl::numeric, 3),
        'pnl_sq', ROUND(agg.pnl_sq::numeric, 4),
        'avg_odds', ROUND(agg.avg_odds::numeric, 3),
        'n_open', 0,
        'pnl_open', NULL,
        'clv_avg', NULL,
        'pnl_mid', ROUND(agg.pnl_mid::numeric, 3),
        'avg_odds_mid', ROUND(agg.avg_odds_mid::numeric, 3),
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
$$;


-- The list of games behind those totals: run_backtest_recent's shape (db/074).
CREATE OR REPLACE FUNCTION run_backtest_venue_recent(spec jsonb, lim integer DEFAULT 8) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'kickoff', kickoff_utc,
               'league', league_name,
               'home', home_team,
               'away', away_team,
               'home_score', home_score,
               'away_score', away_score,
               'odds', ROUND((1 / cost)::numeric, 2),
               'won', won,
               'pnl', ROUND((CASE WHEN won THEN 1 / cost - 1 ELSE -1 END)::numeric, 2)
           ) ORDER BY kickoff_utc DESC, match_id DESC), '[]'::jsonb)
    FROM (
        SELECT l.*
        FROM bt_venue_lines l
        WHERE l.market = spec->>'market' AND l.side = spec->>'side' AND l.agrees
          AND bt_venue_fit(l, spec)
        ORDER BY l.kickoff_utc DESC, l.match_id DESC
        LIMIT GREATEST(1, LEAST(lim, 50))
    ) sel
$$;

REVOKE ALL ON FUNCTION refresh_bt_venue_lines() FROM PUBLIC;
REVOKE ALL ON FUNCTION bt_venue_fit(bt_venue_lines, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION run_backtest_venue(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION run_backtest_venue_recent(jsonb, integer) FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON FUNCTION refresh_bt_venue_lines() FROM anon, authenticated;
    REVOKE ALL ON FUNCTION bt_venue_fit(bt_venue_lines, jsonb) FROM anon, authenticated;
    REVOKE ALL ON FUNCTION run_backtest_venue(jsonb) FROM anon, authenticated;
    REVOKE ALL ON FUNCTION run_backtest_venue_recent(jsonb, integer) FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
