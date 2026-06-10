-- 022_wc_agent.sql
-- World Cup 2026 paper sub-agent.
--
-- Separate from the main strategies (DC, Sim, No-Bias, Poisson, …). Bets one
-- pick per game on every WC 2026 fixture from an own national-team Elo line —
-- no sharp-book reference, markets restricted to 1X2 + Over/Under. Strategy
-- registered as id=10 so the site, status reports and resolver pick it up
-- automatically once trades start landing.

INSERT INTO research_hypotheses (id, title, description, source, status, created_by)
VALUES (
    15,
    'World Cup 2026 sub-agent (own national-team Elo)',
    'Purpose-built national-team Elo (Stage G internationals) prices the WC '
    'tournament directly via the existing MC sim, then a per-game selector picks '
    'one Polymarket bet per match from 1X2 + Over/Under only. No Pinnacle / '
    'Betfair anchor — the alternative line IS the alpha. Objective: finish the '
    'tournament green via balanced EV (variance cap + slight favorite tilt). '
    'Phase 0 gate passed 2026-06-04: nat-team Elo out-calibrates DC on '
    'internationals (paired 1X2 Brier 0.537 < 0.575, log-loss 0.914 < 0.968, n=4173).',
    'agent', 'promoted', 'system'
) ON CONFLICT (id) DO NOTHING;

INSERT INTO strategies (id, name, promoted_at, hypothesis_id, rules)
VALUES (
    10,
    'World Cup Agent',
    NOW(),
    15,
    '{"phase":         "paper-only",
      "venue":         "Polymarket",
      "model":         "Own national-team Elo -> MC sim",
      "markets":       ["home_win","draw","away_win","over_1_5","over_2_5","over_3_5","under_2_5","under_3_5"],
      "no_sharp":      true,
      "objective":     "balanced EV (variance cap + favorite tilt)",
      "scope":         "every WC 2026 fixture, one bet per game",
      "stake":         "1u flat"
    }'::jsonb
) ON CONFLICT (id) DO UPDATE
SET name  = EXCLUDED.name,
    rules = EXCLUDED.rules;

SELECT setval('strategies_id_seq', GREATEST((SELECT MAX(id) FROM strategies), 10));


-- Tiny state table used by the adaptive meta-layer (Phase 2). Singleton row.
-- snapshot_ratings holds the WC-informed Elo (post-Bayesian update from observed
-- WC results so knockout bets use updated strengths). adaptive_weights blends
-- the candidate experts (Elo line, structural priors, sentiment/public-fade) —
-- starts uniform, drifts toward whichever is actually winning after the group
-- stage.
CREATE TABLE IF NOT EXISTS wc_agent_state (
    id                  INT PRIMARY KEY DEFAULT 1,
    snapshot_ratings    JSONB,        -- {team: rating}
    adaptive_weights    JSONB,        -- {"elo_line": w1, "draw_bias": w2, "ko_unders": w3, "public_fade": w4}
    n_settled           INT NOT NULL DEFAULT 0,
    pl_units            NUMERIC NOT NULL DEFAULT 0,
    notes               TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT wc_agent_state_singleton CHECK (id = 1)
);

INSERT INTO wc_agent_state (id, snapshot_ratings, adaptive_weights, notes)
VALUES (1, NULL, '{"elo_line":1.0,"draw_bias":1.0,"ko_unders":1.0,"public_fade":1.0}'::jsonb,
        'Initialised pre-kickoff (2026-06-10). Weights uniform until ~24 settled bets.')
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE wc_agent_state IS
  'Singleton state for the World Cup 2026 sub-agent: ratings snapshot + adaptive weights.';
