-- 059_venue_market_history.sql
-- Settled football markets on Polymarket and Kalshi, one row per binary
-- market: what it cost before kick-off and how it resolved.
--
-- Written by ingest/stage_j_venue_history.py. It exists so the Lab can answer
-- the question a prediction-market user actually asks, "would this have made
-- money at the venue's price?", rather than only "did it beat Pinnacle?".
--
-- What each price column IS, measured 2026-09-29, before anyone reads a yield
-- off it:
--
--   mid_*       Polymarket: CLOB /prices-history, which is the book MID (60 of
--               60 recorded bid/ask pairs agreed to 0.005, a 0.03/0.96
--               placeholder book included, read as 0.495). A mid is not a price
--               anyone paid. Kalshi: (yes_bid + yes_ask) / 2 of the minute
--               candle.
--   bid_close / ask_close
--               Kalshi only: the real book at the last candle before kick-off.
--   buy0_* / buy1_*
--               Polymarket only: the last TAKER BUY of each outcome before
--               kick-off (data-api /trades?end=), i.e. an ask somebody actually
--               paid, before the fee. With both sides recent,
--               buy0 + buy1 - 1 is the spread at that moment.
--   fee_rate    the market's own taker schedule, rate * p * (1 - p) per
--               share. Polymarket sports carried NO fee before ~2026-03, then
--               0.03 (v2), then 0.05 (v3); Kalshi 0.07.
--
-- Everything is in OUTCOME 0's terms. Outcome 1 is the complement: its mid is
-- 1 - mid, its ask is 1 - bid.
--
-- Sides: team_a / team_b are the VENUE's order (Polymarket teams[].ordering
-- when present, otherwise the title; Kalshi's title, verified home-first).
-- Home and away for a backtest come from the linked `matches` row, never from
-- here: `match_swapped` says the venue's team_a is the match's AWAY side, and
-- `subject_side` resolves a moneyline or spread subject to home/away/draw.

CREATE TABLE IF NOT EXISTS venue_market_history (
    venue              TEXT        NOT NULL CHECK (venue IN ('polymarket', 'kalshi')),
    market_id          TEXT        NOT NULL,   -- PM conditionId | Kalshi market ticker
    event_id           TEXT,                   -- PM event id | Kalshi event ticker
    sport              TEXT        NOT NULL DEFAULT 'soccer',
    competition        TEXT,                   -- PM seriesSlug | Kalshi series ticker
    competition_name   TEXT,
    fixture            TEXT,                   -- "A vs. B" as the venue writes it
    team_a             TEXT,
    team_b             TEXT,
    sides_source       TEXT,                   -- 'pm_teams' | 'title' | 'kalshi_title'
    event_date         DATE,                   -- the date the venue files it under (Kalshi: ET)
    kickoff_utc        TIMESTAMPTZ,
    kickoff_source     TEXT,                   -- see stage_j settle_kickoff(): listed | match_london | end_estimate
    kickoff_listed     TIMESTAMPTZ,            -- the start the venue LISTED (PM gameStartTime), kept for the audit
    finished_at        TIMESTAMPTZ,            -- PM event finishedTimestamp: when the match actually ended
    closed_at          TIMESTAMPTZ,            -- PM market closedTime | Kalshi close_time (a winner was declared)
    family             TEXT        NOT NULL CHECK (family IN ('moneyline', 'totals', 'spreads', 'btts')),
    question           TEXT,
    subject            TEXT,                   -- moneyline: team or 'draw'; spreads: the team the line is on
    subject_team       TEXT CHECK (subject_team IN ('a', 'b', 'draw')),  -- which of team_a / team_b the subject is
    line               NUMERIC(6,2),           -- totals: 2.5; spreads: -1.5 on the subject
    outcome0           TEXT,
    outcome1           TEXT,
    token0             TEXT,
    token1             TEXT,
    volume_usd         NUMERIC(16,2),
    fee_rate           NUMERIC(6,4),
    winner             SMALLINT CHECK (winner IN (0, 1)),
    payout0            NUMERIC(6,4),           -- what one share of outcome 0 paid: 1, 0, 0.5
    final_score        TEXT,                   -- PM event score, in team_a-team_b order

    -- the price, outcome 0's side
    mid_24h            NUMERIC(6,4),
    mid_6h             NUMERIC(6,4),
    mid_1h             NUMERIC(6,4),
    mid_close          NUMERIC(6,4),
    close_at           TIMESTAMPTZ,            -- the time of the mid_close point
    bid_close          NUMERIC(6,4),
    ask_close          NUMERIC(6,4),
    buy0_price         NUMERIC(6,4),
    buy0_at            TIMESTAMPTZ,
    buy1_price         NUMERIC(6,4),
    buy1_at            TIMESTAMPTZ,
    trades_1h          INTEGER,                -- taker trades in the last hour before kick-off (capped by one page)
    prices_status      TEXT,                   -- NULL = not fetched | 'ok' | 'no_history' | 'no_kickoff' | 'no_volume' | 'error'
    prices_fetched_at  TIMESTAMPTZ,

    -- the join to our own results
    match_id           INTEGER REFERENCES matches(id),
    match_swapped      BOOLEAN,
    subject_side       TEXT CHECK (subject_side IN ('home', 'away', 'draw')),
    link_score         NUMERIC(4,3),
    link_status        TEXT,                   -- NULL = not tried | 'linked' | 'no_candidate' | 'ambiguous' | 'not_in_model' | 'no_teams'
    linked_at          TIMESTAMPTZ,

    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (venue, market_id)
);

CREATE INDEX IF NOT EXISTS idx_vmh_match    ON venue_market_history (match_id) WHERE match_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vmh_kickoff  ON venue_market_history (kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_vmh_prices   ON venue_market_history (venue, prices_status);
CREATE INDEX IF NOT EXISTS idx_vmh_event    ON venue_market_history (venue, event_id);

-- Research data, read by the Lab over DATABASE_URL only. Same posture as
-- db/045 and db/048: RLS on, no policies, nothing granted to the client roles.
ALTER TABLE venue_market_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE venue_market_history FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON TABLE venue_market_history FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
