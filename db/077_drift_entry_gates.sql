-- Pre-Match Drift entry gates on the executable price (2026-10-19).
--
-- db/076 made the agent buy NO at the ask and sell at the bid. Two things it
-- left alone are fixed in drift_scanner.py alongside this migration:
--
--   1. Book gates. A position is marked at the NO bid the moment it is bought,
--      so any book wider than the −5% stop was a stop-out on the first check
--      (11 of 18 dry-run entries on 2026-10-16). Entry now needs a two-sided NO
--      book, ≤ 3c wide, with ≥ $25 at the ask.
--   2. The edge. edge_pp compares Gamma's YES ask with the DC fair, but buying
--      NO at p is selling YES at 1 − p, which can sit far below that ask (0.04
--      against 0.43 on Brighton v Crystal Palace). Entry now needs
--      exec_edge_pp = (1 − entry_no_price − dc_fair) × 100 ≥ 8.
--
-- The stop moved too: it is now STOP_PCT below the NO bid at entry, not below
-- the price paid, so the spread paid on the way in is not read as a move.
--
-- entry_yes_price and edge_pp keep their db/076 meaning (the signal). A NULL
-- exec_edge_pp marks a row opened before these gates.

ALTER TABLE drift_positions ADD COLUMN IF NOT EXISTS exec_edge_pp NUMERIC;

COMMENT ON COLUMN drift_positions.exec_edge_pp IS
  'Edge on the YES price actually obtained: (1 - entry_no_price - dc_fair) * 100. NULL = opened before db/077.';
