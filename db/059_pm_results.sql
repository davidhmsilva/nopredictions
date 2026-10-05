-- 059_pm_results.sql — every finished Polymarket football fixture, with the
-- 1X2 price at kick-off and what happened.
--
-- The site's "Yesterday" board and /results/<day> read it; the daily cron
-- (site/app/api/cron/pm-results) writes it. One row per fixture event.
--
-- Prices come from the CLOB's own price history, read at the last point at or
-- before kick-off (measured on 2026-09-27: 97 of 98 fixtures had a point in
-- the kick-off minute, and the three Yes prices summed to 1.005 at the median),
-- then normalised to sum to 1. The outcome is Polymarket's RESOLUTION of the
-- three questions, never inferred from the score: extra time and penalties
-- change what a score means, and the market's own verdict is the one a trader
-- was paid on (db/039 measured that trap on "Portugal vs Croatia").
--
-- fav_record is written once, when the row is written, from rows and matches
-- BEFORE this kick-off only: "before this game, this favourite at this price
-- had won W of N". It is never recomputed with later games in it.
--
-- RLS on, no policies: the site reads it over DATABASE_URL (db/045, db/048).

CREATE TABLE IF NOT EXISTS pm_results (
    slug          text        PRIMARY KEY,           -- Polymarket event slug, what /game/<slug> takes
    kickoff       timestamptz NOT NULL,
    title         text        NOT NULL,
    home          text        NOT NULL,              -- Polymarket's names, as the title orders them
    away          text        NOT NULL,
    competition   text,
    p_home        real,                              -- normalised to sum 1
    p_draw        real,
    p_away        real,
    raw_sum       real,                              -- the three Yes prices before normalising
    price_at      timestamptz,                       -- oldest of the three history points used
    outcome       text CHECK (outcome IN ('home', 'draw', 'away')),
    score         text,                              -- Polymarket's "h-a", when the event carries it
    period        text,                              -- FT / VFT / CAN …
    volume_usd    real,
    fav_record    jsonb,
    resolved_at   timestamptz,                       -- when we first saw an outcome
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pm_results_kickoff ON pm_results (kickoff);
-- The favourite's history at a price is looked up by name.
CREATE INDEX IF NOT EXISTS pm_results_home ON pm_results (lower(home), kickoff);
CREATE INDEX IF NOT EXISTS pm_results_away ON pm_results (lower(away), kickoff);

ALTER TABLE pm_results ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON pm_results FROM anon, authenticated;
