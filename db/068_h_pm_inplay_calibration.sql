-- 068: pre-registration of H-PM-INPLAY-CAL, written BEFORE any Polymarket
-- price was compared with an outcome (2026-10-04).
--
-- Stage M (2026-10-03) made Polymarket's in-play history testable at once:
-- 55,458 settled football markets with a 1-minute price path, 2024-08 → 2026-09.
-- Until then every in-play claim in this repo rested on weeks of forward data.

INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT
  'H-PM-INPLAY-CAL — Polymarket''s in-play football price is calibrated: no taker edge after costs',
  'UNIVERSE: every settled Polymarket football market in venue_market_history '
  '(prices_status ''ok''; moneyline, totals, btts, spreads; 55,458 markets, 6,417 fixtures, '
  '2024-08 → 2026-09) with its Stage M 1-minute price path. '
  'OBSERVATION: (market, minute) — token0''s DISPLAYED price p against token0''s payout y (0/1). '
  'CLOCK AND SCORE: ESPN timelines (Stage L), each fixture anchored on its own goals (the minute '
  'its markets jump together, matched to ESPN''s goal minute). Fixtures with no timeline or that '
  'went to extra time are left out of the in-play phases. '
  'PHASES: PRE (listed kick-off −30 to −1, the control), 1H (1-45''), 2H-A (46-69''), 2H-B (70-95''). '
  'EXCLUDED: p outside [0.02, 0.98]; markets the score has already decided (an Over past its line, '
  'BTTS once both have scored) — counted separately; the minute of each goal''s market reaction and '
  'the two after it (post-goal staleness; past information only). '
  'STATISTIC: per cell (family × phase × price bucket) the mean over FIXTURES of each fixture''s mean '
  '(y − p); 95% CI by fixture bootstrap, B = 2000. '
  'SPLIT: DISCOVERY = kick-off before 2026-03-01 (3,609 fixtures), TEST = from 2026-03-01 (2,808). '
  'Verdicts on TEST only. '
  'PREDICTIONS: '
  'P1 (main): in every family × in-play phase, all prices pooled, the TEST mean (y − p) lies within '
  '±1.0pp, CI included. '
  'P2 (late longshots, the Angelini / "Yogi Berra" bias): in 2H-A and 2H-B, families pooled, tokens '
  'priced [0.02, 0.15) resolve BELOW their price and tokens (0.85, 0.98] ABOVE it. '
  'P3 (late overs): totals Over tokens in 2H-B priced [0.15, 0.85] resolve ABOVE their price. '
  'DECISION RULE: a cell is a tradeable mispricing only if, in TEST: n >= 200 fixtures; the CI '
  'excludes 0; |mean| exceeds cost = the median half-spread measured on the live tape '
  '(agent/data/soccer_live, 2026-09-13 → 09-27, same family × phase × bucket) + fee_rate·p·(1−p); '
  'the sign is the same in DISCOVERY; and it survives BH-FDR at q <= 0.10 across every cell scanned. '
  'SENSITIVITY (reported, not decisive): also dropping the minute BEFORE each goal''s market reaction; '
  'markets with volume >= $5k only; fee-free markets only. '
  'FALSIFIED (P1) IF any cell clears the decision rule.',
  'Pre-match, Polymarket''s mid sits on the Pinnacle line (finding-pm-mid-is-pinnacle): the loss is '
  'the spread. In-play has never been tested with history — pm_ticks began 2026-07-21 — so the '
  'in-play readings in CLAUDE.md (late overs fair-to-cheap on clean books, +2 to +4pp with CIs '
  'crossing zero; the HT over 0.5 ~4pp rich; the favourite-at-HT market 8.5pp above realised; PM '
  'prices the pressure correctly) all rest on a few weeks of forward rows. EXPECTED: calibrated '
  'within costs, as pre-match. The price is the DISPLAYED price — the book mid, or the last trade '
  'when the spread is wide — never a fill, so any gap has to clear the spread before it is an edge; '
  'the live tape says what crossing it costs.',
  'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-PM-INPLAY-CAL%');

-- AMENDMENT, 2026-10-04, written BEFORE any outcome was examined. Found during
-- the outcome-blind build (pm_inplay_calibration.py --costs), on the live tape.
UPDATE research_hypotheses SET description = description || ' '
  || 'AMENDMENT 2026-10-04 (before any outcome was examined): the live tape shows Polymarket''s '
  || 'history price is (best bid + best ask)/2 with a MISSING bid counted as 0 and a missing ask as 1 — '
  || 'an empty book reads 0.5000 (98% of empty-book minutes), an ask-only book ask/2 (90%), a bid-only '
  || 'book (bid+1)/2 (96%). Those minutes are artefacts — 47% of tape minutes, concentrated late and in '
  || 'totals/spreads — and they would manufacture exactly the tail shape P2 predicts. Therefore: '
  || '(1) p = 0.5000 is dropped; (2) the primary test runs only on TRUSTED cells — family × phase × '
  || 'displayed-price bucket where >= 95% of displayed prices on the tape came from a two-sided book, '
  || 'with >= 200 tape minutes (agent/data/pm_inplay_cal/two_sided_given_p.csv); every other cell is '
  || 'reported as not measurable from history mids; (3) secondary, not decisive: the same calibration '
  || 'at REAL quotes (mid and ask) on the live tape itself, 2026-09-13 → 09-27 — the only place P3 can '
  || 'be measured on Polymarket. Decision rule otherwise unchanged.'
WHERE title LIKE 'H-PM-INPLAY-CAL%' AND description NOT LIKE '%AMENDMENT 2026-10-04%';
