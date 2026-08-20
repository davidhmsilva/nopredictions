-- 034 — favourite-pressure observations (Live Pressure Favourite HT)
--
-- Third arm of the pressure family. Where 031 buys "another goal" and 033 buys
-- "a goal before the break", this one buys a SIDE: PM's "<Favourite> leading at
-- halftime?", entered while the match is still 0-0 and only once the pre-match
-- favourite has visibly been on top for the opening quarter of an hour.
--
-- The columns that do not exist on its siblings are the ones that matter here:
-- which side the favourite is, what the market priced it at BEFORE kickoff, and
-- the two-sided pressure split. A side error on this market does not degrade the
-- bet, it inverts it, so every input to that decision is stored rather than
-- recomputed later from a price that has since moved.

CREATE TABLE IF NOT EXISTS fav_ht_observations (
    id                  BIGSERIAL PRIMARY KEY,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    obs_version         SMALLINT    NOT NULL DEFAULT 1,

    fixture_id          BIGINT      NOT NULL,
    league              TEXT,
    home                TEXT        NOT NULL,
    away                TEXT        NOT NULL,
    event_title         TEXT,
    condition_id        TEXT,
    token_id            TEXT,

    minute              SMALLINT    NOT NULL,
    home_goals          SMALLINT    NOT NULL,
    away_goals          SMALLINT    NOT NULL,
    goals_total         SMALLINT    NOT NULL,
    ladder_goals        SMALLINT,
    score_agrees        BOOLEAN,

    -- the favourite, as PM priced it BEFORE kickoff
    fav_side            TEXT,        -- 'home' / 'away', resolved against api-football
    fav_team            TEXT,
    fav_prob            REAL,        -- de-vigged across all three 1X2 legs
    -- false = the 1X2 was first seen in play, so the price has already drifted
    -- with the goalless clock. Never traded on: both the "is it a favourite"
    -- gate and the strength bucket would be reading a different quantity.
    fav_is_prematch     BOOLEAN NOT NULL DEFAULT false,

    -- pressure features, cumulative totals at this poll
    home_xg             REAL,
    away_xg             REAL,
    home_shots_on       SMALLINT,
    away_shots_on       SMALLINT,
    home_shots_total    SMALLINT,
    away_shots_total    SMALLINT,
    home_shots_inside   SMALLINT,
    away_shots_inside   SMALLINT,
    home_corners        SMALLINT,
    away_corners        SMALLINT,
    home_possession     REAL,
    away_possession     REAL,
    home_reds           SMALLINT,
    away_reds           SMALLINT,
    has_stats           BOOLEAN NOT NULL DEFAULT false,
    has_xg              BOOLEAN NOT NULL DEFAULT false,

    -- composite, per side, scaled to a 15-minute rate
    home_danger         REAL,
    away_danger         REAL,
    -- the FROZEN 15-18' reading: the favourite's own index, the underdog's, and
    -- the gap. The gap is the part that says "living up to the price" rather
    -- than merely "busy".
    opening_fav_pressure REAL,
    opening_dog_pressure REAL,
    opening_dominance    REAL,
    opening_minute       SMALLINT,
    pressure_factor      REAL,

    pre_over25          REAL,
    best_bid            REAL,
    best_ask            REAL,
    bid_depth_usd       REAL,
    ask_depth_usd       REAL,
    fair_base           REAL,       -- empirical table: P(fav leads at HT | 0-0, m)
    fair_pressure       REAL,       -- crude monotone transform, recorded only
    fair_n              INTEGER,
    fee_pp              REAL,
    edge_base_pp        REAL,
    edge_pressure_pp    REAL,
    would_enter         BOOLEAN NOT NULL DEFAULT false,
    entered             BOOLEAN NOT NULL DEFAULT false,
    paper_trade_id      INTEGER REFERENCES paper_trades(id),
    skip_reason         TEXT,

    -- settlement, off api-football's own score.halftime
    ht_home_goals       SMALLINT,
    ht_away_goals       SMALLINT,
    fav_led_at_ht       BOOLEAN,
    ht_source           TEXT,        -- 'api' = score.halftime, 'poll' = our tape
    settled_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_fav_ht_fixture ON fav_ht_observations (fixture_id, minute);
CREATE INDEX IF NOT EXISTS idx_fav_ht_observed ON fav_ht_observations (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_fav_ht_unsettled ON fav_ht_observations (settled_at)
    WHERE settled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_fav_ht_entered ON fav_ht_observations (entered)
    WHERE entered;

CREATE UNIQUE INDEX IF NOT EXISTS idx_fav_ht_one_entry
    ON fav_ht_observations (fixture_id) WHERE entered;

INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT
  'H-PRESSURE-FAV — a favourite visibly on top at 15'' is underpriced to lead at HT',
  'On a live football fixture still 0-0 after 15 minutes, buying PM "<Favourite> '
  'leading at halftime?" when the pre-match favourite (de-vigged PM 1X2 >= 0.50, '
  'captured before kickoff) has both pressed hard in absolute terms and '
  'out-pressed the underdog over those 15 minutes pays more than the price '
  'implies. '
  'PRE-REGISTERED PREDICTION: on >= 200 settled entries, the yield CI excludes '
  'zero after the taker fee, AND the dominance arm beats the plain-favourite arm '
  'recorded on the same rows. '
  'FALSIFIED IF: the two arms are within noise — that would mean early dominance '
  'is already in the in-play price, which is the null. '
  'PRIMARY TEST: logistic regression of fav_led_at_ht on opening_dominance with '
  'fav_prob, minute and venue as controls, over ALL observation rows (entered or '
  'not), one row per fixture. The yield comparison is secondary — far less power.',
  'The favourite''s strength is in the price by construction, so backing '
  'favourites is not a strategy. What is not obviously in the price is whether '
  'this particular favourite is currently living up to it: shots, xG and '
  'territory arrive on a slower feed than the scoreline, and the half-time '
  'leader market is thin enough that it may not be repriced by anyone watching '
  'the match. Against it: PM in-play mid behaved as a martingale over 298k '
  'observations, in-play half-time books are micro, and every in-play edge '
  'measured on this project so far has died at the spread. Also note the market '
  'resolves NO on a half-time draw, which is the modal outcome from 0-0 at 15'' '
  '(43-57% depending on state) — this is a value bet on a 30-minute window, not '
  'a bet that the better team wins.',
  'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-PRESSURE-FAV%');

INSERT INTO strategies (hypothesis_id, name, rules)
SELECT (SELECT id FROM research_hypotheses WHERE title LIKE 'H-PRESSURE-FAV%' LIMIT 1),
       'Live Pressure Favourite HT',
       '{"venue":"polymarket","market":"favourite leading at halftime","phase":"in-play",'
       '"entry":"0-0 at 15-25 minutes, pre-match favourite >= 0.50 that is both '
       'pressing and out-pressing the underdog over the opening 15",'
       '"signal":"api-football in-game pressure, per side, frozen at 15-18 minutes",'
       '"status":"paper only — thresholds calibrated to frequency, never to an outcome"}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM strategies WHERE name = 'Live Pressure Favourite HT');

-- Public projection, column-compatible with v_pressure_trades / v_ht_pressure_trades
-- so the dashboard reads all three agents through one shape. target_line has no
-- meaning on a 1X2-style market; pressure_index carries the favourite's own
-- opening reading, which is the number the entry was made on.
CREATE OR REPLACE VIEW v_fav_ht_trades AS
SELECT
    o.paper_trade_id,
    o.fixture_id,
    o.obs_version,
    o.home,
    o.away,
    o.league,
    o.minute                AS entry_minute,
    o.goals_total           AS goals_at_entry,
    NULL::real              AS target_line,
    o.best_ask              AS entry_price,
    o.opening_fav_pressure  AS pressure_index,
    NULL::int               AS goal_minute,
    o.ht_source             AS goal_minute_source,
    o.fav_led_at_ht         AS won,
    (o.ht_home_goals + o.ht_away_goals) AS final_goals,
    o.observed_at           AS entered_at
FROM fav_ht_observations o
WHERE o.entered AND o.paper_trade_id IS NOT NULL;

COMMENT ON VIEW v_fav_ht_trades IS
  'Public projection of Live Pressure Favourite HT entries. Same columns as '
  'v_pressure_trades. Join to paper_trades on paper_trade_id = paper_trades.id.';

GRANT SELECT ON v_fav_ht_trades TO anon, authenticated;
