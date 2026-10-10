-- 066: what Football-Data publishes that Stage A never loaded (Stage K).
--
-- 1. Sixteen "extra" countries (football-data.co.uk/new/<CODE>.csv): results
--    and closing 1X2 — Pinnacle to 2026-01, Betfair Exchange, market max and
--    average, Bet365 — from 2012. Four of them (ARG, BRA, MEX, USA) are already
--    leagues here (Stage G, api-football, 2020-25, no odds at all); the other
--    twelve are new, plus Argentina's Copa de la Liga, which Football-Data
--    files under the same country.
-- 2. Two pre-closing market aggregates, so O/U 2.5 and Asian handicap prices
--    exist before 2019-20 (Betbrain's max/average, which Football-Data carried
--    until it switched to its own Max/Avg columns in 2019-20 — both are kept
--    under the same code, so the series runs 2010 → now).
-- 3. match_context: referee now (Football-Data, England and Scotland), with
--    room for attendance, venue, managers and formations (Transfermarkt).

INSERT INTO leagues (code, name, country, tier, is_international, is_cup, fd_code, active) VALUES
    ('JPN-J1',  'J1 League',                    'Japan',       1, false, false, 'new/JPN', true),
    ('NOR-EL',  'Eliteserien',                  'Norway',      1, false, false, 'new/NOR', true),
    ('SWE-AL',  'Allsvenskan',                  'Sweden',      1, false, false, 'new/SWE', true),
    ('DEN-SL',  'Superliga',                    'Denmark',     1, false, false, 'new/DNK', true),
    ('AUT-BL',  'Bundesliga',                   'Austria',     1, false, false, 'new/AUT', true),
    ('SUI-SL',  'Super League',                 'Switzerland', 1, false, false, 'new/SWZ', true),
    ('POL-EK',  'Ekstraklasa',                  'Poland',      1, false, false, 'new/POL', true),
    ('IRL-PD',  'Premier Division',             'Ireland',     1, false, false, 'new/IRL', true),
    ('FIN-VL',  'Veikkausliiga',                'Finland',     1, false, false, 'new/FIN', true),
    ('ROU-L1',  'SuperLiga',                    'Romania',     1, false, false, 'new/ROU', true),
    ('RUS-PL',  'Premier League',               'Russia',      1, false, false, 'new/RUS', true),
    ('CHN-CSL', 'Super League',                 'China',       1, false, false, 'new/CHN', true),
    ('ARG-CLP', 'Copa de la Liga Profesional',  'Argentina',   1, false, true,  'new/ARG', true)
ON CONFLICT (code) DO NOTHING;

-- snapshot_type 'opening' on these rows: they are NOT closes, and every
-- existing reader that wants a close filters on snapshot_type = 'closing'.
INSERT INTO bookmakers (code, name, is_exchange, is_sharp) VALUES
    ('MAXO', 'Maximum market (pre-closing; Betbrain until 2018-19)', false, false),
    ('AVGO', 'Market average (pre-closing; Betbrain until 2018-19)', false, false)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS match_context (
    match_id        INT          PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    referee         TEXT,
    referee_source  TEXT,                 -- 'football-data' | 'transfermarkt'
    attendance      INT,
    venue           TEXT,
    home_manager    TEXT,
    away_manager    TEXT,
    home_formation  TEXT,
    away_formation  TEXT,
    context_source  TEXT,                 -- who filled the non-referee columns
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_match_context_referee ON match_context (referee);

ALTER TABLE match_context ENABLE ROW LEVEL SECURITY;
