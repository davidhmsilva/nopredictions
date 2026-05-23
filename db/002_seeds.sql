-- =============================================================================
-- Seed data — leagues + bookmakers
-- =============================================================================
-- Safe to re-run (uses ON CONFLICT DO UPDATE).
--
-- Run AFTER 001_schema.sql.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- BOOKMAKERS
-- -----------------------------------------------------------------------------
-- Codes must match BOOKMAKER_COLUMNS keys in ingest/stage_a_football_data.py.
-- Pinnacle closing (PSC) and Betfair Exchange (BFEX) are our sharp references.

INSERT INTO bookmakers (code, name, kind, is_sharp) VALUES
    ('B365', 'Bet365',             'bookmaker', FALSE),
    ('PSC',  'Pinnacle (closing)', 'bookmaker', TRUE),
    ('PS',   'Pinnacle (opening)', 'bookmaker', FALSE),
    ('BW',   'Betway',             'bookmaker', FALSE),
    ('WH',   'William Hill',       'bookmaker', FALSE),
    ('VC',   'BetVictor',          'bookmaker', FALSE),
    ('BFEX', 'Betfair Exchange',   'exchange',  TRUE),
    ('MAX',  'Market Max',         'aggregate', FALSE),
    ('AVG',  'Market Avg',         'aggregate', FALSE)
ON CONFLICT (code) DO UPDATE SET
    name     = EXCLUDED.name,
    kind     = EXCLUDED.kind,
    is_sharp = EXCLUDED.is_sharp;


-- -----------------------------------------------------------------------------
-- LEAGUES
-- -----------------------------------------------------------------------------
-- Internal codes must match the LEAGUES dict in ingest/stage_a_football_data.py
-- for the 22 domestic leagues that Football-Data covers.
-- UEFA and international competitions are seeded here even though Stage A
-- doesn't ingest them (they'll be populated by later stages / manual loads).

INSERT INTO leagues (code, name, country, tier, fd_code, is_cup) VALUES
    -- England (5) ---------------------------------------------------------
    ('ENG-PR',  'Premier League',        'England', 1, 'E0',  FALSE),
    ('ENG-CH',  'Championship',          'England', 2, 'E1',  FALSE),
    ('ENG-L1',  'League One',            'England', 3, 'E2',  FALSE),
    ('ENG-L2',  'League Two',            'England', 4, 'E3',  FALSE),
    ('ENG-CON', 'National League',       'England', 5, 'EC',  FALSE),
    -- Scotland (4) --------------------------------------------------------
    ('SCO-PR',  'Scottish Premiership',  'Scotland', 1, 'SC0', FALSE),
    ('SCO-CH',  'Scottish Championship', 'Scotland', 2, 'SC1', FALSE),
    ('SCO-L1',  'Scottish League One',   'Scotland', 3, 'SC2', FALSE),
    ('SCO-L2',  'Scottish League Two',   'Scotland', 4, 'SC3', FALSE),
    -- Germany (2) ---------------------------------------------------------
    ('GER-BL1', 'Bundesliga',            'Germany',  1, 'D1',  FALSE),
    ('GER-BL2', '2. Bundesliga',         'Germany',  2, 'D2',  FALSE),
    -- Italy (2) -----------------------------------------------------------
    ('ITA-SA',  'Serie A',               'Italy',    1, 'I1',  FALSE),
    ('ITA-SB',  'Serie B',               'Italy',    2, 'I2',  FALSE),
    -- Spain (2) -----------------------------------------------------------
    ('ESP-LL',  'La Liga',               'Spain',    1, 'SP1', FALSE),
    ('ESP-L2',  'La Liga 2',             'Spain',    2, 'SP2', FALSE),
    -- France (2) ----------------------------------------------------------
    ('FRA-L1',  'Ligue 1',               'France',   1, 'F1',  FALSE),
    ('FRA-L2',  'Ligue 2',               'France',   2, 'F2',  FALSE),
    -- Others (5) ----------------------------------------------------------
    ('NED-ED',  'Eredivisie',            'Netherlands', 1, 'N1', FALSE),
    ('BEL-JPL', 'Jupiler Pro League',    'Belgium',     1, 'B1', FALSE),
    ('POR-PL',  'Primeira Liga',         'Portugal',    1, 'P1', FALSE),
    ('TUR-SL',  'Süper Lig',             'Turkey',      1, 'T1', FALSE),
    ('GRE-SL',  'Super League',          'Greece',      1, 'G1', FALSE),
    -- UEFA (3) — not covered by Football-Data; fd_code NULL --------------
    ('UEFA-CL',  'UEFA Champions League',      'UEFA', 1, NULL, FALSE),
    ('UEFA-EL',  'UEFA Europa League',         'UEFA', 2, NULL, FALSE),
    ('UEFA-ECL', 'UEFA Europa Conference League', 'UEFA', 3, NULL, FALSE),
    -- Americas (7) — FBref-sourced, no Football-Data coverage --------------
    ('USA-MLS',     'Major League Soccer',  'USA',       1, NULL, FALSE),
    ('BRA-SA',      'Brasileirão Série A',  'Brazil',    1, NULL, FALSE),
    ('ARG-PD',      'Primera División',     'Argentina', 1, NULL, FALSE),
    ('MEX-LMX',     'Liga MX',             'Mexico',    1, NULL, FALSE),
    ('COL-PA',      'Primera A',           'Colombia',  1, NULL, FALSE),
    ('CHL-PD',      'Primera División',    'Chile',     1, NULL, FALSE),
    ('CONMEBOL-CL', 'Copa Libertadores',   'CONMEBOL',  1, NULL, TRUE),
    -- International (2) ---------------------------------------------------
    ('INT-WC',   'FIFA World Cup',             'INTL', 1, NULL, TRUE),
    ('INT-EURO', 'UEFA European Championship', 'INTL', 1, NULL, TRUE)
ON CONFLICT (code) DO UPDATE SET
    name    = EXCLUDED.name,
    country = EXCLUDED.country,
    tier    = EXCLUDED.tier,
    fd_code = EXCLUDED.fd_code,
    is_cup  = EXCLUDED.is_cup;

-- Total: 22 FD-covered + 3 UEFA + 7 Americas + 2 International = 34 competitions.
