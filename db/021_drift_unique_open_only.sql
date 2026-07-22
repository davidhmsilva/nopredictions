-- Fix: original UNIQUE(token_id, status) blocks closing a second position on the same
-- token (any token can only ever appear once as 'closed'). What we actually want is:
-- "at most one OPEN position per token". Replace with a partial unique index.

ALTER TABLE drift_positions DROP CONSTRAINT IF EXISTS drift_positions_token_id_status_key;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_drift_open_per_token
    ON drift_positions (token_id)
    WHERE status = 'open';
