# NOPREDICTIONS — AI Trading Agent for Football Prediction Markets

**Brand:** NOPREDICTIONS · **Domain:** [nopredictions.com](https://nopredictions.com) · **Pivot date:** 2026-05-02

## What this project is

An AI trading agent that hunts for exploitable edges in football prediction markets,
with **Polymarket as the primary trading venue** and **Betfair Exchange + Pinnacle as the sharp
benchmark**. The agent compares Polymarket prices against sharp bookmaker consensus
and mathematical models to find mispricings, then logs paper trades publicly at
**nopredictions.com**.

**Core thesis — two waves at once:**

1. **AI is suddenly capable** of doing serious research at human-analyst quality.
2. **Prediction markets** (Polymarket especially) are having their cultural moment —
   high attention, growing volume, comparatively thin systematic sharp money.

We sit at the intersection. The agent doesn't predict — it finds **mispricings**.
Hence the brand: *no predictions, just edges*.

**Why Polymarket primary, Betfair as benchmark:** Betfair Exchange is the most
efficient sports market in the world — prices are set by sharp money competing
against each other, no bookmaker margin. We use Betfair (and Pinnacle closing
lines) as the **truth oracle for fair value**. If our model says a Polymarket
price is mispriced *relative to the Betfair / Pinnacle consensus*, that's our
edge signal. Betfair tells us what the market should be; Polymarket tells us
where to trade.

**Two active strategies:**
1. **PM-vs-Sharp Consensus (pre-match)** — compare PM prices against vig-removed Pinnacle + Betfair odds. Edge = sharp_prob − pm_price. Trade if edge >= threshold.
2. **PM-vs-Poisson In-Play** — derive pre-match Poisson lambdas from sharp odds, then calculate in-play fair probabilities given current score + minute. Trade if PM diverges significantly.

**Content angle:** The entire process is documented publicly at
[nopredictions.com](https://nopredictions.com) and on X (Twitter), in English.
The value is watching an AI try to beat the prediction market everyone is watching —
its hypotheses, its failures, its reasoning. Not a tipster account. A live experiment
riding the AI + prediction-markets wave simultaneously.

---

## Project structure

```
~/agente/
├── CLAUDE.md                          ← you are here
├── .gitignore                         ← git ignore rules
├── site_prototype.html                ← static HTML prototype (legacy)
├── ingest/
│   ├── stage_a_football_data.py       ← Football-Data ingestion ✅ DONE
│   ├── stage_b_fbref_xg.py            ← FBref xG scraper ✅ DONE
│   ├── stage_b_understat.py           ← Understat xG (alternative to FBref) ✅
│   ├── stage_c_polymarket.py          ← PM market fetcher ✅ DONE
│   ├── stage_c_clubelo.py             ← ClubElo ratings ✅
│   ├── stage_d_odds_api.py            ← The Odds API live sharp odds ✅
│   ├── stage_e_inplay_monitor.py      ← In-play live score monitor ✅
│   ├── stage_g_international.py       ← International results (WC, Euro, qualifs) ✅
│   ├── stage_i_kalshi.py              ← Kalshi market ingestion (read-only) ✅
│   ├── requirements.txt
│   ├── .env.example
│   ├── .env                           ← DB connection string + API keys (gitignored)
│   └── tests/
│       └── test_stage_a_end_to_end.py
├── db/
│   ├── 001_schema.sql                 ← deployed ✅
│   ├── 002_seeds.sql                  ← deployed ✅
│   ├── 003_quality_checks.sql         ← data quality queries
│   └── 004_pm_tracking.sql            ← Polymarket tracking schema ✅
├── agent/
│   ├── run.py                         ← CLI entry point ✅
│   ├── orchestrator.py                ← simplified control loop ✅
│   ├── paper_trader.py                ← Strategy 1: PM-vs-Sharp Consensus ✅
│   ├── poisson_trader.py              ← Strategy 2: PM-vs-Poisson In-Play (legacy) ✅
│   ├── dixon_coles.py                 ← Dixon-Coles xG model ✅
│   ├── dc_scanner.py                  ← Strategy 4: DC Model Pre-Match scanner ✅
│   ├── elo_model.py                   ← Strategy 3: ELO+Poisson model ✅
│   ├── nba_scanner.py                 ← Strategy 5: NBA Elo Pre-Match scanner ✅
│   ├── espn_stats.py                  ← free live stats, no key — fallback ✅ NEW
│   ├── injury_tracker.py              ← real-time player injury / suspension data ✅
│   ├── market_flow.py                 ← whale activity + smart money signals ✅
│   ├── live_tracker.py                ← rolling-window pressure signals ✅
│   ├── pressure_agent.py              ← Live Pressure Overs + drives the HT arm ✅
│   ├── ht_pressure_agent.py           ← Live Pressure HT Over 0.5 (first half) ✅
│   ├── fav_pressure_agent.py          ← Live Pressure Favourite HT ✅
│   ├── first_half_table.py            ← empirical P(goal before HT | 0-0 at m) ✅
│   ├── favourite_ht_table.py          ← empirical P(fav leads at HT | 0-0 at m) ✅
│   ├── sim/                           ← Monte Carlo match simulator ✅ NEW
│   │   ├── state.py                   ←   vectorized MatchState
│   │   ├── rates.py                   ←   λ → per-minute rates
│   │   ├── adjustments.py             ←   state-dependent rate modifiers
│   │   ├── simulator.py               ←   vectorized MC loop
│   │   ├── pricer.py                  ←   sim → market probabilities
│   │   └── calibrator.py              ←   vs analytical Poisson sanity check
│   ├── sim_demo.py                    ← sim engine demo + calibration check ✅ NEW
│   ├── sim_scanner.py                 ← Strategy 6: Sim Model Pre-Match ✅ NEW
│   ├── inplay_sim_scanner.py          ← Strategy 7: Sim Model In-Play ✅ NEW
│   ├── wallet_analyzer.py            ← Polymarket wallet analyser (FIFO + written reading) ✅ NEW
│   ├── late_goals_table.py            ← empirical late-goal fair value (no model) ✅
│   ├── priced_like_table.py           ← Game Center "priced like this" + league base rates ✅ NEW
│   ├── venues.py                      ← both exchanges, one price: fees, book gates, best-of ✅ NEW
│   ├── late_goals_observer.py         ← Over Late Goals — paper observation only ✅
│   ├── resolver.py                    ← resolves trades + calculates CLV ✅
│   ├── lab_strategy_runner.py         ← paper-trades users' saved Lab agents ✅ NEW
│   ├── tools/
│   │   └── db.py                      ← DB read/write helpers
│   └── _archive/                      ← retired research pipeline
│       ├── generator.py
│       ├── backtester.py
│       ├── critic.py
│       ├── prompts/
│       └── tools/runner.py
└── site/                              ← Next.js SaaS app ✅ LIVE (split 2026-09-06)
    ├── middleware.ts                  ← refreshes the Supabase session on page routes
    ├── app/
    │   ├── page.tsx                   ← SCOUT — the matchday board (home)
    │   ├── lab/page.tsx               ← LAB — hypothesis tester (was /test)
    │   ├── agent/page.tsx             ← AGENTS — the signed-in user's own, private
    │   ├── wallet/                    ← WALLET — analyser, now a public tab
    │   ├── login · account · pricing  ← accounts and the paid plan ✅ NEW
    │   ├── auth/callback/route.ts     ← OAuth / magic-link code exchange
    │   ├── api/me · api/stripe/{checkout,portal,webhook}
    │   ├── lib/plan.ts                ← tiers + the quota gate (server only)
    │   ├── lib/agents.ts              ← a user's agents: owner check in the SQL
    │   ├── api/agents/…               ← list · save · run/pause/archive · trades
    │   ├── lib/stripe.ts              ← inert without keys, by design
    │   ├── lib/supabaseEnv.ts         ← url + anon key, shared, with fallback
    │   ├── lib/supabaseBrowser.ts     ← client half — NEVER merge with the server half
    │   ├── lib/supabaseAuth.ts        ← server half (imports next/headers)
    │   ├── lib/useSession.ts · lib/useWatchlist.ts
    │   ├── game/[slug]/page.tsx       ← GAME CENTER — tabs: Overview · Stats · Match · Markets
    │   ├── game/[slug]/{Insights,StatsTab,MatchTab}.tsx · fmt.ts
    │   ├── api/game/context · api/game/brief   ← team form + ESPN + pressure · the AI brief
    │   ├── lib/teamform.ts            ← our DB → form, streaks with rarity, H2H, vs closing price
    │   ├── lib/espnMatch.ts           ← ESPN summary: line-ups, timeline, box score, table
    │   ├── lib/pricedLike.ts          ← "matches priced like this" (priced_like.json)
    │   ├── lib/matchbrief.ts          ← Claude brief, cached per fixture per phase
    │   ├── lib/momentum.ts · lib/matchcontext.ts · lib/teamname.ts
    │   ├── api/scout/route.ts         ← the board  ·  api/pulse → the tape's 7 numbers
    │   ├── api/game · api/backtest · api/wallet
    │   ├── lib/venues.ts              ← venue vocabulary: fees, book grade, the best-price pick
    │   ├── lib/kalshiSoccer.ts        ← Kalshi's football, swept · lib/kalshiGame.ts per market
    │   ├── lib/venueMatch.ts · venueMerge.ts · etDate.ts · teamMatch.ts · clob.ts
    │   ├── components/BoardView.tsx   ← ONE board for football and the six US sports
    │   ├── lib/scout.ts               ← Gamma sweep → fixtures + book grades
    │   ├── lib/espn.ts                ← live clock fallback where PM has none
    │   ├── lib/scoutCache.ts          ← 45s TTL + in-flight coalescing, shared
    │   ├── lib/gamecenter.ts · lib/looks.ts · lib/wallet.ts · lib/backtest.ts
    │   ├── components/AppShell.tsx    ← tape · nav · tabs · account actions · footer
    │   ├── components/icons.tsx       ← inline SVG on currentColor
    │   ├── components/EquityCurve.tsx ← the agent's record as a chart
    │   ├── layout.tsx                 ← root layout + SEO metadata
    │   └── globals.css                ← design tokens + every component layer
    ├── package.json                   ← Next.js 14.2, React 18.3, Supabase JS 2.43
    ├── tailwind.config.ts
    ├── next.config.mjs                ← redirects for the routes that moved
    ├── vercel.json
    ├── SETUP.md                       ← local dev + Vercel deployment guide
    └── .env.local                     ← Supabase URL + anon key (gitignored)
```

---

## Database (Supabase / Postgres)

Schema is live. Key tables:

| Table | Purpose |
|---|---|
| `leagues` | 22 domestic competitions loaded (Big 5 + lower divs + smaller European) |
| `seasons` | One row per league per season |
| `teams` | Canonical team identity |
| `team_aliases` | Source-specific names (FD, FBref, Betfair all differ) |
| `matches` | One row per match with result (~124k rows) |
| `match_stats` | xG, shots, corners, cards (~91k rows; 16k with xG) |
| `match_odds` | Opening + closing odds (Pinnacle, Bet365, Betfair Exchange, etc.) |
| `nba_odds` | NBA closing ML/spread/total, 2014-15→2021-22 (consensus, not Pinnacle) |
| `bt_nba` | Flattened NBA backtest features — 10,006 games ✅ |
| `research_hypotheses` | Legacy — hypothesis log from retired research pipeline |
| `backtest_runs` | Legacy — backtest results |
| `strategies` | Active trading strategies (Sharp Consensus, Poisson, ELO, DC, NBA Elo, Sim Pre-Match, Sim In-Play) |
| `paper_trades` | Every position the agent takes on Polymarket ✅ ACTIVE |
| `pm_markets` | Polymarket market metadata + snapshots |
| `agent_runs` | Log of agent invocations ✅ ACTIVE |
| `data_ingestion_log` | What was ingested, when, from where |

Connection string is in `ingest/.env` as `DATABASE_URL`.

---

## Data pipeline status

### Stage A — Football-Data.co.uk ✅ COMPLETE
- **~124,000 matches** across 22 European leagues
- 2010-11 → 2025-26 (current season live)
- Results, half-time scores, match stats, closing odds from 9 bookmakers
- **Pinnacle opening odds** (columns PSH/PSD/PSA → stored as "Pinnacle (legacy)", ~95k records)
- **Pinnacle closing odds** (columns PSCH/PSCD/PSCA → stored as "Pinnacle (closing)", ~95k records)
- **Betfair Exchange closing** (BFEX, ~13k records)
- Both Pinnacle opening + closing are present → CLV can be measured without Stage C
- Idempotent. CSVs cached to `.cache/fd/`

**To re-run or update:**
```bash
cd ingest && source .venv/bin/activate
python stage_a_football_data.py --continue-on-error
```

### Stage B — FBref xG enrichment ✅ COMPLETE
- **16,217 matches** with xG data
- Big 5 leagues (PL, La Liga, Serie A, Bundesliga, Ligue 1)
- Seasons: 2014-15 → 2024-25
- Populates `match_stats.home_xg`, `away_xg`
- HTML cached to `.cache/fbref/`

**To extend or update:**
```bash
cd ingest && source .venv/bin/activate
python stage_b_fbref_xg.py --continue-on-error
```

### Stage B (v2) — Understat xG ✅ COMPLETE
- Alternative xG source using `understatapi` library
- Replaces FBref scraper for cases where FBref rate-limits

### Stage C — Polymarket integration ✅ LIVE (PRIMARY VENUE)
Polymarket is now the **primary trading venue**. Markets feed running, paper trades
flowing into `paper_trades`, settled trades visible on the public site. Operating in
**paper-trading-only mode** for now (1u flat, immediate Twitter posts with signed
timestamps).

Tables:
- `pm_markets` — live + historical Polymarket markets
- `paper_trades` — every position the agent takes on Polymarket

### Stage C (v2) — ClubElo ratings ✅ COMPLETE
- Downloads ELO rating histories from api.clubelo.com
- Provides pre-match strength estimates for all clubs

### Stage D — The Odds API ✅ LIVE
- Fetches Pinnacle + Betfair Exchange h2h odds for active competitions
- Powers the PM-vs-Sharp Consensus strategy with live sharp prices

### Stage E — In-Play Monitor ✅ BUILT
- Polls live football matches every N minutes
- Feeds the PM-vs-Poisson In-Play strategy
- Requires `FOOTBALL_API_KEY` in `.env`

### Stage H — NBA historical closing lines ✅ COMPLETE
- **10,006 NBA games** with closing moneyline, spread and total (`nba_odds`, `bt_nba`)
- Seasons **2014-15 → 2021-22 only** — the source archive is unmaintained and
  the original host no longer serves the files, so the range cannot be extended
- Source: `github.com/flancast90/sportsbookreview-scraper` (MIT), a pre-scraped
  sportsbookreview.com archive. **Consensus close, not Pinnacle.**
- Spread/total prices are not archived → backtests assume -110 (1.909)
- No opening price → **no CLV arm for the NBA**
- ~7.4% of source rows have spread/total swapped; the loader repairs them
- Powers the NBA arm of the Hypothesis Tester (`run_backtest_nba`)

**To run:**
```bash
cd ingest && source .venv/bin/activate
python stage_h_nba_odds.py              # download (cached) + load + refresh bt_nba
python stage_h_nba_odds.py --dry-run    # parse and report, no writes
```

### Stage G — International results ✅ COMPLETE
- **8,394 matches** from Mart Jürisoo's international results dataset
- Covers: FIFA World Cup (finals + qualifiers), Euro, Nations League, Copa América, CONCACAF, AFCON, AFC Asian Cup, friendlies (2015+)
- All **48 World Cup 2026 teams** in the DC model
- Source: `github.com/martj42/international_results`
- Teams auto-created with `source='international'` aliases

**To run:**
```bash
cd ingest && source .venv/bin/activate
python stage_g_international.py                  # full run (2010+)
python stage_g_international.py --min-year 2020  # recent only
```

### Stage I — Kalshi market ingestion ✅ READ-ONLY (Phase 1)
- Second venue, **data only**. Kalshi market data + orderbooks are public — no
  API key, no account, nothing in this stage can place an order.
- Writes into the same tables as Polymarket: `pm_markets` (platform='kalshi')
  and `pm_market_snapshots`.
- Taxonomy: Series (`KXEPLGAME`) → Event (one fixture) → Market (one outcome).
  95 soccer *game* series covering ~29 competitions; ~1,070 soccer series in
  total across all market families (totals, BTTS, spreads, 1st half, correct
  score, method of victory).
- Event titles are **"Home vs Away"** — verified against ESPN (Kalshi's own
  settlement source) on 3/3 fixtures, 2026-07-22. Re-verify if titles change
  shape; this is the bug class that bit the NBA scanner.
- `match_id` is left NULL (same as Stage C — `matches` is historical, so future
  fixtures have nothing to link to). Cross-venue joins go through
  `raw_metadata->'_np'`: `{home, away, match_key, side, competition, spread}`.
- ⚠️ ~26% of markets quote a placeholder book (0.02 / 0.81 on every outcome).
  The mid is meaningless there — **filter on `_np.spread` before using a price.**
- Legacy integer-cent fields (`yes_bid`, `yes_ask`, `liquidity`) now return
  null; the live ones are the `*_dollars` / `*_fp` variants.

**To run:**
```bash
cd ingest && source .venv/bin/activate
python stage_i_kalshi.py --probe                        # inspect API shape
python stage_i_kalshi.py --dry-run                      # parse, no writes
python stage_i_kalshi.py                                # 1X2 only (default)
python stage_i_kalshi.py --families GAME,TOTAL,BTTS     # wider coverage
python stage_i_kalshi.py --series KXEPLGAME             # one competition
```

**Kalshi economics vs Polymarket** (matters before anything trades there):
- Taker fee `ceil(0.07 × C × p × (1−p))` — **40% more expensive** than PM's 0.05.
  Maker fee is 25% of that (`0.0175`).
- ⛔ **Geo:** Portugal appears on the restricted list in secondary sources.
  Unverified against the Member Agreement. Data ingestion is unaffected;
  real-money execution from PT may simply not be possible.

### Betfair historical opening prices — PARKED
Not strictly required — Pinnacle opening odds serve as the entry price proxy and
Pinnacle closing as the CLV benchmark. Can be added later if needed.

Kalshi is also parked — build after Polymarket edge is confirmed at scale.

---

## Agent architecture ✅ BUILT

Two deterministic trading strategies + resolver. No LLM in the loop — pure math.

```
agent/run.py  (CLI entry point)
└── orchestrator.py       — runs strategies + resolver sequentially
    ├── paper_trader.py   — Strategy 1: PM-vs-Sharp Consensus (pre-match)
    ├── poisson_trader.py — Strategy 2: PM-vs-Poisson In-Play
    └── resolver.py       — resolves settled trades, calculates CLV
```

**To run:**
```bash
cd agent && source ../ingest/.venv/bin/activate
python run.py                              # one cycle: both strategies + resolver
python run.py --strategy consensus         # only pre-match scan
python run.py --strategy poisson           # only in-play scan
python run.py --cycles 5 --interval 900   # 5 cycles, 15 min apart (in-play daemon)
python run.py --dry-run                    # no DB writes

# Standalone scanners (not in run.py)
python dc_scanner.py                       # DC pre-match (cron: 8:00 UTC)
python sim_scanner.py                      # MC sim pre-match (new — runs every market type)
python inplay_sim_scanner.py               # MC sim in-play (new — same engine + live state)
python nba_scanner.py                      # NBA Elo pre-match

# Sim engine demo / calibration
python sim_demo.py                         # sanity-check sim vs analytical Poisson
```

**API keys** in `ingest/.env`:
- `THE_ODDS_API_KEY` — sharp odds from The Odds API (Pinnacle + Betfair)
- `FOOTBALL_API_KEY` — live scores for in-play strategy (optional)
- `ANTHROPIC_API_KEY` — not needed for current strategies

---

## Current pipeline state (as of 2026-05-23)

| Stage | Status |
|---|---|
| DB schema (4 migrations) | ✅ Live |
| Stage A — Football-Data match data | ✅ 124k matches, 22 leagues, 2010–2026 |
| Stage B — FBref xG | ✅ 16k matches, Big 5, 2014–2025 |
| Stage B (v2) — Understat xG | ✅ Alternative xG source |
| Stage C — Polymarket markets | ✅ Live, feeding paper trades |
| Stage C (v2) — ClubElo ratings | ✅ ELO histories loaded |
| Stage D — The Odds API (sharp) | ✅ Live Pinnacle + Betfair odds |
| Stage E — In-Play Monitor | ✅ Built (needs FOOTBALL_API_KEY) |
| Live stats — api-football | ⚠️ Primary, but quota exhausts nightly and xG has been 0% since 09-02 |
| Live stats — ESPN | ✅ Free fallback, no key (`agent/espn_stats.py`), auto-engages on refusal |
| Live clock — Polymarket | ✅ `live`/`score`/`period`/`elapsed` on the event itself; Scout's primary |
| Stage F — NBA pipeline | ✅ 15k games, Elo model, scanner |
| Stage G — International results | ✅ 8,394 matches, 48 WC teams in DC model |
| Stage I — Kalshi markets (read-only) | ✅ 930 markets / 310 fixtures / 29 competitions |
| Odds — Pinnacle opening | ✅ ~95k records ("Pinnacle (legacy)") |
| Odds — Pinnacle closing | ✅ ~95k records ("Pinnacle (closing)") |
| Odds — Betfair Exchange closing | ✅ ~13k records |
| Strategy 1 — PM-vs-Sharp Consensus | ✅ Built and running |
| Strategy 2 — PM-vs-Poisson In-Play (legacy) | ✅ Built; O/U classifier bug fixed 2026-05-23 |
| Strategy 3 — PM-vs-ELO Pre-Match | ✅ Built |
| Strategy 4 — DC Model Pre-Match | ✅ Daily cron at 8:00 UTC |
| Strategy 5 — NBA Elo Pre-Match | ✅ All PM market types (ML/spreads/totals) |
| Strategy 6 — Sim Model Pre-Match | ✅ NEW — MC sim, 15 market keys (HT/FT/totals/BTTS/handicaps) |
| Strategy 7 — Sim Model In-Play | ✅ NEW — same MC sim from in-play state |
| Over Late Goals — observation | 🔬 Paper only, no orders. Fair value known, PM side unmeasured |
| Live Pressure Overs (strategy 16) | 🔬 Paper only — the only agent still running. **obs_version 4** (book gates, db/037) |
| Live Pressure HT Over 0.5 (strategy 17) | 🔬 Paper only, first-half arm of the same signal. **obs_version 4** (min odds 1.75 + armed monitoring) |
| Live Pressure Favourite HT (strategy 18) | 🔬 Paper only, favourite ahead at HT. **obs_version 3** (rolling reading, entry to 40') |
| Monte Carlo sim engine | ✅ Vectorized, 50k sims in <600ms; passes Poisson sanity |
| InjuryTracker + MarketFlow | ✅ Real-time injury / whale-money signals |
| Resolver | ✅ Settles trades + calculates CLV |
| Public website | ✅ SaaS app at [nopredictions.com](https://nopredictions.com) — Scout · Lab · Agent · Wallet |
| Both venues on every board + the Game Center | ✅ NEW — one `BoardView`, cheaper exchange marked net of fees |
| s16 / s17 buy at the cheaper exchange | ✅ NEW — obs_version 6 / 7, db/054, `H-BEST-VENUE` |
| Git repo | ✅ Remote: github.com/davidhmsilva/nopredictions |
| X / Twitter launch | ⏳ Pending first edge results |

---

## Over Late Goals — observation phase (no money)

Thesis: at minute 70-88, in a match the market expected goals from, PM's live over
line may be too cheap. Fair value comes from `goal_events` (Understat, minute-level,
16,479 clean matches) — **not** from the MC sim, which puts 49.5% of goals in the
first half against 44.1% real and so underprices late overs by construction.

What the data already settles:
- Conditional on the goals scored so far, the **pre-match total still predicts a late
  goal**: 1 goal at 75' → 44.9% (2.23) in the lowest pre-match bucket vs 53.0% (1.89)
  in the highest. n=4,444, CIs disjoint. This is the filter to key on.
- **League identity is the weak axis** — 4.5pp across the Big 5 with overlapping CIs,
  against 14pp for the pre-match total. Public "late-goal league" tables run on n=5-11
  games per league and are noise.
- **Cash-out on the first goal loses; leverage does not.** Backtested on 1,559 real
  matches at 1 goal / 75': selling on the first goal is worse than holding the same
  line at every assumed bias, because you sell back into the same mispricing you
  bought and pay fee + spread twice to do it. But *holding* the two-goals-away line
  is a genuinely different bet — if PM misprices the goal rate the error compounds,
  and Over 2.5 held beats Over 1.5 held above ~10% rate error (24.9% vs 12.9% at 20%).
  Which line is right depends on the FORM of PM's error, which is still unknown, so
  the observer records BOTH lines every poll (`goals_needed` 1 and 2). `would_enter`
  stays on the one-more-goal line until the shape of that error is measured.
- **Football is underdispersed vs Poisson late on**, increasingly so: empirical
  P(>=2 more) is 0.90x the Poisson value at 68' and only 0.63x by 86'. Two goals need
  time between them; Poisson does not require that. So never extrapolate the two-goal
  line from the one-goal line — the table carries a measured `p2`. It also means any
  Poisson-ish pricer (plausibly PM's, certainly our MC sim) *overprices* the leveraged
  line late, which would run the compounding-edge argument in reverse.
  Check with `python late_goals_table.py --calibration`.

What is NOT known: whether PM actually misprices it. `pm_ticks` only starts 2026-07-21,
so there is no history to mine. `late_goal_observations` (db/029) accumulates it.

```bash
cd agent && source ../ingest/.venv/bin/activate
python late_goals_table.py                 # rebuild the fair-value table
python late_goals_observer.py --once       # single cycle
python late_goals_observer.py              # forever, 60s
python late_goals_observer.py --settle     # backfill outcomes
python late_goals_observer.py --report     # what has been collected
```

Runs 24/7 via `late_goals_daemon.sh` (cron `0 * * * *`, `--settle` at `40 */2 * * *`).
The wrapper holds an atomic `mkdir` lock on `/tmp/nopredictions_late_goals.lock`:
guarding on the observer process alone did NOT hold, because the wrapper sleeps 30s
between restarts and a cron firing inside that window stacked a second forever-loop
(three observers found live 2026-08-04). Entry universe = displayed pre-match
over-2.5 price of **1.80 or shorter**, which is `pre_over25 >= 0.534` de-vigged —
*not* 1/1.80 = 0.5556, see the calibration note in the source.

⚠️ **`obs_version = 1` (2026-07-22 → 08-04, 73k rows) is unusable — do not analyse it.**
Two defects, both fixed in db/030 + the observer on 2026-08-04:
- **Clock.** `game_minute` came from PM's listed start time with nothing checking it.
  On smaller-league fixtures that time runs ~30 min ahead of the real kick-off, so
  recording stopped around real minute 60: on 26 settled fixtures the true full-time
  total was 2.65 against 1.69 at our "minute 90" — **0.96 goals arrived after we
  stopped watching.** The fair-value lookup then got a minute far later than reality,
  which is the whole reason PM sat above our fair on 4,862 of 4,916 eligible polls.
  That 99% "no edge" reading measured our clock, not a price.
- **Score.** `infer_goals()` reads Gamma's `outcomePrices`, which lag the CLOB. Where
  api-football could referee, the ladder was wrong on **29% of polls** (305 of 1,048),
  under-reading the score in 181. The existing cross-check hides this — a disagreement
  downgrades the row to `certain=false`, so the survivors agree 743/743 by construction.

v2 gates every entry on `clock_verified` (api-football's elapsed minute, or a
quota-free kick-off detection from the ladder's first movement) **and**
`book_confirmed` (the CLOB agrees with Gamma on both rungs pinning the score).
Settlement also refuses to label a token that never resolved — 13% finish mid-book,
where `last >= 0.5` is a coin flip biased by the price, not an outcome.

Verdict gate: n>=200 settled observations at `obs_version >= 2` with a certain score
AND a yield CI clear of zero after the taker fee. Nothing below that is a result.
The v1 rate was 9 entries in 13 days, so at that pace the gate is ~9 months away —
expect to have to widen the universe before it is reachable.

## Live Pressure HT Over 0.5 — first-half arm (paper, 2026-08-19)

Same signal as Live Pressure Overs, one market over. The rule: a fixture is still
**0-0**, the clock has passed **15 minutes**, and those first 15 minutes were
played at **high pressure** → buy PM **"1st Half O/U 0.5"** (over), 1u, paper —
**and never at less than 1.75** (obs_version 4). Pressure and price are two
separate events: a fixture that presses at a shorter price is **armed** and
monitored, and enters on the first later poll where the price has arrived and
the book is still clean.
Strategy id **17**, hypothesis **H-PRESSURE-1H**, table `ht_pressure_observations`
(db/033), public view `v_ht_pressure_trades`.

```bash
cd agent && source ../ingest/.venv/bin/activate
python first_half_table.py            # rebuild the empirical baseline
python ht_pressure_agent.py --once --dry-run
python ht_pressure_agent.py --report  # entries, skip reasons, pressure buckets
```

**It runs inside `pressure_agent.py`, not as its own daemon.** Both arms price the
same api-football poll; a second process would double the live calls and split the
stats budget, and that budget is what once mislabelled 36,917 rows as "no
coverage". The existing crontab needs no new line — `pressure_daemon.sh` drives
both arms and `--settle` settles both.

What is measured, and what is a guess:

- **Fair value** — `first_half_table.py`: P(goal before HT | 0-0 at minute m,
  pre-match total bucket), from `goal_events` for the STATE and the half-time
  score in `matches` for the OUTCOME. Understat's minute folds first-half stoppage
  into the neighbouring minutes and disagrees with the half-time score on 1.4% of
  matches, and a 45+2 goal is exactly what this market pays on — so the outcome
  never comes from `goal_events`. Clean universe (count(goal_events) == final
  score), n ≈ 1,400-4,800 per cell. The pre-match total survives into the 0-0
  state: **at 15', lo bucket 49.6% (2.02) vs hi bucket 64.5% (1.55)**.
- **Pressure** — the danger index on the shared 0-100 axis
  (`live_tracker.danger_index`, a module-level function). Since **obs_version 3**
  it is re-read at EVERY poll rather than frozen at 15-18': cumulative-scaled up
  to 18', the **rolling 15-minute window** after that
  (`ht_pressure_agent.current_pressure`, shared by both first-half arms). A
  fixture with no window baseline still gets no reading and no entry. The frozen
  15-18' number is still written to `opening_pressure` on every row as the
  CONTROL — see [Unfreezing the reading](#unfreezing-the-reading--obs_version-3-2026-09-05).
- **MIN_PRESSURE = 25** — calibrated to FREQUENCY, not profitability. Recomputing
  this index on 165 real fixtures that already have a 15-18' stats row gives
  median 11, p75 16, p90 23, p95 26, p99 43. The sibling's 45 would fire on
  **0.6%** of openings, i.e. about once a month. 25 ≈ the top decile.
- **⚠️ xG is 40% of the index and api-football supplies it on only ~half the
  fixtures it covers.** A fixture without xG reads far quieter than it played, so
  it effectively cannot clear the bar. Recorded per row as `has_xg` — a control
  for the fit, not a silent difference between two rows that look identical.

⚠️ **This starts from behind, on purpose.** PM's price on this exact market sat
**ABOVE** the realised frequency at every minute tested from 5' to 40' (n=236
fixtures reconstructed from the CLOB, ~4pp, CI crossing zero). The generic over is
rich; the pressure filter has to beat ~4pp + fee + spread before it is worth
anything. Liquidity is micro too — $938 seen on this line against $32,718 on the
same fixture's full-match O/U 2.5 — hence `MIN_DEPTH_USD = 25`. Expect the book,
not the signal, to be the binding constraint; `--report` breaks entries down by
skip reason for exactly that question.

Verdict gate: n >= 200 settled entries AND a yield CI clear of zero after the
taker fee AND the pressure arm beating the base arm recorded on the same rows.

⚠️ **`obs_version 2` since 2026-08-31** — adds `MAX_SPREAD = 0.06` and a
two-sided-book requirement; the $25 depth floor is unchanged because depth is
non-monotone on this book. Never pool v1 and v2 entries. And the aggregation
question this arm's mean raises is now closed: no combination of the two danger
indices beats minute + pre-match total (db/037, `H-PRESSURE-1H-AGG`). See
[The 100-game review](#the-100-game-review--book-quality-not-pressure-2026-08-3031).
⚠️ **`obs_version 3` since 2026-09-05** — the reading is no longer frozen and the
entry window runs to 40'. Same thresholds, same book gates.
⚠️ **`obs_version 4` since 2026-09-06** — `MIN_ODDS = 1.75`, with the pressure
gate latched. See [The 1.75 floor](#the-175-floor--a-price-gate-that-is-mostly-a-clock-gate-2026-09-06).
⚠️ **`obs_version 5` since 2026-09-06** — the pressure gate is **re-applied at
the moment of entry**. v4 entered on `(pressing or armed)`, so a fixture that
cleared the gate once could be bought minutes later purely because the price had
walked out, with the match already quiet. Measured on v4's own 11 entries: 9
were `live` (pressure 23.1 at entry) and **2 were `armed` with the reading
collapsed below the gate** — Henan v Chengdu armed at 26' on 19.6 and entered at
34' on **12.7**; Cracovia v Gornik armed at 15' on 21.3, in at 18' on **14.0**.
Arming survives and still holds a fixture under watch while the book walks out;
what it no longer does is stand in for a reading. An armed fixture whose price
arrived but whose pressure has gone gets its own skip reason, so the population
v5 gives up stays countable.

---

## Live Pressure Favourite HT — third arm (paper, 2026-08-19)

Buy the pre-match favourite to be **ahead at half time**, but only once it has
shown it is living up to the price. Strategy id **18**, hypothesis
`H-PRESSURE-FAV`, table `fav_ht_observations` (db/034), view `v_fav_ht_trades`.

The rule: clear pre-match favourite (de-vigged PM 1X2 **>= 0.50**, captured
**before kickoff**), still **0-0**, minute **15-40**, and over the last 15
minutes of play the favourite both **pressed hard** (own index >= 19) and
**out-pressed the underdog** (gap >= 20). Then buy PM's
`"<Favourite> leading at halftime?"`, 1u, paper.

```bash
cd agent && source ../ingest/.venv/bin/activate
python favourite_ht_table.py           # rebuild the empirical baseline
python fav_pressure_agent.py --once --dry-run
python fav_pressure_agent.py --report
```

Runs inside `pressure_agent.py` like the other two arms — same poll, same stats
budget, no new cron line.

**Why the dominance term is the whole strategy.** The favourite's strength is
already in the price by construction, so backing favourites is not an edge. The
candidate signal is the joint event — favourite, visibly on top, and still level —
where two of the three inputs are public and instant and the third arrives on a
slower feed. `edge_base_pp` (price vs the plain empirical table) is recorded on
every row as the null: if the dominance arm does not beat it, this is just
favourite-backing with extra steps.

**What is actually being bought.** "Leading at halftime" resolves NO on every
half-time draw, which is the modal outcome from 0-0 at 15' (43-57%). Fair value
runs **2.11** (strong away favourite) to **4.26** (weak home favourite) — a
value-priced bet on a 30-minute window, not a favourite-backing engine.

Empirical table (`favourite_ht_table.py`), same universe convention as the other
two, state from `goal_events`, outcome from the half-time score, favourite from
vig-free Pinnacle 1X2 opening:

| 0-0 at 15' | P(win) < 0.45 | >= 0.65 |
|---|---|---|
| favourite home | 23.5% (4.26) | 45.3% (2.21) |
| favourite away | 25.7% (3.90) | 47.3% (2.11) |

n ≈ 270-2,500 per cell. Venue is kept as its own dimension and never pooled away.

⚠️ **Side errors here invert the bet, they do not blunt it.** So: the favourite is
read from PM's pre-kickoff 1X2 swept across the WHOLE listed universe (not when
the match goes live — by minute 15 that book has been drifting for a quarter of an
hour); team names are resolved against **api-football's** home/away with the
alias-aware scorer, never against PM's title order; ambiguity fails closed; and
the "<team> leading at halftime?" market is resolved independently by the same
rule, so the team measured and the market bought have to agree.

Thresholds are calibrated to FREQUENCY on the 165 fixtures with a real 15-18'
stats row — per-side danger p50 11 / p75 19 / p90 29, gap p50 12 / p75 18 / p90 28
— never to an outcome. Verdict gate: n >= 200 settled entries, yield CI clear of
zero after the fee, AND the dominance arm beating the plain-favourite arm.

⚠️ **`obs_version 2` since 2026-08-31** — adds `MAX_SPREAD = 0.03` (this book
breaks at 3pp, NOT the 6pp used on the sibling arms) and a two-sided-book
requirement; the $25 depth floor is unchanged. Never pool v1 and v2 entries.
⚠️ **`obs_version 3` since 2026-09-05** — the reading is no longer frozen and the
entry window runs to 40'. Same thresholds, same book gates.
⚠️ And the number no gate fixes: across 635 fixtures this market's ask sits
**8.49pp above the realised rate, CI[−11.94,−5.04]**. See
[The 100-game review](#the-100-game-review--book-quality-not-pressure-2026-08-3031).

---

## Two bugs that produced zero entries — fixed 2026-08-20

Both first-half agents ran a full day and entered nothing. Neither cause was the
funnel; both were defects in the measurement layer, found by tracing one fixture
poll by poll.

**1. Stats vanished on 2 polls out of 3.** Every poll builds a NEW `StatSnapshot`
and only the fixtures that win a paid call get theirs filled — with `ENRICH_TTL_S`
at 180s against a 60s cycle, that is one poll in three. Nothing carried the last
known values forward, so a match measured at 15' reported shots 0-0 and no
pressure at all at 16' and 17':

```
15'  stats=True   shots=4/0  press=15.6
16'  stats=False  shots=0/0  press=None   <- "within TTL, using last fetch"
17'  stats=False  shots=0/0  press=None
18'  stats=True   shots=4/0  press=13.8
```

The first-half arms freeze their reading inside a four-minute window, so this
alone could lose a fixture entirely. The full-match arm was hit too — its window
deltas computed `max(0, 0 - 5) = 0`. Fixed with `_carry_stats_forward`: a snapshot
is now the last known state of the match, carrying `stats_minute` so consumers
know how old it is. Rate scaling divides by that, not by the poll's minute.
**Rows with stats went from 2.3% to 70%.**

**2. The uncovered-league blacklist was a latch.** Three empty responses
blacklisted a competition for the life of the process — which runs for weeks —
and the dict was never cleared. On 2026-08-20 it had **MLS** marked uncovered
while api-football was serving 5 shots to 8 for the very fixtures we refused to
ask about; restarting the process was the only cure. Now coverage is read from
the API's own `coverage.fixtures.statistics_fixtures` (one cached call per
competition), a league it confirms as covered is never struck on empty responses,
and the strike heuristic only applies when the API would not answer — with a 2h
expiry.

**Thresholds lowered 2026-08-20, by decision not by fit.** `MIN_PRESSURE` 25 → 19
on the first-half arm (top decile → top quartile: 11.0% of openings cleared 25,
26.5% clear 19, ≈2.4x the entries) and `MIN_FAV_PRESSURE` 30 → 19 on the favourite
arm. ⚠️ On the favourite arm that leaves `MIN_DOMINANCE = 20` doing all the work —
a side scoring 19 with a 20-point gap is arithmetically impossible — so its
candidate rate moves 19.3% → 34.8% by retiring the side term rather than by any
view of how dominant a favourite should look. Both hypotheses in db/033 and db/034
are written about the STRONG claim; `opening_pressure` is on every row so
"separates at 25 but not at 19" stays answerable from the data.

⚠️ **The fixes were necessary but are not sufficient.** On the 12 PM-listed
fixtures that did get a 15-18' reading yesterday, the highest opening pressure was
**22.3 against a gate of 25** — nothing would have entered anyway. Expect entries
every few days, not daily, until either the measurable pool grows (the fixes
should roughly triple it) or the threshold moves, which is a decision about what
"high pressure" means, not a bug fix.

---

## The 100-game review — book quality, not pressure (2026-08-30/31)

Strategy 16 reached exactly 100 entries (88 distinct fixtures; 12 matches entered
twice on different lines). **Yield −2.07% gross, ≈−4.6% after the fee, CI
[−22.4, +18.2].** That interval is ±20pp against a 2-4pp target, so the
settlement arm cannot answer anything and was set aside — the readings below all
come from the calibration arm (`real − ask`, one row per fixture/minute/score,
CIs clustered by fixture), which has two orders of magnitude more power.

**The pressure gate selects nothing. This is the test the three arms exist to
run**, and the control wins. `real − fair_base` by pressure quintile at 75-88':
**+4.82 / +10.82 / +13.84 / +9.61 / +7.29pp** — non-monotone, peaking in the
MIDDLE. On clean books `press>=45` gives **+2.35pp** against **+5.28pp** for
`press<45`: the gate throws away the better rows. The +5.8pp that entries show
against the table is not the signal — it is a uniform table bias present at
pressure zero (see below).

Same answer on the first-half arm, tested separately (db/037, `H-PRESSURE-1H-AGG`,
n=338 fixtures, control = minute + pre-match total, out-of-sample 5-fold CV,
paired Δ log-loss, bootstrap CI): **mean +0.00380 CI[+0.00278,+0.00498]** (worse
than the control), **max +0.00265 CI[−0.00218,+0.00750]** (indistinguishable from
zero). Terciles flat within ±9pp. And **`sum` = 2 × `mean`** is a monotone
rescale, so that half of the mean-vs-sum question was never a real choice.
⚠️ `MIN_PRESSURE` was left in place on all three arms anyway: removing it is a
decision about what these strategies ARE, not a bug fix.

**`late_goals_table` runs ~6pp low at every minute** — `real − fair_base` =
+5.68 / +5.98 / +5.98 / +6.19pp across 70-74 / 75-79 / 80-84 / 85-89, CIs clear
of zero, 470 fixtures. Uniform, so not noise. Every recorded `edge_base_pp` is
inflated until it is refitted, and `PRESSURE_NEUTRAL = 22.0` has the same problem
(measured on the pre-v3 axis, never re-measured after the no-xG renormalisation).

**What was actually shipped: gates on the BOOK, measured per market.**

| `real − ask` by spread | s16 (n=6,449/494fx) | s17 (n=3,420/552fx) | s18 (n=4,654/635fx) |
|---|---|---|---|
| 0-3pp | +3.64 | +0.41 | −1.83 CI[−5.6,+2.0] |
| 3-6pp | +0.92 | −0.77 | −6.97 CI[−11.3,−2.7] |
| 6-10pp | −4.26 | −6.01 CI[−11.0,−1.0] | −7.88 CI[−13.6,−2.1] |
| 20pp+ | −38.38 CI[−44.3,−32.5] | −38.93 | −43.86 |
| **shipped `MAX_SPREAD`** | **6pp** | **6pp** | **3pp** |

The 20pp+ bucket is not a market: on s16 that is 430 rows / 221 fixtures quoting
an ask near 0.90 that resolves at 0.529 — a lone sell order parked far from any
bid. **`MAX_ASK` cannot catch these; the defect is the empty ladder, not the
price level.** Thresholds are deliberately NOT copied between arms — s18 breaks
at 3pp, where 0-3pp is its only bucket whose CI still contains zero.

🔑 **The spread is the tell, not the depth.** CA Mineiro vs EC Vitória,
2026-08-29 at 15': the 1st Half O/U 0.5 book quoted **bid 0.55 / ask 0.99 on
$30,117 of depth** and traded at 0.56 two minutes later. Every depth floor passes
that quote. Depth was raised only on **s16 ($50 → $1000**, monotone: $0-200
−4.72 · $200-1000 −0.49 · $1000-5000 +3.63 · $5000+ +4.61); on s17/s18 it comes
out non-monotone and the $25 floor is unchanged.

**What the gate buys.** The apparent "PM's late over is expensive" reading
(−5.11pp at 81-83', −6.02pp at 84-86', CIs clear of zero) is ENTIRELY an artefact
of those books. On a clean book the ask is fair to slightly cheap at every minute:
+2.13 / +2.86 / +4.66 / +2.98 / +3.92pp across 70-74 / 75-78 / 79-82 / 83-86 /
87-89. It removes a measured bleed; it does not find an edge — every one of those
positives still has a CI crossing zero, and deep/tight books belong to the larger
competitions, a confound that is not controlled.

⚠️ **The in-sample splits below are NOT a result** — the thresholds were fitted on
the same rows. They are why the gate is worth forward-testing, nothing more.
Pre-registered as `H-PRESSURE-BOOK` (db/037), forward-only, primary test on all
observation rows carrying a book rather than on entries.

```
s16   85 -> 56 entries    -0.86% -> +5.31%   (dropped: -12.41%)   <- CORRUPTED, see below
s17   46 -> 34 entries    -9.41% -> -0.62%   (dropped: -33.95%)
s18   31 -> 20 entries   -14.22% -> +5.63%   (dropped: -45.16%)
```

⚠️ **The s16 line above was computed on three fabricated wins** (db/038, fixed
2026-08-31). Repaired and re-run on all 108 settled entries: **all −2.93%, gate
pass n=76 +1.03%, gate drop n=32 −12.33%**. The gate still separates; the level
does not survive. s17 and s18 were never affected.

⚠️ **Strategy 18's market is expensive outright**, and no gate fixes that: across
all 635 fixtures its ask sits **8.49pp above the realised rate, CI[−11.94,−5.04]**.
Even the tightest bucket is −1.83pp. The dominance signal has to beat that first.

⚠️ `payout_units` is GROSS — the ~1.18pp average fee (≈2.5% of stake) is not
deducted anywhere in `--report`.

---

## Strategy 16 settled off its own tape, not the score — fixed 2026-08-31

Found from a single user report: *Aberdeen vs Rangers, Over 1.5 — this result is
wrong.* It was. The match finished **0-1** (Shankland 69', the only goal), so
Over 1.5 lost; pt#5827 was booked **won** at 4.348.

`settle()` took `final_goals = max(g for _, g in obs)` — the highest goal total
our own poll tape ever recorded for the fixture. **A maximum over a noisy feed is
not a final score.** api-football's live score flaps: this fixture read 0-1 to
82', **1-1 for three polls at 83-85'**, then 0-1 again to full time. The max
returned 2 and the over was paid.

🔑 **The error is one-sided by construction.** A flap can only push a maximum UP,
so every score-feed glitch lands as a fabricated WIN on an over and never as a
fabricated loss. Any yield read off these rows is biased high — which is exactly
the direction that makes a strategy look worth keeping.

Measured blast radius, all 108 settled s16 entries re-checked against
api-football's full-time score:

| | rows | fixtures |
|---|---|---|
| tape final TOO HIGH (phantom goal) | 9,388 | 216 |
| tape final TOO LOW (tape stopped early) | 14,307 | 304 |
| `goal_before_ft` flipped true→false | 6,652 | — |
| `goal_before_ft` flipped false→true | 6,143 | — |
| **paper trades flipped won→lost** | **3** | pt#5525, pt#5780, pt#5827 |

**23,695 of 248,602 settled observation rows (9.5%) carried the wrong outcome** —
and that is the calibration arm the whole 100-game review runs on. The "too low"
half is the uptime problem wearing a different hat: the tape stops when the Mac
sleeps, so its maximum under-reads. All repaired against the API.

**s17 and s18 were thought unaffected** because both ask api-football first. They
were not: the tape fallback and s17's events count were both wrong. See
[s17/s18 settled off the tape](#s17s18-settled-off-the-tape-and-off-an-empty-events-list--fixed-2026-09-13).

Fixed in `pressure_agent.py`: `_final_goals_api()` (batched `/fixtures?ids=`, 20
per call, returns a total only for FT/AET/PEN — a missing key means "not
settleable", never "0 goals"); the tape maximum survives only as a fallback for
fixtures the API will not answer; `goals_at_plus_10` is discarded when the tape
claims more goals than the match ever had; and the paper trade now settles on
**`final_goals > target_line`** — the line the token was actually bought on —
rather than "the score moved off what we read at entry", because where the tape
was wrong at entry the LINE is wrong too. `final_goals_source` on every row.

## s17/s18 settled off the tape AND off an empty events list — fixed 2026-09-13

Found from one paper trade: s18 pt#5977, Dunkerque v Saint-Étienne. The
favourite led **0-1 at half time (43')**, which api-football and ESPN both
confirm, and the trade was booked **lost**. The same shape as db/038: a
settlement that trusted something other than the half-time score.

1. **The tape fallback (both arms).** When the API did not answer, settle read
   our own poll tape. api-football holds `minute` at **45 through the break and
   into the second half**, so the tape carries dozens of "45'" rows, first-half
   flaps and second-half goals under the same label. s18's `max(near_ht,
   key=minute)` returned the first of them, a 0-0 flap one minute after the goal.
   s17's "tape reached 43', so 0 goals" fails the same way. **239 s18 rows over
   10 fixtures and 428 s17 rows over 12 fixtures had the wrong outcome.** That
   includes pt#5914 (Moreirense v Benfica) and pt#5977, both booked lost when
   they had won.
2. **s17's API path read an empty list as "no goal".** Its outcome was the
   `/fixtures/events` goal count. Leagues without event coverage return `[]`,
   and so does every ESPN (negative) id, which was being sent to api-football.
   Both were booked as **0 goals under source `'api'`**: **11,426 rows over 278
   fixtures** disagreed with `score.halftime`, 91% of them "no goal" where there
   was one. No s17 entry was affected; the calibration arm was.

Fixed in both `settle()` functions:
- The outcome comes **only** from `score.halftime`, via
  `fav_pressure_agent._halftime_scores` (batched, status-gated, never sends
  ESPN ids). `_first_half_goals_api` now only supplies the minute shown on the
  site.
- **No tape fallback.** An unanswered fixture waits for the next run. Past
  `SETTLE_API_MAX_AGE_H`, a row with no trade is closed with **no outcome**
  (`'no_api'`), so it costs no call on the next run. A traded fixture keeps
  being asked.

Repaired, with the before-state in `reports/ht_settlement_repair_2026-09-13.json`:
s18 622 rows / 260 flips, s17 12,413 rows / 11,854 flips, all tagged
`'api_repair'`. The s17 ESPN-id rows: 32 verified on ESPN (`'espn_repair'`), 74
unverifiable and left with no outcome (`'no_api'`). pt#5914 and pt#5977 → won.
⚠️ On the API path only rows whose **outcome** was wrong were repaired. s17
`'api'` rows where the events count differs from the half-time score but the
outcome agrees still carry the events count in `ht_goals`.

## s16 rows settled before the whistle, and never again — fixed 2026-09-14

Found through the weekend report: 17 factory trades entered on 09-13 at 46-65'
never settled. `settle()` wrote a row as soon as EITHER horizon was known.
`goal_next_10` is known ten minutes after the row, which is before the whistle
for anything observed before ~70'. `_final_goals_api` had no full-time total
yet, so the row went out with `final_goals` NULL and `settled_at` set.
`pending` (`settled_at IS NULL`) never selected it again.

| before the fix | rows | fixtures |
|---|--:|--:|
| settled with `final_goals` NULL | 546,187 | 10,749 |
| … whose final was already on a later row of the same match | 519,483 | 10,162 |
| NULL by minute | 15-34' ≈ 100% · 50' ≈ 65% · 65' ≈ 15% · 70-88' 0.5% | |

🔑 **The final is a fact about the match, not about the row.** The 85' row of
the same fixture was settled after the whistle and had the API's final all
along, so almost everything completes with no API call. The factory only ever
saw the minority of rows before 70' whose first settle came late. That cost its
46-69' next-goal specs most of their power, so the "0 pass" on those specs said
less than it seemed to.

Tape finals (`poll`) had the same shape. They were taken the moment the tape
passed 88' while the API had no full-time total (stoppage time, or a refused
key), and never revisited. Compared with the API final already stored on the
same match: 45,154 poll rows, 8,574 with a different total and 2,965 outcome
flips. **7,206 read LOW** (the tape stopped before the last goal) and 1,368 read
HIGH (a goal that did not stand). No paper trade changed result: the one the
API contradicts, pt#5941 (Over 2.5), wins on either total.

Changes in `pressure_agent.py`:
- **`complete_finals()`** runs at the end of every settle, including runs with
  nothing pending. It covers rows with a NULL or tape final whose fixture was
  seen within `FINAL_API_MAX_AGE_H = 24`. Each row takes the API's answer, else
  an API final stored on another row of the fixture, else the tape (only past
  `MAX_MINUTE`, and only to fill a NULL). Two stored API finals that disagree
  resolve nothing; 2 fixtures are in that state. Updates are set-based, 500
  fixtures per statement, and every fetch happens before the first write.
- The +10 horizon is dropped when it claims more goals than the final (555
  rows). That is the db/038 rule, which could not run on a row settled before
  its final existed.
- **A trade pays only on an API final.** A tape-final trade waits for
  `complete_finals`, which pays it from the API, or from the tape once the
  fixture is `FINAL_API_MAX_AGE_H` old. A traded fixture is asked about however
  old it is.
- `_final_goals_api` never sends ESPN (negative) ids, and it stops at the first
  refusal.

The repair ran as `python pressure_agent.py --backfill-finals`. It used stored
API finals only, with no API call and no tape, and completed **564,637 rows over
10,201 fixtures** in 6 minutes. Before 70', rows with no final went from 76% to
3.6%. The before-state (45,549 rows) is in
`reports/pressure_final_backfill_2026-09-14.json`, and completed rows are tagged
`'api_repair'`.
⚠️ Some fixtures still have no stored API final: 535 carry NULL rows (26,704)
and 517 carry tape rows (28,375). Fixtures inside the last 24h are asked about
by the cron. Older ones need a run with the API once the key answers. No daemon
restart is needed, because settle runs from cron.

### The key ran out again on 2026-09-14, and the settle cron was never counted

From ~14:00Z, `/status` answered *"You have reached the request limit for the
day"*, and every `ids=` batch came back with *"Free plans do not have access to
the Ids parameter"*. The tracker fell back to ESPN from ~16:00Z.
`_final_goals_api` swallowed the error and sent every remaining batch anyway.

`af_budget` flushed every 25 calls and never at exit. A short process like the
settle cron seldom reaches 25, so it was never written: on Monday its counter
still held Sunday's total. It now flushes at exit. By 18:00Z the recorded spend
was ~3.5k (pressure 1,367 + sweep 2,160). What settle spent that day is unknown;
from now on it is counted. So "whose calls exhaust the key" is still open.

## Rationing one api-football key — day vs evening (2026-09-06)

The blackout is real and it is a **daily allowance that resets at 00:00 UTC** —
four consecutive nights, recording dies in the evening and the first row back
lands at **00:02-00:03Z**. That retires the "their quota service is misreporting"
reading. It does not say whose calls spend it: our own measured usage is far
short of 75,000, and the headers still advertise 74,999 free while refusing
every endpoint including `/status`.

⚠️ **2026-09-05 (Saturday) went dark at 16:11Z**, one hour into the only window
whose boards we can trade, after a day in which **89% of everything recorded was
a fixture Polymarket does not list** (39,974 fixture-minutes against 4,711; 712
fixtures against 78). The stats budget was never the binding constraint — a busy
cycle spends ~7 of 40, throttled by `ENRICH_TTL_S` — so raising or lowering
`ENRICH_BUDGET_PER_CYCLE` was never going to help. Research was not displacing a
decision inside a cycle; it was displacing the whole evening.

```bash
cd agent && source ../ingest/.venv/bin/activate
python -m pytest tests/test_af_budget.py tests/test_sweep_feed_budget.py -q
cat agent/.af_calls_pressure.json agent/.af_calls_sweep.json   # today's spend
```

**`agent/af_budget.py` does two separate things.**

1. **Counts.** Every call is recorded at the call site, per UTC day and per hour.
   Two daemons share the key, so each writes its OWN file and reads the other's:
   one writer per file needs no lock, and a lock this repo forgets to release is
   a documented failure mode rather than a hypothetical. The running total is
   logged every cycle by both agents. 🔑 **Four blackouts were argued from `ok=`
   counts scraped out of a log after the fact; the number that settles it was
   the one number the log never carried.**
2. **Rations.** `EVENING_RESERVE = 45_000` of the 75,000 must still be unspent
   when `EVENING_START_H = 15` UTC arrives; the daytime gets the rest on a flat
   per-hour ceiling so the small hours cannot eat the afternoon either.

**Research is sampled, not switched off** — `RESEARCH_DAYTIME_SAMPLE = 4` keeps
one unlisted fixture in four before 15:00Z, everything after. 🔑 **The sample is
deterministic on the fixture id**, because a random per-cycle sample cuts the
same number of calls and destroys what they buy: the pressure arms difference a
15-minute rolling window, and a fixture measured at 22' and 31' but not 25' has
no window at all. A fixture sampled out records `stats not fetched: research
sampled out (daytime budget)` — never "no coverage". The tracker now lets the
caller name its own skip reason instead of flattening every `rank < 0` into
"deprioritised"; only the caller knows the difference, and collapsing two
reasons into one label is the mistake this file has paid for three times.

**The sweep's pending re-query was the largest uncontrolled call class.**
`/fixtures?live=all` drops a match the moment it ends — which is when the
post-whistle window opens — so every fixture seen live is followed with a
batched `/fixtures?ids=` lookup. That part is the thesis. What was not the
thesis: it re-asked **every** pending fixture on **every** 30-second cycle for
**three hours**. On a Saturday with 564 fixtures live that is a tail of finished
matches polled twice a minute long after anything can change. Now
`PENDING_FRESH_S = 15min` keeps the every-cycle cadence where the whistle
actually is and `PENDING_SLOW_S = 5min` backs off the tail — ~5.7x fewer calls
on that class, with the window the thesis depends on untouched.

⚠️ **This machine's clock is UTC+2**, so log timestamps run two hours ahead of
every UTC figure above. 15:00Z is 17:00 in the logs.

⚠️ **None of this proves the allowance is ours to spend.** It removes our own
worst daytime waste and makes the spend measurable; if the key is being consumed
elsewhere the counter will show a blackout arriving while our own total is low,
which is the discriminating measurement we have never had. The dashboard at
`dashboard.api-football.com` is still the only place that settles it.

## Settled-market sweep — observation phase (no money, 2026-09-02)

Thesis, taken from a wallet rather than a model: a PM football market whose
outcome the SCORE has already decided is sometimes still quoted with a live ask
well below 1. Wallet `0xec5723df…560fa7` (**GSX-**) made **+$85,423 in 105 days
on a book that never exceeded $29k**, 100% in-play, zero pre-match, median hold
**1.6 minutes** — and **half of it in the 20 minutes after the final whistle**.
Full analysis in `reports/wallet_gsx_2026-09-02.md`.

The decomposition that makes it buildable, on GSX-'s own post-whistle cheap buys:

| | n | cost | P&L |
|---|---|---|---|
| bought the eventual **WINNER** | 323 | $3,565 | **+$27,931 (+783%)** |
| bought the eventual LOSER | 2,631 | $19,030 | −$690 (−4%) |

The whole return is the winner leg, and identifying it needs no model. 🔑 And
blind hold-vs-flip on the same lots was **+158% vs +124%**, so **exit speed does
not matter** — this is buy-and-redeem, not scalping, which is what puts it inside
reach of a 30-second Python loop.

```bash
cd agent && source ../ingest/.venv/bin/activate
python settled_sweep_observer.py --once --dry-run   # one cycle, no writes
python settled_sweep_observer.py                    # forever, 30s
python settled_sweep_observer.py --settle           # backfill PM resolutions
python settled_sweep_observer.py --report
```

Strategy: none yet (observation only). Hypothesis **H-SETTLED-SWEEP** (id 31),
table `settled_market_observations` (db/039), wrapper `settled_sweep_daemon.sh`.

**Three windows, in rising order of frequency.** Measured on one real board:
**40 markets are already decided at full time and 24 at minute 70.**
- `post_whistle` — the match is over, the board has not resolved. GSX-'s window.
- `halftime` — every 1st-half market settles at the break with the fixture still
  live. **This happens in EVERY match**, so it should be the more frequent window.
- `in_match` — an Over passes its line, BTTS gets its second goal, an exact score
  dies. Settled the instant the ball crosses.

⚠️ **The risk is our settlement code, not the market.** There is no model risk and
no price risk here; a wrong rule means buying at 6 cents something worth 0 while
believing it is a 16x. So `rule_correct` — our verdict checked back against PM's
own resolution — is the PRIMARY measurement, ahead of any yield. The rules live
in `settled_markets.py`, apart from the network and the DB, with 38 tests, and
**everything fails closed**: an unparsed question, an ambiguous team, a missing
half-time score and an unrecognised status all return "we do not know".

⚠️ **Knockouts are excluded outright** (`AET/PEN/ET/BT/P`), and so is anything
interrupted. This is measured, not theoretical: GSX- bought "Will Portugal vs.
Croatia end in a draw?" at 0.003 after the whistle and **the market resolved NO**.
Extra time and penalties change what these questions pay, and that error inverts
the position instead of blunting it.

⚠️ **Ask-only books are kept.** A determined token routinely loses its bid side —
`ladder_of` already notes that makers pull the book once a line is decided — and
the shared `_fetch_book` helper returns None for exactly those. `book_missing` is
recorded as a finding, not skipped as an error. Whether the quote exists at all
is one of the two things this observer is for.

Verdict gate: n >= 200 `would_enter` rows, `rule_correct` >= 0.99 **measured on
the `would_enter` rows themselves**, and a yield CI clear of zero after the taker
fee. 🔑 The fee is why the cheap end works at all: `0.05·p·(1−p)` is 1.25pp at
p=0.50 and **0.05pp at p=0.01**.

### First `rule_correct` audit — phantom goals (2026-09-13)

Full data: `reports/sweep_rule_audit_2026-09-13.md`. obs_version 1, 09-04 → 09-13.

| phase | settled rows | rule wrong | `would_enter` | wrong entries |
|---|--:|--:|--:|--:|
| `post_whistle` | 235,389 / 692 fx | **0** | 2 | 0 |
| `halftime` | 6,319 / 169 fx | **0** | 0 | — |
| `in_match` | 22,871 | 21 (7 fx) | 19 | **3** |

**All 21 wrong verdicts are one cause: the live feed showed a goal that did not
stand.** Seoul E-Land v Suwon read 0-2 from 65' to 68' and then 0-1 again, and
Pyramids read 2-0 at 89' and finished 1-0. Criciúma, CRB, Austin, Bucheon and
Kharkiv failed the same way. There are zero parsing, side or logic errors. The
rules are sound; a live score is not, and a whistle score has already been
through VAR.

🔑 **Rule errors select themselves into entries.** `in_match` `rule_correct` is
99.91% over all rows and **84.2% (16/19) over `would_enter`**, about 170× worse.
When our score is wrong the market is priced on the true score, and that looks
like edge (wrong entries 18-32pp, right ones 5-55pp, so no price threshold
separates them). Worse, `rule_correct` undercounts: **6 of 19 entries were
bought on a phantom score**, and 3 of those "won" only because the match
cooperated (Criciúma "0-0 → No" was bought at a real 0-0 in the 8th minute).
Hypothetical in_match P&L: +$1.51 on $423 gross, ≈ −$2.5 net; +$76.51 without
the three.

⚠️ The error is one-sided, as in db/038: every `in_match` rule fires on "a goal
happened", so a phantom fabricates entries and never removes one. Austin and
Bucheon show only against the **half-time** score, because a later goal restored
the full-time total.

Proposed, **not implemented** (needs obs_version 2; never pool v1 `in_match`
rows with it): a goal must have stood ≥ 5 min (the longest phantom lasted
~4 min), and/or PM's own ladder must confirm the score (the late-goals
`book_confirmed` pattern — Seoul's O/U 1.5 was asked at 0.67 while we read it
as settled). The book is still the binding constraint: 24 `would_enter` rows in
9 days.

## Wallet analyser — the GSX- method, codified (2026-09-05)

The [wallet study](reports/wallet_gsx_2026-09-02.md) that produced
H-SETTLED-SWEEP was done by hand and survived only as markdown. It is now a
tool, with a page.

```bash
cd agent && source ../ingest/.venv/bin/activate
python wallet_analyzer.py 0xec5723df1ef786d95b05b2941c89b45dcb560fa7
python wallet_analyzer.py <addr> --report              # reports/wallet_<name>_<date>.md
python wallet_analyzer.py <addr> --since 2026-07-01 --json out.json
python wallet_analyzer.py <addr> --verify-site https://nopredictions.com
```

Given an address it rebuilds every fill into FIFO round trips and reports the
numbers **and a written reading**: the archetype it matches and why, which leg
of the book actually carries the profit, how the behaviour changed month by
month (including a named regime change), whether the edge survives a bootstrap
clustered by event, and what is not established. Every sentence is a threshold
on a number that appears in the profile.

`site/app/lib/wallet.ts` + `/api/wallet` + `/wallet/[address]` are the same
analysis running inside a serverless request — **a hidden, `noindex` route**, so
the method is something you are given rather than something you find.

🔑 **Two implementations of a FIFO reconstruction WILL drift**, and a page that
quietly disagrees with the research it came from is worse than no page. So
`--verify-site` compares 17 numbers **and every generated paragraph**. On GSX-
they agree to the last decimal, bootstrap CI included (the PRNG is a mulberry32
ported both ways, with its stream pinned in the tests).

Validated against the hand-written report: median ticket $10.01 vs $10.01, cash
floor −$577 vs −$577, sweeps 786 lots / 58% / 0.8% vs 807 / 55% / 0.9%, whistle
window +34.6% vs +35.4%, same sweep examples.

**The traps it exists to avoid** — all of them produce a plausible wallet rather
than an obvious failure:

| | |
|---|---|
| `/activity?offset=` | refuses past **5000**. A naive pager returns 5,000 rows and looks complete; GSX- has 16,157. Page by time cursor. |
| a fill's `price` | is the price **before the fee**; `usdcSize` is the cash, fee included. Use `usdcSize / size` for P&L. The gap IS the fee — 0 on maker fills, rate × p(1−p) per share on taker (mostly 0.05), never negative on 16.5k fills across two wallets. It was once read as tick rounding; it is not. |
| lb-api `profit` | is **GROSS of fees and leaves rebates out**. Compared against net P&L, every fee-paying wallet read `unreconciled` — king1605 $41,098 vs $35,366, the gap being $5,876 of fees (to the dollar what gravia.trade shows). Reconciled against P&L + fees with a 1% tolerance since 2026-09-10. |
| the ticker head | is not a sport, and a hand table goes stale: `col` is the **Conference League**, not Colombia, and 37% of an all-football wallet read "unknown". Gamma `/sports` (465 leagues) is keyed on the same code, names the league and tags soccer (`100350`). Its series ids change by season — never join on them. |
| REDEEM rows | carry **no `asset`** — map `(conditionId, outcomeIndex)` → token or the payout lands on the other side of the market. |
| shares sold, never bought | are neg-risk conversions (`type=CONVERSION` returns empty). Booked **FLAT, never free** — free is an error that can only run one way, the shape of db/038. The report brackets the two bounds and PM's own figure has to fall inside. |
| MERGE | **is a real exit and the feed publishes it** (RN1: 2,845). Unmodelled it showed **−$14.2M of "expired worthless"** that never happened. SPLIT stays unmodelled on purpose — its per-leg cost is unknowable and any convention would distort the entry-price bands. |
| `Math.max(...xs)` | **overflows the stack** on a 190k-fill history, and fails as a server error rather than a size limit. |
| the 110–130′ window | is a **football** fact. 110 minutes into a tennis match is the middle of it — RN1 (13% football) was being handed a "post-whistle settlement buyer" verdict off nothing. |

Every report carries a `trust` field — `ok` / `partial` (the walk never reached
the start of the account) / `unreconciled` (PM's own profit falls outside our
bracket). When it is not `ok` the warning opens the headline and the page,
because a caveat at the bottom of a long page is a caveat nobody reads.

⚠️ Gamma is used for market metadata, not the CLOB: 1,214 markets in **2.2s**
against ~30s and 712 rate-limit refusals. Verified equal to the CLOB on
`gameStartTime` (1,114/1,114) and winner (1,108/1,108). **Its own trap is
`closed`** — without `closed=true` it returns zero rows for a settled market,
which reads as "no metadata" rather than an error, so both states are swept.

## Unfreezing the reading — obs_version 3 (2026-09-05)

Both first-half arms measured pressure ONCE, at the first poll landing in 15-18',
and then froze it. A match that woke up later could never be entered however hard
it pressed. **Manchester City vs Coventry** is the case that changed it: at 15'
City had **85% possession and zero shots**, giving dominance **+16.7 against a
gate of 20**; by 22' the same index read **+32**, with the reading frozen and the
entry window closing at 25'. It scored at 26'.

From obs_version 3 the gate is re-applied at every poll, out to minute **40**:

| minute | reading |
|---|---|
| <= 18 | cumulative totals scaled to a 15-minute rate — literally the old opening number, so entries at 15-18' are unchanged |
| > 18 | the **rolling 15-minute window** (`live_tracker` deltas), the same measure the full-match arm has always used |
| no window baseline | no reading, no entry — never back-filled from a longer average |

One shared function, `ht_pressure_agent.current_pressure`, so "pressing now"
cannot come to mean two different things in two files. The frozen 15-18' reading
is still recorded on every row (`opening_pressure` / `opening_dominance`) as the
CONTROL arm, and `pressure_source` says which measurement fired each entry.

**Thresholds did not move, and that was checked rather than assumed.** The
rolling index reconstructed offline from the cumulative stats already on these
tables (4,557 poll-rows, minutes 19-44, still 0-0) gives both-ends p50 13.3 /
p75 18.8 / p90 24.4 and per-side p75 19.6, |gap| p75 18.8 — within a point of the
opening-15 distribution the current gates were set on, and flat across the clock.
So 19/19/20 still select about the top quartile. Nothing was refitted to an
outcome.

A usable window exists on **97% of PM-listed polls at 19-25', 86% at 26-33' and
81% at 34-40'** (from `pressure_observations`, 21 days), so the late path fires
in production rather than in principle.

⚠️ **Longer odds are not a cheaper market.** `real − ask` on clean books, still
0-0, clustered by fixture, is about equally negative at every minute:

| | 15-19' | 20-24' | 25-29' | 30-34' | 35-40' |
|---|---|---|---|---|---|
| s17 | −3.39 | −4.90 | −3.14 | −2.32 | −5.43 |
| s18 | −3.48 | −3.62 | −1.81 | −2.31 | −2.88 |

Every CI crosses zero and none narrows with the clock. Entering at 32' buys
leverage on whatever the pressure signal is worth — s17 fair value falls ~0.59 at
15' to ~0.19 at 40', s18 ~0.29 to ~0.10 — not a better price. Replaying both
gates over stored rows, the funnel goes **s17 60 → 102 fixtures** and **s18
63 → 90** (some old-gate fixtures are lost: hot opening, quiet by the time the
book was clean). Pre-registered as `H-PRESSURE-LATE` (db/040), forward-only,
primary test on all observation rows carrying a book.

## The 1.75 floor — a price gate that is mostly a clock gate (2026-09-06)

Strategy 17 now refuses anything shorter than **1.75**, by the user's decision.
The signal gate and the price gate are separate events: a fixture that clears
`MIN_PRESSURE` while the book is still short is **armed** (`state.armed`,
`armed_at_minute`, `armed_pressure`) and monitored, entering on the first later
poll where the price has arrived and the book is still clean, still 0-0, still
inside 40'. `entry_trigger` says whether pressure was passing at the moment of
entry (`live`) or only when the fixture was armed (`armed`). **obs_version 4** —
never pool with v1-v3. Migration db/042, hypothesis `H-PRESSURE-MINODDS` (id 34).

🔑 **The gate mostly selects the clock, not the price.** On 5,436 clean 0-0 polls
between 15' and 40', the share of the book already quoting 1.75 or longer:

| | 15-19' | 20-24' | 25-29' | 30-34' | 35-40' |
|---|---|---|---|---|---|
| at 1.75+ | 41% | 77% | 95% | 99% | 100% |
| median ask | 0.590 (1.69) | 0.530 (1.89) | 0.460 (2.17) | 0.370 (2.70) | 0.280 (3.57) |

The ask falls because the **fair value** falls (~0.59 at 15' to ~0.19 at 40'), so
"wait for 1.75" is close to "wait until ~25'" — and db/040 already measured that
later is not cheaper (`real − ask` −3.4 / −4.9 / −3.1 / −2.3 / −5.4pp across the
same buckets, every CI crossing zero). What it buys is **leverage on whatever the
pressure signal is worth**, not a better price.

**Funnel, replayed over stored rows.** Of 121 fixtures that ever armed: **11**
enterable at the arming poll, **49** reached 1.75 on a later poll while still 0-0
(median wait 9 min, p90 13), **61** never got there before a goal or half time.
Entries roughly halve and move later and longer.

⚠️ **What the odds bands actually said — this is a decision, not a finding.**
68 settled entries, 1u flat, net of the fee:

| band | n | won | hit | implied | net yield | 95% CI |
|---|--:|--:|--:|--:|--:|---|
| 1.00-1.50 | 16 | 11 | 0.688 | 0.716 | −4.6% | [−39.4, +23.8] |
| 1.50-1.75 | 31 | 18 | 0.581 | 0.620 | −9.0% | [−35.8, +17.7] |
| 1.75-2.00 | 16 | 9 | 0.562 | 0.551 | +0.4% | [−45.3, +45.4] |
| 2.00-2.50 | 5 | 3 | 0.600 | 0.474 | +23.3% | [−61.8, +108.4] |
| **ALL** | **68** | **41** | 0.603 | 0.616 | **−3.4%** | [−22.9, +15.9] |

The hit rate tracks the implied price band by band — **no gradient** — and every
interval is 6-25× too coarse for the effect being hunted. `--report` now prints
this split and the arming funnel on every run.

⚠️ **`armed` is a latch, the fourth in a month** (after the uncovered-league
blacklist, the PM-listed cache and the stats carry-forward). This one is bounded
on both ends by construction: consulted only inside 15-40' on a fixture still
0-0, dropped by `prune()`, and never a substitute for a live reading anywhere it
is recorded.

## The site is two products now — SaaS + the agent (2026-09-06)

`nopredictions.com` was a waitlist landing plus a public record of a paper
agent. It is now an app with four tabs, and the agent is one of them, framed as
what it is: **paper, not live, no arm near its verdict gate**.

| tab | route | what it is |
|---|---|---|
| **Scout** | `/` | Today's Polymarket football, biggest markets first |
| **Lab** | `/lab` | Write a theory in English, backtested over 111,475 games |
| **Agent** | `/agent` | The paper record, behind an IN TESTING banner |
| **Wallet** | `/wallet` | Any Polymarket trader's whole record, rebuilt |
| Game Center | `/game/<slug>` | Reached by clicking a fixture, never a tab |

Deleted with the user's confirmation: the landing page and waitlist, the
scanner and its `/api/analyze` + `lib/edge` + `lib/dc_model` cascade, the
already-dead `/api/scan` and LeaderboardSection, the Newsletter and About
sections, `site_prototype.html`. `/dashboard` → `/agent`, `/test` → `/lab` and
`/scanner` → `/` redirect, because both of the first two were in links we do
not control.

🔑 **What the board ranks on is a product decision that reversed once.** It
first led on book quality — the one thing the 100-game review found separating
— and that is a research finding, not a reason anyone opens a betting site. It
now ranks on **money traded**, which settles the quality question by itself: a
fixture with millions through it has a real two-sided book by construction. The
grade survives as a column. The ranking bug that exposed this: "in play first"
put a $13k J-League board above Everton v Manchester United at $5.4M.

### Gamma's four traps, each of which produced a plausible board

Every one of these looked like data rather than like a failure:

| | |
|---|---|
| `limit` | is **capped at 100** however large a value you send, and `offset` is honoured. A 300-step loop returned **26 fixtures out of 344**. |
| `startDate` | is the **listing** time, not kick-off — usually the same morning. Filtering kick-off with `start_date_min` returns an empty board. Kick-off is `startTime` (event) / `gameStartTime` (market). |
| `endDate` | **EQUALS `startTime`** on a fixture event, so `end_date_min=now` drops every match the moment it kicks off — the exact set a matchday board exists to show. Filter back over the whole window and bound it client-side. |
| question text | is not a market family. `"… : Everton FC O/U 2.5 Corners"` matches an "O/U 2.5" pattern perfectly, which graded **Arsenal v Chelsea as a blown book on $1.18M of volume**. `sportsMarketType` is exact: match goals are `totals` and nothing else (`first_half_totals`, `soccer_team_totals`, `total_corners`, `spreads`, `moneyline`, `both_teams_to_score` all carry their own). |

Also free on the listing and worth knowing: `bestBid`, `bestAsk`, `spread`,
`liquidityNum`, `volume`. The base book grade therefore costs **no** CLOB round
trip and every fixture gets one; only the busiest dozen are re-read live,
because Gamma's prices lag the book. Each card says which source it used.

⚠️ **An unfunded ladder quotes every rung at a placeholder extreme**, which is
indistinguishable from a settled one. Without a clock guard that read as a
fixture LIVE seven hours before kick-off and another FINISHED nine and a half
hours before it. The clock is now **necessary** and only ever rules out; where
it is all we have the card says `KICKED OFF?` rather than `LIVE`.

`lib/scoutCache.ts` holds one sweep behind a 45s TTL with in-flight coalescing,
because the tape runs on every page and a visit to `/lab` must not pay for a
Gamma sweep plus twelve CLOB calls to print seven numbers. A failed sweep
returns the previous board rather than emptying the page.

### Three claims removed because they were not true

- The dashboard hero said **LIVE** directly above a banner saying the agent is
  not live.
- `HomeSection`'s newsletter form set local state and showed a tick. It sent
  **nowhere**.
- `TradeCard` printed the edge as `` `+${edgePp}%` `` with a hard-coded plus, so
  a **negative edge rendered as `+-1.8%` in green** — a losing pick shown as a
  winning one.

⚠️ **Log in / Sign up / Alerts are rendered but not wired**, by request. They
are not inert: a click says accounts are not live rather than swallowing it,
which is the difference between a control awaiting a backend and the newsletter
form above. Replace `notYet` in `AppShell.tsx` and nothing else changes.

⚠️ **The Lab's verdict is computed from yield and p-value alone.** "Draws are
underpriced in Serie B" returns EDGE FOUND on +4.66% yield with CLV −0.07%,
which rule 5 of this file calls luck. The results panel now says so when the
two arms disagree; the verdict logic itself is untouched.

## The evening the key ran out — ESPN as a second source (2026-09-06)

api-football refused from 20:44 with **7,176 calls of our own recorded** against
a supposed 75,000 (`agent/.af_calls_*.json`), and the user confirmed the
dashboard showed the allowance spent. It had also served **0.0% xG since
09-02** — the same figure on s16 and s17, which is what proves it is the feed
and not our parsing. A refused poll used to `return {}`, so every arm went blind
for the rest of the night. Fourth time in a week.

🔑 **Our own spend is now the discriminating number this file said we never
had.** 7,176 against a refusal is either a plan that is not what we think it is,
or a key being spent elsewhere. It is not us running out of 75,000.

### `agent/espn_stats.py` — free, no key, no quota

```
https://site.api.espn.com/apis/site/v2/sports/soccer/<code>/scoreboard
```

**One request per LEAGUE returns every live match's statistics.** api-football
needs one for the live list plus one per fixture — 2,188 of that day's 3,691
pressure calls were the per-fixture half. 28 leagues, 109 fixtures, 3.3s.
Covers 18 of the 19 leagues where s16 had a PM book in the preceding three days.

⚠️ **DO NOT SET A USER-AGENT.** Measured:

| | |
|---|---|
| `curl/8.x` default | **200** |
| `python-requests` / `fetch` default | **200** |
| `Mozilla/5.0` (browser) | **403** |
| `nopredictions/1.0 (+url)` | **403** |

The edge rejects browser-shaped and custom agents and serves plain library
defaults. The first version of the file set a polite self-identifying header and
every league 403'd. The library default is also the honest string. A browser UA
would work and is **not** used, because that is claiming to be something we are
not — the same line that stopped us working around FotMob's signed header.

**What it does not carry: team xG, and shots inside the box** — 55% of the index
weight. `danger_index` gains `has_inside` beside `has_xg` and **drops** both
rather than scoring them zero: zero says nobody got into the box, and the truth
is nobody told us. Same match scores **58.50** on a full feed, **56.67** in
ESPN's shape, and **43** if zeroed — under every threshold, which is the exact
failure that once excluded every no-xG fixture from s16.

### The fallback, and the three things it had to get right

A refused live poll — or a missing key — now polls ESPN instead.

1. **The fixture id is namespaced NEGATIVE.** ESPN's event ids are ~400M and
   api-football's ~1.5M, so no collision today; a collision would splice two
   matches' snapshot histories into one. Negative also makes the source visible
   in any query with no join.
2. **A source switch mid-match could buy the same game twice.** The "already
   entered" guards keyed on `fixture_id` alone, which a re-namespaced id walks
   straight past. All three arms now match on the **teams** as well, inside a
   6-hour window.
3. **It is a different measurement.** `db/043` puts `stats_source` and
   `has_inside` on all three observation tables. ⚠️ `has_xg` alone cannot carry
   this: an api-football fixture in a competition with no xG and an ESPN fixture
   both read `has_xg=false`, and only one is also missing the inside-box term.
   **Never pool the two in one yield.**

Falling back does not clear the quota latch — the outage is still an outage.

⚠️ **This is cost and reliability, not edge.** [[finding-live-reading-ceiling]]
put the whole box-score apparatus at **+0.0008 pseudo-R²** over free state and
[[finding-ask-move-no-signal]] at **−0.00042 CI[−0.00104,+0.00019]** — a zero.
Swapping feeds removes a failure mode. It does not make the signal work.

### Leagues api-football never covers — ESPN fills the stat block (2026-09-13)

On 2026-09-12, **49 Polymarket-listed fixtures** reached the first-half arms
with no reading at all: api-football confirms (`coverage.fixtures.
statistics_fixtures = false`) that it publishes no statistics for their
competitions. The whole-cycle fallback above never engaged, because
api-football was not refusing; it simply had nothing.

`espn_stats.AF_UNCOVERED_TO_ESPN` maps six of them, **keyed on api-football's
league ID** because "Primera División" is three countries in one day's feed:

| api-football id | league | ESPN |
|---|---|---|
| 43 | National League (ENG) | `eng.5` |
| 255 | USL Championship | `usa.usl.1` |
| 489 | USL League One | `usa.usl.l1` |
| 129 | Primera Nacional (ARG) | `arg.2` |
| 299 | Primera División (VEN) | `ven.1` |
| 162 | Primera División (CRC) | `crc.1` |

Uruguay is uncovered too, but ESPN has no stats for it, so it is left out.
J2, Slovakia, Kazakhstan, K League 2 and others did not resolve under the codes
tried.

`LiveMatchTracker._enrich_from_espn` differs from `_poll_espn`: **api-football
still owns the fixture** (a positive id, its minute and its score) and ESPN
supplies only the stat block. It makes one request per league per cycle,
outside the quota latch and the cycle budget. It fails closed on no match, on
no stats yet, and on **ESPN's score disagreeing with api-football's** (a lagged
goal or a wrong pairing). Rows carry `stats_source='espn'`, `has_inside=False`,
so **never pool them with api-football rows.**

Matching was checked offline against the 2026-09-12 boards: **34/34 in the
mapped leagues.** Five first looked like misses; they are filed under the
previous US-Eastern date and pair 1.00/1.00 on the right day. The live
scoreboard shows the current ET day, so that does not bite in production.

🐛 **Fixed on the way:** `_carry_stats_forward` copied the stat values but not
`source`/`has_inside`. A fresh snapshot defaults to api-football with
shots-in-box, so any carried ESPN block was relabelled on the way through, and
the index scored its missing shots-in-box as a measured zero.

`tests/test_espn_uncovered_leagues.py`, 6 cases. ⚠️ A daemon restart is required
for any of it to apply.

## The site has accounts and a paid plan (2026-09-08)

`nopredictions.com` was four free tabs. It is now a product with a free tier
and a paid one, and `main` is what production runs.

| | |
|---|---|
| no account | Scout · Agent · Game Center — unlimited, never asked to sign in |
| free | + Lab **3/day** and Wallet **3/day**, watchlist in this browser |
| pro | + both unlimited, watchlist synced. **$19/mo · $190/yr** |

🔑 **`usage_events` (db/044) has RLS ON with NO policies.** Not "read-only for
the owner" — nothing is granted, so anon and authenticated cannot read a count,
forge one, or delete one to start the day again. Every read and write goes over
`DATABASE_URL` in `app/lib/plan.ts`. A quota the client can reach is not a
quota. The same reasoning gives `profiles` a SELECT-own policy and **no
UPDATE**: a client that could update its own row could set `plan = 'pro'`. The
Stripe webhook is the only thing that grants or removes Pro.

`claimUse()` counts and inserts inside ONE transaction behind
`pg_advisory_xact_lock(hashtextextended(user_id, 0))`. Check-then-insert lets
two simultaneous requests both read `used = 2` and both run — at three a day
that is a third of the tier. Both gates sit AFTER the free validation and
BEFORE the expensive call, so a refusal never spends an Anthropic call or a
33-page walk of PM's feed; `refundUse()` returns the use on a 5xx that is ours.

⚠️ **Alerts are schema-only.** `public.alerts` exists and nothing sends
anything. The pricing page names them as *next for Pro*, never as included —
this repo already deleted a newsletter form that set local state, showed a
tick, and sent nowhere.

**Still needs a console you have to be logged into** (`site/SETUP.md`):
Supabase email-confirmation OFF or SMTP, redirect URLs, and the Stripe account
+ prices + webhook. All of it degrades honestly — the upgrade button says paid
plans are not switched on yet rather than erroring.

### Three things that were quietly wrong

- **`NEXT_PUBLIC_SUPABASE_URL` was never set on Vercel** — only the anon key
  was. Nothing had broken because `supabase.ts` carried the URL as a literal,
  but the auth code read the variable and asserted it with `!`. Fixed on both
  sides: the variable is set, and the fallback moved to `app/lib/supabaseEnv.ts`
  where all four callers share it.
- **The browser and server Supabase clients cannot live in one module.** The
  server one imports `next/headers`, and Next refuses to compile any
  `'use client'` graph that reaches it — the build fails outright. The split
  into `supabaseBrowser.ts` / `supabaseAuth.ts` is load-bearing.
- **Importing that client into `AppShell` cost +70kB of First Load JS on every
  page** (the board went 104 → 174kB) to carry a sign-out button most visitors
  never press. It is `await import(...)`ed inside `signOut` and inside the
  watchlist sync; the board is back to 105kB.

## Dropping odds, and paying before there is an account (2026-09-08)

Both came out of looking at `steamwatch.io`, a Pinnacle line-movement tracker
that is the nearest adjacent product to this one.

### `/dropping-odds` — the movers board

It took the **"Clean books"** chip in the Scout row, beside the Insights link
that had already taken "Measured". The grade survives as a column on every row;
what went is the filter.

🔑 **It costs no extra request.** Gamma publishes `oneDayPriceChange` and
`oneHourPriceChange` on every market in the listing response the Scout sweep
already downloads — the same shape as the live block. `moveOf()` in
`lib/scout.ts` puts the biggest shortener on `ScoutFixture.move`, `lib/movers.ts`
filters and splits, and `/api/movers` shares the board cache.

⚠️ **Two people are not a market.** Ranked naively by move size, the top card
was **23.0pp on $2,672** — which is the thing worth criticising in the tracker
it was modelled on, whose own top card was 39pp on $5,324. Measured on a live
board: the median move is the SAME in every volume band — 3.0pp under $5k,
3.0pp at $5-10k, 2.5pp above — and only the thin band has a tail, against a 4pp
maximum on everything funded. **Same middle, fat tail on one side, is what noise
looks like.** So `MIN_VOLUME_USD = 1000` to appear at all, `FUNDED_VOLUME_USD =
5000` splits the main board from a collapsed "thin books" section, and
`MIN_MOVE_PP = 2`. Pre-match only: a price moving during a match is mostly the
score, which Scout already shows.

⚠️ **The horizon is 48 hours, and that is Polymarket's doing.** SteamWatch shows
Friday movers on a Tuesday because Pinnacle prices a week out. Measured across a
1,200-event Gamma sweep, the time to kickoff on this board runs **median 25h,
maximum 52h** — a four-day-out mover essentially cannot appear here. The card
prints T−kickoff anyway, because 8pp at T-40h and 8pp at T-2h are different
events.

⚠️ The change field is **null on ~35% of upcoming fixtures** (65% coverage,
against 93% on finished ones): a market listed today has no yesterday. Null is
"not known", never "did not move", and those rows are dropped rather than drawn
at zero.

The page states what it is not, citing this project's own measurements — ask
movement carries nothing beyond the ask level, and pre-match PM football did not
survive the spread floor. A board of arrows that stayed quiet about those would
be making a claim by implication.

### Pay first, sign up after

`/pricing` takes an email and goes straight to Stripe; no account is required.

🔑 **`pro_grants` (db/047) is the whole mechanism.** A payment can arrive for an
email that has no `auth.users` row, and `profiles.id` references that table — so
there is nowhere to write it, and creating the user from the webhook needs the
Supabase service-role key this deployment does not hold. The webhook therefore
always writes the grant **keyed by email**, and `handle_new_user()` claims it
when the account is created. Pay then sign up, or sign up then pay: either
order, any later time, same end state. Email is safe as the join key only
because Supabase issues it — the grant goes to whoever proves control of the
address through auth, never to whoever types it into a box.

`/welcome` is the Stripe return URL rather than `/account`: most people arriving
from checkout have no account, and an account page saying "not signed in"
immediately after taking their money is the worst possible first screen.
`/login` prefills the address from there, because using a different one puts the
subscription on the wrong account.

⚠️ **It has never been run end to end.** There is no Stripe account, and the
checkout route refuses at the keys check before anything else executes. What IS
verified is the half that decides who gets Pro: `active` and `trialing` grants
resolve to `pro`, `canceled` and `past_due` to `free`, on four synthetic grants.
The untested half is Stripe's own round trip.

⚠️ **Never `rm -rf .next` while the preview server is running.** `next dev` and
`next build` share that directory, and the collision surfaces as
`Cannot find module './vendor-chunks/@swc.js'` or a 500 on a page that builds
perfectly — it cost two false alarms in one session.

## The board's cache is shared between instances (2026-09-08)

`lib/scoutCache.ts` was module-level, which on Vercel means **per serverless
INSTANCE**. Measured on production: **8.3s cold, 0.4s warm** — so the 0.4s
number described an experience almost nobody had, because most visitors land on
an instance that has never swept. The fix is not a longer TTL; it is a cache the
instances share. The sweep now sits behind `unstable_cache` (Vercel's Data
Cache, shared across every instance and region) with the module cache kept in
front as L1. **Worst of 20 cold requests afterwards: 0.55s.**

Also raised: `typescript.ignoreBuildErrors` is now **false**. It was hiding two
real errors — a `Set` the unset (therefore ES5) target could not iterate, and
`avg_clv` typed as absent when the view returns null. Lint still does not block
a deploy; there was no eslint config at all, and one was added only so
`npm run lint` runs.

⚠️ **Git auto-deploy is still not on.** `vercel git connect` returns 400 — the
Vercel account has no GitHub login connection. Until someone authorises that in
the Vercel dashboard, deploys stay `cd site && vercel --prod --yes`.

## The anon key could TRUNCATE the research — closed 2026-09-08

A Supabase advisory during the accounts work: **18 tables had RLS disabled**,
and `anon` was granted `DELETE, INSERT, TRUNCATE, UPDATE` on every one of them.
The anon key ships inside the site's JavaScript, as anon keys do. So anyone
could have truncated 761,088 rows of `pressure_observations`, 371k of
`pm_ticks`, 106k of `settled_market_observations`, and fifteen more.

🔑 **These are the rows the project cannot buy back.** The pressure model can
only ever be fitted on data recorded FORWARD — a truncate is not an outage, it
is every fixture-minute since 2026-07-22 and the restart of a verdict gate that
is already months away.

`db/045_rls_lockdown.sql`: RLS on, **no policies**. Safe by inspection, and the
inspection is the point — the site reads exactly six things with the anon key
(`leagues`, `match_odds`, `matches`, `paper_trades`, `strategies`,
`v_strategy_performance`, all already RLS-on), while the Lab (`app/lib/db.ts`)
and every Python agent (`agent/tools/db.py`) connect over `DATABASE_URL`, which
bypasses RLS. Verified after: research tables return `[]` to anon, the six still
return rows, `/agent` still shows all 245 settled bets, and
`pressure_observations` took a new write 13 seconds later.

⚠️ **Applying it needs care.** `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` takes
an ACCESS EXCLUSIVE lock, so a batched migration dies on a statement timeout
while the daemons write. One table at a time with `set lock_timeout` got 17 of
18; the last was blocked by the transaction leak below.

## Agents belong to users; the Lab builds them (2026-09-11)

The product changed shape again, by the user's decision: **an agent is a
user's, and private**. The pressure arms are the operator's own agents, shown
to him on `/agent` behind a sign-in — there is no public record any more
(`/agent/ours` and `/dashboard` redirect to `/agent`). A public leaderboard of
agents users CHOOSE to publish is the next step; until it exists the site shows
no publish button.

| | |
|---|---|
| `db/049` | `profiles.role` ('owner' = unlimited), and on `strategies`: `owner_id`, `source` ('agent' hand-written / 'lab'), `theory`, `interpretation`, `spec`, `backtest`, `run_status`, `run_blocker`, `is_public` |
| `db/050` | drops the public read of `paper_trades`/`strategies` and their views. **Applied 2026-09-19**: the anon key gets `permission denied` on all eight; the site reads them only through `/api/agents` over DATABASE_URL. All 17 `source='agent'` strategies are owned by the operator's account |
| `site/app/lib/agents.ts` | every read/write, owner check in the SQL, over DATABASE_URL. The saved backtest is **recomputed on the server** from the spec — a leaderboard cannot rank on numbers a browser posted |
| `/api/agents` · `/api/agents/[id]` · `/api/agents/trades` | list + save · run/pause/archive · the record |
| Lab | **SAVE AS AN AGENT** under a result |
| `agent/lab_strategy_runner.py` | paper-trades running Lab specs; `--settle` settles them by the TOKEN bought |

```bash
cd agent && source ../ingest/.venv/bin/activate
python lab_strategy_runner.py --spec '{"market":"ou25","side":"over","leagues":null}' --window 1440
python lab_strategy_runner.py --once          # one cycle over saved, running agents
python lab_strategy_runner.py --settle
python -m pytest tests/test_lab_strategy_runner.py -q
```

In cron since 2026-09-19: `--once` every 5 minutes (entries only happen in the
last 45' before kick-off) and `--settle` through `cron_guard.sh --gap 25
lab_settle`, both into `agent/lab_runner.log`. With no Lab agent running a
cycle exits before any network call; it never calls api-football.

What the runner refuses, each a way a live trade would stop being the tested
rule: a competition outside the 22 backtest leagues (mapped by tag, ambiguity
fails closed); entering earlier than 45' before kick-off (the backtest bought at
the close); **trusting Polymarket's title order** for home/away — any rule that
needs sides requires ESPN to confirm orientation or the fixture is skipped; a
book with spread > 0.05 or < $50 ask depth; filters it cannot compute live
(form, rest days → paused, reason in `run_blocker`). The resolver now skips
`source='lab'` trades: it maps "first outcome won" to a win, which is a LOSS on
an Under.

Measured on the first dry run over 24h of boards: 1,590 fixtures, 87 fits for
"Over 2.5, all leagues", 24 for "home favourite" with orientation confirmed.

Two Gamma facts found building it:
- **Upcoming fixtures need a date-bounded query.** Unbounded, `order=startTime`
  opens on hundreds of stale never-closed events from February — twelve pages in
  it had not reached September, and the runner saw 0 fixtures. `end_date_min/max`
  as plain DATES works (a fixture's `endDate` IS its kick-off).
- **O/U 2.5 lives in the `"<fixture> - More Markets"` sibling event**, never the
  main one. Other siblings (Halftime Result, Exact Score, Total Corners) carry
  their own `sportsMarketType`, and are skipped by title as well.

⚠️ Plan limits in `AGENT_LIMITS` (free keeps 5 / runs 1, pro 50 / 10) are a
starting point, not a pricing decision.

## The public key could rewrite the public record — closed 2026-09-11

db/045 turned RLS on but never took the GRANTS back. Supabase's default
privileges give `anon` and `authenticated` everything on every table `postgres`
creates, so both held INSERT/UPDATE/DELETE/TRUNCATE on **all 50 relations**.
RLS blocked most of it — **not the views**: v_pressure_trades,
v_ht_pressure_trades, v_fav_ht_trades and v_research_log are auto-updatable and
run as their owner, so a PATCH/DELETE through them over PostgREST skips the base
table's RLS. Proven with a no-op PATCH on pt#5926 (value set to itself): the
view returned the row as updated. `won` on v_pressure_trades IS
`goal_before_ft` — the public results were rewritable with the key in the JS.

`db/048`: every write revoked from both roles on every relation, except the
three with a policy (watchlist + alerts for authenticated, waitlist insert for
anon). Same probe afterwards: `permission denied for view` (401); all site reads
still 200. 🔑 **Default privileges changed too: a NEW table grants the client
nothing** — a table the site must read now needs an explicit `GRANT SELECT`.

## A transaction held open across HTTP — fixed 2026-09-08

The thing that blocked that last table: a backend `idle in transaction` for
**8 minutes** on `SELECT fixture_id, minute, home_goals, away_goals FROM
fav_ht_observations` — `fav_pressure_agent.settle()`. It survived 120 retries
over six minutes, and it does something quieter every day: an 8-minute-old
snapshot stops VACUUM reclaiming dead tuples newer than it, on tables taking
writes every cycle (`pressure_observations` is 761k rows and climbing).

🔑 **psycopg2 defaults to `autocommit = False`, so a bare SELECT opens a
transaction that outlives the read.** Two bugs of that one shape:

1. **Network I/O inside the transaction.** All three `settle()` functions read,
   then looped calling api-football once per pending fixture with the read
   transaction still open. `pressure_agent` was worse — `_goal_minute_api` sat
   inside the WRITE loop, one round trip per winning entry while holding row
   locks.
2. **Early returns that skip the commit.** `settle()` returns 0 when nothing is
   pending; `open_trades()` `continue`s past its own `conn.commit()` when a
   fixture was already entered; and `_db_alive()`'s `SELECT 1` opened one every
   single cycle.

`agent/db_txn.py` fixes the class rather than the two paths: `connect()` returns
an autocommit connection and every daemon's `_conn()` now goes through it —
pressure, ht, fav, settled-sweep, late-goals. `commit()` and `rollback()` stay
safe no-ops under autocommit (verified against this database), so the existing
calls scattered through those modules needed no edit.

`db_txn.atomic(conn)` gives back the one thing autocommit takes away. Settling
an observation without paying out its paper trade leaves a trade that never
resolves **and that the next run cannot see**, because `pending` filters on
`settled_at`. It is now per row rather than per batch, which is also the better
shape: a failure loses one row instead of every settlement in the batch.
⚠️ **No network call may go inside `atomic()`** — that is the entire bug.

There is an alarm at the top of `pressure_agent`'s main loop: a connection found
inside a transaction there is logged as this bug returning, and rolled back.

`agent/tests/test_db_txn.py`, 12 cases. The three asserting that no fetch
happens inside a write loop were checked against the pre-fix code and fail there
— one offending call site in each of the three agents. Full suite: 339 passed.
After restarting both daemons, `pg_stat_activity` shows **zero** backends in
`idle in transaction`.

⚠️ **A restart was required for any of it to apply**, as always in this repo.

## The settle cron was spending the key — fixed 2026-09-10

s16 had not entered since 09-05 15:06Z. api-football refused the live poll
from mid-afternoon every day, and the per-process counters said whose calls
did it: `.af_calls_pressure_settle.json` recorded **143,613 on 09-10** against
a 75,000/day key, at a steady ~5,525 per settle run. The live daemon spent 1,613.

🔑 **`fav_pressure_agent.settle()` fetched before it filtered.** It asked for
the half-time score of every pending fixture, one call each, and only THEN
`continue`d past rows with `fav_side IS NULL`. Those rows — every fixture with
no favourite, i.e. most of what the agent watches — could never settle, so they
stayed pending and were fetched again on every run: 5,556 of 5,560 pending
fixtures, piling up since 08-19, so the cost grew daily until it passed the
whole allowance. The likeliest answer to the question the 09-06 section left
open ("7,176 of our own against a refusal").

Fixed in `fav_pressure_agent.py`:
- no-favourite rows are closed set-based with **no call** (`ht_source =
  'no_favourite'`, outcome columns NULL) — 247,671 closed on the first run, 32s;
- `_halftime_scores()` batches 20 ids per call, stops at the first refusal, and
  never sends ESPN's negative ids;
- `SETTLE_API_MAX_AGE_H = 72`: an older fixture settles from the tape or not at
  all, so what cannot settle costs nothing per run.

⚠️ **s16 has a second, separate problem: xG.** api-football rows have carried
**no xG since 2026-09-02** — every league on the same day, while shots, corners
and possession kept arriving (238 fixtures with stats that day, 0 with xG).
`_parse_stats` matched the exact string `'expected_goals'`; it now matches
normalised variants and logs every stat-type name the first time a process sees
it. **A rename is a theory, not a finding** — read the `stat types first seen`
line after the 00:00 UTC reset.

### The xG is now estimated — obs_version bump on all three arms (2026-09-11)

`live_tracker.estimate_xg`, fitted by `agent/xg_proxy_fit.py` on api-football's
OWN live xG (459 fixtures, 08-14 → 09-01, out-of-sample by fixture):
**xG ≈ 0.087 × shots on target + 0.104 × shots in the box** — R² 0.73
cumulative, corr 0.82 on the 15-minute window. Outside-box shots and corners fit
to exactly 0. ESPN (no shots-in-box): 0.166 × on + 0.059 × off-target, R² 0.69,
the same shape as 32k historical team-matches. `danger_index` uses the estimate
whenever the feed has no xG; renormalising survives only as the fallback when
not even the estimate is possible.

On 21,865 fixture-minutes that DID carry real xG, the index recomputed each way:

| | MAE vs real | p90 at 75'+ | ≥45 at 75'+ |
|---|---|---|---|
| real xG | 0 | 41.0 | 7.8% |
| **estimated (now)** | **2.35** | 36.8 | 6.8% |
| renormalised (before) | 4.21 | 34.3 | 6.2% |

🔑 **This corrects a number above's framing:** "no-xG p90 31 vs with-xG 43"
compared DIFFERENT fixtures. On the same fixtures the missing xG costs ~7 points
at p90 and the rest is competition mix — so the estimate lifts s16's ≥45 rate
~10% (6.2% → 6.8% of rows at 75'+), it does not restore 7.8%. And it is **shots
reweighted, not new information**: [[finding-live-reading-ceiling]] still holds.

`has_xg` still says what the FEED sent, so from these versions on
`has_xg = false` means *estimated*. Fixed on the way: `ht_pressure_agent` never
passed `has_inside`, so on ESPN rows s17/s18 scored shots-in-box as a real zero.
**s16 v5 · s17 v6 · s18 v4** — never pool with earlier versions.

## Polymarket already carried the clock (2026-09-06)

Found by the user looking at Polymarket's own page — "2H - 90", "0 - 1" — while
our board said `KICKED OFF?`. Their event carries:

```json
{"live": true, "score": "0-1", "period": "2H", "elapsed": "90", "ended": false}
```

🔑 **And it is on the LIST response, not only on a single-event query** — so it
was already in the bytes the Scout sweep downloads, and the board was inferring
liveness from resolved markets instead. Portland Thorns read as `board` with no
clock while the same payload said `2H, 59', 1-0`.

Best source available by a distance: no second request, no name matching (it IS
the event), 100% coverage of listed fixtures by construction, and it agrees with
what Polymarket shows the trader — which is where they will check.

`period` is `1H`/`2H` while playing and `FT`/`VFT` once over, so `finished`
comes from it rather than from three settled 1X2 rungs.

Scout's order is now **pm → ESPN → a resolved market → the listed kick-off**,
and the badge says which, because they are different claims. On a live board:
8 from Polymarket with minute and score, ~2 from ESPN, ~4 on board evidence,
~5 left on the clock alone — those last are competitions neither source tags,
where the weak claim is the true one.

⚠️ ESPN is still needed by the **pressure arms**: they want shots, corners and
possession, and Polymarket carries none of those.

## The Game Center, second pass — what only this database can show (2026-09-11)

Modelled on Sofascore's match page, differentiated by the one thing a stats site
does not have: **results joined to closing prices**. Four tabs — Overview · Stats
· Match · Markets — with `#stats`-style links that open the right one.

| panel | source | what makes it ours |
|---|---|---|
| **Brief** | Claude (`claude-opus-5`, fallback 4.8) | told to use only the page's numbers; no tips; small samples flagged |
| **Priced like this** | `priced_like.json` | every other market in the matches Pinnacle CLOSED at this price (47,740 on totals, 101,249 on 1X2) |
| **Stats** | our `matches` + `match_odds` | HT/FT splits (last 5 / 10 / real venue / season), each game with its closing odds |
| **Runs** | same, rarity from league base rates | "1 in N" at the league's own rate; only runs ≤5% shown, and the page says 68 are checked |
| **Against the closing price** | same | wins / overs vs what those closing prices implied — labelled as variance on 10 games |
| **Pressure curve** | `pressure_observations` | our agent's per-minute danger index, joined on PM's own `event_title` |
| **Match** | ESPN summary | timeline, pitch from position codes, box score, commentary, table, venue/referee/TV |

```bash
cd agent && source ../ingest/.venv/bin/activate
python priced_like_table.py      # rebuild site/app/lib/priced_like.json (reads ~145k matches)
```

🔑 **The Stats tab is only as fresh as Stage A — and Stage A's cache never
expired.** On 2026-09-11 the European leagues stopped at 08-27 and the
Bundesliga had no 2026-27 rows at all. A plain `--seasons 2026-27` re-run
"worked" and changed almost nothing: `download_csv` returned any cached file
as-is, so 21 of 22 leagues re-read their 08-28 snapshot and only the uncached
Bundesliga got new rows (Premier League: 10 matches in the DB, 30 played).
`--refresh` re-downloads (falling back to the cache if the server will not
answer) and `--current-season` picks the live season from the date:

```bash
cd ingest && source .venv/bin/activate
python stage_a_football_data.py --current-season --refresh --continue-on-error   # 869 matches, ~2 min
```

Daily in cron since 2026-09-11 behind `cron_guard.sh --daily 07:00 stage_a`
(log `ingest/stage_a_cron.log`, crontab backup `reports/crontab_backup_2026-09-11.txt`).
The tab still warns when a team's last game is 3+ weeks old.

⚠️ **The brief after kick-off gets no prices.** Its first in-play version read
Union at **6.06 while trailing 0-1 at 45'** and called it "an unusually wide
line" — the board prices the score. From `started` (board OR ESPN) the facts
carry `match_state: in play — prices withheld` and the cache key moves to its own
phase (`pre` / `pre-xi` / `live`), so a pre-match brief is never overwritten by,
or served as, the price-free one. Counts it would otherwise tally itself (H2H
draws — it once said three of four) are pre-computed in the facts.

💸 **Brief cost scales with fixtures, not visitors**: `unstable_cache` keyed on
slug + phase, 12h, so ≤3 Opus calls per fixture ever. Refused beyond 72h before
kick-off or 6h after it, so a crawler walking old slugs cannot buy one per URL.
~11s cold. A failure is thrown, never returned, so it is not cached.

**Team identity** (`teamform.resolveTeams`): `agent/team_aliases.json` first —
including its `__NOT_IN_MODEL__` verdicts, because "Inter Miami" scores 1.0
against our "Inter" under token containment — then `teamScore` over canonical
names + `team_aliases`, then **pairing**: two clubs on one board have shared a
competition inside 420 days, which is what separates Rangers from Queens Park
Rangers. Ties fail closed.

**Sides are never read from PM's title order.** ESPN is matched in both
orientations and returns `swapped`; the venue split, venue-scoped runs and the
brief all follow ESPN's `homeAway`. The pressure curve follows api-football's
`home`/`away` on its own rows.

ESPN traps, each of which produced a plausible page:
- `passPct` arrives as a FRACTION (0.8) beside `possessionPct` as a percent (65.7).
- `lastFiveGames[].score` is not from the team's side — Union's 2-4 home defeat
  read "4-2 · L". Rebuilt from `homeTeamId` + the two scores.
- A fixture is filed under its **US-Eastern** date; the index asks for both.
- `formationPlace` is not ordered by line; the pitch is drawn from position codes.

`teamname.shortTeam` replaced "first two words", which labelled every tile of
"1. FC Union Berlin" as **"1. FC"**. The priced-like block finds headlines BY
label, so server and client share the one function.

⚠️ **The pressure curve is public on the Game Center.** It shows our agent's
danger index per minute — the reading the private arms trade on — not the trades.
A decision for the operator, flagged when it shipped.

**Two `next dev` in one folder**: `next.config.mjs` reads `NEXT_DIST_DIR`
(unset in production) so a second session can preview on its own build dir —
`np-site-gc` in `.claude/launch.json`, port 3107, `.next-gc/`.

## Live stats coverage — measured 2026-08-19

"More leagues" turned out not to be a stats problem. Over three days of
`pressure_observations`:

| | fixtures | ever got stats |
|---|---|---|
| PM lists a board | 107 | **107 (100%)** |
| PM lists nothing | 715 | 0 — never asked |

⚠️ That table used "has shots" as the proxy for "measurable", and it is the wrong
proxy: **xG is 40% of the danger index and api-football publishes it for only 60%
of the PM-listed fixtures** (94 of 164 in the 15-25' window). A fixture without it
scored out of 60 rather than 100, and against a threshold calibrated on the mixed
population **not one of them ever cleared it** — 13.2% of fixtures with xG reached
25 against 0.0% without, at a p90 of 15.3 versus 26.1. So the threshold was partly
selecting "leagues that publish xG", not pressure. Fixed by renormalising the
remaining weights when the feed carries no xG (`live_tracker.danger_index`,
`has_xg`); those fixtures now clear 25 about 10.8% of the time. Strategy 16 keeps
the old measurement deliberately — 78 settled entries and 154k rows are recorded
against it.

The binding constraints, in order:

0. **PM ↔ api-football name matching — 56% on a 27-board snapshot**, almost all
   one-sided transliteration (`Panaitolikós GFS`/`Panetolikos`, `FC Andijon`/
   `Andijan`). Largest *fixable* loss in the tradeable universe. The remedy is
   already built: `python learn_fixture_aliases.py --days 1 --apply`, which only
   ever adds pairings and verifies that invariant before writing. Run on
   2026-08-19: 56% → **70%**. Worth repeating periodically.
1. **PM board coverage.** Only **13%** of the live fixtures api-football reports
   have a Polymarket board at all. Nothing in our code changes that.
2. **Machine uptime — 78%** over four days (4,465 of 5,760 minutes), with gaps of
   6h, 5h16 and 4h42. Most sit in the 05:00-10:00 UTC quiet window, but the 6h
   gap on 08-15 ran straight through prime European kickoff time. This is the
   only large lever we control, and it is a `caffeinate`/scheduled-wake problem,
   not a code one — see [[cron-sleep-guard]].
3. **api-football's own per-league coverage.** Queryable, not guessable:
   `/leagues?id=<id>` returns `seasons[].coverage.fixtures.statistics_fixtures`.
   La Liga, Primeira Liga, Brasileirão, Liga Profesional all true; CONCACAF
   Central American Cup genuinely returns empty.

Quota was never the constraint at any point: **3,246 stats calls over ~4,000
cycles** (under one per cycle) against 75,000/day on Ultra.

Changed on the strength of the above:
- `PressureSignals.league` now carries the competition, and all three agents write
  it. It was NULL on **all 81,917 rows**, which is why "which leagues are we
  missing" could not be answered from our own data at all.
- Fixtures PM does not list are ranked LAST instead of refused: they can never be
  traded, but the pressure model can only ever be fitted on data recorded
  forward, and an unlisted 0-0 teaches that fit exactly as much as a listed one.
- `ENRICH_BUDGET_PER_CYCLE` 25 → 40 so that spare capacity can reach them. Worst
  case 59k/day against the 75k limit; the measured figure is a small fraction of
  that.
- The danger index renormalises when the feed has no xG, which roughly doubles the
  measurable pool. Thresholds were re-set on the corrected axis, because the axis
  moving silently changes what a threshold means: the favourite arm's 25/15 was
  p90/p75 before and only p78/p52 after, so it went to **30/20** to keep the
  strategy that was specified. The first-half arm's 25 was p92 and is p88 — kept.

---

## NFL Every Game — one bet on every NFL game (paper, 2026-09-13)

By the user's decision: every NFL game Polymarket lists gets exactly one bet, of
the agent's choosing, with profit as the objective. Strategy **"NFL Every Game"**,
hypothesis `H-NFL-SHARP`, table `nfl_candidates` (db/051), cron `*/5`.

```bash
cd agent && source ../ingest/.venv/bin/activate
python nfl_agent.py --once --dry-run   # the board, nothing written, no credits spent
python nfl_agent.py --once             # what cron runs: bet, read closes, settle
python nfl_agent.py --report           # EDGE vs FORCED, net of fee, CLV
python nfl_model.py --validate
python -m pytest tests/test_nfl_agent.py -q
```

**The choice is the strategy.** Per game it prices every full-match token —
moneyline, ~30 spreads, ~40 totals, both sides, ~150 — against the de-vigged
Pinnacle line (Odds API; a median of other books until Pinnacle posts) and buys
the best net EV at the CLOB ask, fee included.
- **EDGE**: EV ≥ 1.5% where PM's line is the same half-point Pinnacle quotes
  (`sharp_exact`), ≥ 3% where it is read off the model (`sharp_model`); from
  24h out; 1u; needs a sharp snapshot fresh for its distance to kick-off.
- **FORCED**: inside 40 min with no bet, the best EV on the board whatever its
  sign, 1u. This is what "every game" costs. Never pool the two in a yield.
- **1u flat on both since 2026-09-19**, like every other agent (it was
  quarter-Kelly 0.5-3u / 0.5u). The 15 earlier trades were restated to 1u —
  stake and payout ×(1/stake), so no bet's result changed; before-state in
  `reports/nfl_stake_restate_2026-09-19.json`. At that point: 12–3, +9.75u net,
  on an average entry EV of −0.83% — 12 wins against 8.7 the prices implied,
  i.e. variance, not a finding.

🔑 **Pinnacle quotes whole numbers, PM only half-points** — so even PM's main
spread is usually not directly comparable (Pinnacle −6 vs PM −6.5). `nfl_model.py`
does the translation: P(margin = k) ∝ Normal(μ, σ) × m(k), key-number factors
fitted by IPF on nflverse 2012-2025 (**m(3) = 2.67, m(7) = 1.74, m(1) = 0.82**),
with **μ and σ solved per game from Pinnacle's spread AND moneyline**. One
global σ ran 1.3-2.1pp short of Pinnacle's own ML on every 6.5+ favourite; the
per-game fit reproduces all 14 week-1 MLs within 0.5pp.

⚠️ **Measured model error, and the haircuts sized to it.** Totals match the
empirical rate within one SE out to ±14.5 points. Spreads miss by 2-4pp in both
directions (1-3.5pt favourite covering s+3.5: 0.333 real vs 0.364 model;
6-7.5pt favourite by 15+: 0.307 vs 0.266). So spread alternates are docked
1.0pp + 0.35pp/point (cap 4pp) and not priced beyond 10 points; totals 0.5 +
0.1/pt. The first dry run, before those haircuts, wanted Eagles −14.5 at 0.22 —
8.5 points out, exactly the tail where the model was least checked.

**Week 1, first board (13 games):** best token per game −2.4% to +0.8% EV —
the soccer finding again (PM's mid sits on Pinnacle; the loss is spread + fee).
No EDGE bet; today's bets are FORCED. Expect FORCED to lose ~1-2%.

- Odds API: 3 credits per snapshot (3 markets, ≤10 books), rationed by the
  nearest unbet game (180 / 45 / 20 min freshness at 24h / 150m / 40m), plus one
  close read at ≤12 min for games already bet (`clv` = sharp close fair / entry
  − 1). Below 60 credits only forced bets and closes may fetch.
- Self-settling from the CLOB winner flag; a 50-50 tie pays 0.5/entry
  (`void`). ⚠️ `resolver.py` now skips `rules->>'self_settling' = 'true'` — it
  maps "first outcome won" to a win, which is wrong for any Under or dog.
- Sides come from outcome labels through PM's `teams` list; the pricing is
  team-relative, so home/away never matters. Gamma's `bestBid/bestAsk` are
  outcome 0's; outcome 1 trades at `1 − ask / 1 − bid`.
- ⚠️ The cron interpreter is **Python 3.9**: no same-quote nested f-strings.
- 🐛 **`strategies_id_seq` was handing out an id that already existed** (19,
  `is_called = false` — s19 was inserted with an explicit id). The first real
  run died on it; `setval` fixed it 2026-09-13. Any new strategy insert — the
  Lab's SAVE AS AN AGENT included — would have hit the same wall.
  `pm_markets_id_seq` sits at 18,180 under one stray row at 1,865,334; harmless
  until the sequence gets there, left alone.

⚠️ **Corrected the same day, at the user's challenge.** The FORCED "~1-2% loss"
above is the cost of TAKING, not of the price: on the 48 tokens Pinnacle prices
exactly, average EV is −4.00% at the ask and **+0.53% at the bid** (median
+0.63%, 35/48 positive — an upper bound; fills and adverse selection are not
modelled, and [[finding-maker-adverse-selection]] went against resting bids in
soccer). PM also pays makers here: $500/day pools (`rewardsDailyRate`) on the
ML, spreads, totals and 1H spreads of every game, for ≥1000 shares within 2.5¢,
and fees are `takerOnly`. And "n ≥ 200 bets, a season" was bad statistics: P&L
at ~2.0 odds needs ~9,600 bets to see +2% (200 gives ±14pp), CLV needs tens, and
whether PM misprices Pinnacle is answered by `nfl_candidates` (~1,000 priced
tokens per snapshot) in days, with no bets. Also measured: **0 violations in
13,801 ladder pairs** (no internal arbitrage), and **~163 of ~381 markets per
game settle before the final whistle** (end of Q1, half time, end of Q3) —
three settlement windows per game against soccer's one.

### Who pays — the NFL profit map (2026-09-13)

The user's correction, taken as the frame: the premise is PROFIT, and a taker's
pricing edge against Pinnacle is one mechanism out of many. Start from *who pays
you and why*. Measured the same afternoon:

| payer | mechanism | measured |
|---|---|---|
| the platform | liquidity rewards — `clobRewards` on ML, spreads, totals, 1H spreads of all 13 week-1 games, **started 2026-09-13**; score ((v−s)/v)²·size, sampled each minute, paid daily, one-sided at 1/3 | 53 markets — but pools are switched on only **~1 day before kick-off** (the 12 games of 09-13 + SNF; MNF and week 2 none) and **every book is cleared at kick-off** (`clearBookOnStart = True`). The rate is per day, the window is hours: a 1000-share quote 1¢ off mid on all 43 real-book rewarded markets from 15:15Z to each kick-off ≈ **$96 on ≈$42k of collateral (0.23%)**, before fills. Best: Cowboys −3.5 $18, 1H Lions −4.5 $7 (alone). ⚠️ My first "≈5-10%/day" multiplied a daily rate by a day the pool does not run and read placeholder books (Bucs −20.5 1H "at 0.49") as empty ones — corrected the same hour |
| impatient takers | spread capture as maker; being the only maker on empty books (props: **2 of 292** have a book) | exact tokens +0.53% at the bid (ceiling) |
| the rules | props resolve **"Under" if the player does not play** (books void) → Under worth + P(inactive); ML tie → 50-50; quarter markets exclude OT, game totals include it | read from each market's `description` |
| time | ~163 markets/game settle mid-game (Q1, half, Q3); garbage-time near-certainties | `nfl_live_recorder.py` records it from today |
| parlay buyers | Combos are RFQ: makers quote in 400ms, payout ≈ product of legs + the maker's margin — a quoter's business | not yet: needs quoter access |
| — | internal ladder arbitrage | **dead: 0 of 13,801 pairs** |
| — | futures logic (SB ≤ conference) · MVP Σbid 1.016 | **dead**: constraint holds on 33 teams; 37 legs of fee eat the 1.6% |
| — | wind ≥15, cold, referees, rest, home dogs vs the CLOSE | **dead vs the close** (all inside the 95% band, n=65-3,794); alive only if PM lags Pinnacle |

`nfl_live_recorder.py` (cron every minute, 15 min before kick-off to 5h after):
every market of every live game from Gamma, the CLOB book of the six main
families via one `POST /books`, and ESPN's scoreboard with per-play win
probability (`situation.lastPlay.probability`). Files, not the DB (quota):
`agent/data/nfl_live/YYYY-MM-DD.jsonl.gz`, one gzip member per minute.

## Strategy factory — thousands of specs, out of sample, paper bots (2026-09-13)

By the user's direction: stop testing one hypothesis at a time. A strategy is a
JSON spec; a grid generates thousands; the engine backtests them out of sample
with the false-discovery rate controlled; the ones that pass paper-trade on the
live tape; the forward record ranks and promotes them. `agent/factory/`,
`agent/factory_cli.py`, db/052 + db/053.

```bash
cd agent && source ../ingest/.venv/bin/activate
python factory_cli.py universes                      # what can be traded, on which features
python factory_cli.py grid [--universe U] [--register] [--refresh]
python factory_cli.py backtest --spec '{"universe": "soccer_inplay_next_goal", "where": [["minute","between",[80,89]],["abs_diff","==",2]], "price": {"odds_min": 2.2, "odds_max": 4}}'
python factory_cli.py leaderboard
python factory_cli.py run --dry-run
python -m pytest tests/test_factory.py -q
```

| universe | price | tape |
|---|---|---|
| `soccer_inplay_next_goal` | real CLOB ask | `pressure_observations`, 31k fixture-minutes / 1,209 fixtures since 08-14 |
| `soccer_inplay_ht_over05` | real CLOB ask | `ht_pressure_observations`, 15k / 1,107 |
| `soccer_inplay_fav_ht` | real CLOB ask | `fav_ht_observations`, 34k / 1,834 |
| `soccer_settled` | real CLOB ask | `settled_market_observations`, 800 |
| `soccer_prematch_close` / `_open` | de-vigged Pinnacle + 0.6pp half-spread | `bt_features`, 101k matches 2012-2026 (`_open` cannot see the close) |
| `nfl_prematch` | de-vigged consensus + 0.5pp | nflverse 2012-2025, ML / spread / total |

- **A spec is a trigger:** entry is the FIRST row of a fixture where every
  condition holds, at that row's ask, and pays the taker fee in and out
  (`cash_out` sells at the bid of the same token at the exit minute).
  `where` may only name a universe's features, all known at that moment.
- **Pass** = Benjamini-Hochberg q ≤ 0.10 on train (per universe) AND positive
  on the held-out test period (in-play: last 30% of fixtures by date;
  pre-match: from 2022-07 / NFL 2021). `cal` = every qualifying row, not just
  the first, averaged per fixture: the higher-powered arm.
- **Status:** candidate (passed, no live tape) · **explore** (positive on
  train, test and cal without passing FDR; paper is free and the forward
  record is the only unbiased test) · paper · promoted · retired. Promotion out
  of explore/paper is BH-controlled across EVERY active strategy (`fwd_q`), so
  200 bots cannot produce a champion by chance. Promoted = shortlist for real
  money, a person's decision.
- The live bots read the same tables the in-play daemons write, through the
  same loaders: backtest and paper cannot drift apart. Crons: `run` every
  minute, `settle` every 10, `grid --refresh --register` daily at 06:00.

**First run: 12,796 specs, 0 passed.** Per universe the count significant on
train sat at or BELOW chance (next goal 4 vs ≈13, pre-match close 5 vs ≈30),
and every train champion reversed on test (NG 70-79' one goal +28.9% → −10.8%;
tier-2 away longshots after steam +35.8% → −56.2%). The calibration arm agrees:
**0 of 347 in-play specs with ≥60 fixtures had a CI clear of zero** (chance ≈
9); median −0.5pp next goal, −4.0pp HT 0.5, −2.6pp favourite at HT.
- Pre-match is well powered (n 200-2,000 per spec): **the sharp close is not
  beaten after PM's costs by any form, rest, tier, goals-average or
  steam/drift rule tested.** That is a conclusion.
- In-play is not: a median tested spec had 66 train and 28 test entries — a
  5-10% edge is invisible at that n either way. It grows every day, and the
  06:00 re-run will say more each week.
- Worth watching, not a result: *next goal 80-89' with a two-goal margin at
  2.2-4* is positive on train and test in several variants (cal +6 to +12pp,
  CIs crossing zero).

12 explore strategies are paper-trading from 2026-09-13 (10 next goal, 1 HT
0.5, 1 favourite HT). ⚠️ The in-play tapes span obs_versions whose pressure
axis changed (xG estimated from 09-11): a spec filtering on `pressure_*`
mixes two scales; `obs_version` is a feature so it can be pinned.

## Every in-play soccer market, every minute (2026-09-13)

`agent/soccer_live_recorder.py` — DATA ONLY. The in-play tapes before it covered
four families; Polymarket lists ~70 markets per game across sibling events
("- More Markets", "- Halftime Result", "- Exact Score", "- First Team to Score",
"- Second Half Result", "- Total Corners"). This records all of them from 10 min
before kick-off to kick-off + 2h45 (the post-whistle window included), each
minute, from the **CLOB book** — Gamma's quote lags it, and in-play that lag is
the whole question.

```bash
cd agent && source ../ingest/.venv/bin/activate
python soccer_live_recorder.py --once          # cron, every minute
python soccer_live_recorder.py --settle        # cron, every 30 min: how each market resolved
python soccer_live_recorder.py --summary       # what today's files hold
python -m pytest tests/test_soccer_live_recorder.py -q
```

Measured on the first run (Sunday 18:25Z): **52 games (20 live), 3,859 markets,
3,735 books, ~980 of them two-sided**, 7.6s per cycle. `POST /books` returns 500
books in 0.46s, so the whole board is ~8 requests a minute. Families: exact
score 884, totals 289, team totals 288, team corners 240, corners 211, spreads
193, 1H/2H team totals 192 each, match odds 156, halftime result 156, second-half
result 156, first to score 156, 1H/2H totals 144 each, BTTS full/1H/2H 48 each…

Files in `agent/data/soccer_live/` (not the DB — over quota; gitignored):
`YYYY-MM-DD.meta.jsonl.gz` (each market once a day, with a day index `i`),
`YYYY-MM-DD.jsonl.gz` (one line per minute: game state from PM's own
`live/score/period/elapsed` + rows `[i, bid, bid_size, ask, ask_size,
bid_usd_top3, ask_usd_top3, last, closed]`), `outcomes.jsonl` (resolutions).
`iter_snapshots(day)` / `load_meta(day)` / `load_outcomes()` read them back.
🔑 Rows carry the day index, not the condition id: 66 characters of random hex do
not compress, and keyed by id a snapshot was 186KB gzipped against **~40KB**
now (~30MB on a busy day).

⚠️ Match statistics are not recorded here on purpose: join `pressure_observations`
on event title and time. A second api-football consumer is how the key was
drained before (the s18 settle cron, 143k calls in a day).

Next: factory universes on this tape (match odds, draw, every totals line, BTTS,
HT result, exact score, first to score, corners), once there are enough settled
games to test on.

## US SaaS pivot — PR 1, the surface (2026-09-13)

By the user's decision on 2026-09-13, the public product is **"see if the price
is wrong before you trade it"**, aimed at the US market. The agent stays in the
repo as the method, not the pitch. PR 1 changes only how the existing board is
written for a US reader. No new data, no new sport.

| | |
|---|---|
| `app/lib/display.ts` | The ONLY place a price or a time becomes text. Odds are American, decimal or implied, remembered per browser (`localStorage np.odds`). The default is keyed on the reader's **time zone**: a US zone gets American odds and a 12-hour clock, anything else decimal and 24-hour. Never keyed on browser language, which renders "quarta, 9/09" on a Portuguese machine. |
| `components/OddsToggle.tsx` | In the nav from 1100px, in Scout's bar below that, and in the phone menu sheet. The buttons are written as the format itself: +150 · 2.50 · 40%. |
| Times | In the reader's own zone, named (`ET` / `CT` / `MT` / `PT` for US zones). Computed client-side only, because the server runs in UTC and has no reader. |
| Quota day | Ends at **midnight ET** (`planTerms.QUOTA_TZ`); it was 00:00 UTC. No migration: `usage_events` stores instants and a day is a window over them. The table had 0 rows at the switch. |
| `app/lib/planTerms.ts` | Price, limits and reset text, safe to import from the client. The pricing page no longer types its own 19/190; `plan.ts` re-exports. |
| Copy | Hero and metadata lead with the sentence. Scout's 1/X/2 is now Home/Draw/Away. Pricing drops the agent row and the /agent/ours link. Footer: 18+, a research tool not a tip service, we do not place trades or hold funds, 1-800-GAMBLER. |

⚠️ **Left alone on purpose:**
- The Claude match brief still writes decimal odds. It is cached per fixture, not per reader.
- The agent's operator-only pages stay decimal.
- Wallet prices stay in PM share cents.
- Dropping-odds' "↓14.1%" is still the fall of the decimal.

⚠️ **The agent side is unchanged:** all paper, nothing live, and no arm near its
verdict gate.

**Blockers for the rest of the pivot:**
- **Auth is down.** The Supabase free plan is over quota (402) until the owner
  buys Pro.
- **No payments or email.** There are no Stripe keys and no email provider on
  Vercel.
- **The Odds API key is on the free tier:** 500 credits a month, 424 left on
  09-13. Recording US books across every sport needs a paid plan.
- **No reliable host for recorders.** They need an always-on machine, and this
  Mac sleeps ~85% of the day.

## US sports boards: Kalshi and Polymarket on ESPN's schedule (2026-09-13)

Six pages, `/nfl` `/cfb` `/mlb` `/nba` `/nhl` `/wnba`, linked from a sport bar
above Scout; soccer stays at `/`. Each page lists every game either exchange
has in the next N days (NFL 9, MLB 3, the rest 7), with both moneylines at the
ask.
- `lib/sports.ts` is the server half and `lib/sportsMeta.ts` the client-safe
  types.
- `/api/sports/[sport]` is cached like Scout: an L1 cache plus
  `unstable_cache`, 60s.

| | |
|---|---|
| Spine | The ESPN scoreboard over the window (`dates=YYYYMMDD-YYYYMMDD`; college adds `groups=80` FBS + `81` FCS). It gives home/away, the start and the live score. **Do not set a User-Agent.** |
| Kalshi | `/events?series_ticker=KX{NFL,NCAAF,MLB,NBA,NHL,WNBA}GAME&status=open&with_nested_markets=true`: public, no key. The date comes from the event ticker, and so does MLB's ET first pitch, which splits doubleheaders. Teams come from the market suffix plus `yes_sub_title`. Quote: `yes_bid/ask_dollars`; depth = `yes_ask_size_fp × ask`. |
| Polymarket | Gamma `/events?tag_id=`: 450 NFL, 100351 CFB, 100381 MLB, 745 NBA, 899 NHL, 100254 WNBA. **Use tags, not series ids**: NFL's series id returned 0 events on 09-13. Sides come from each outcome's label against `teams[].ordering`, never from position. The quote is one `POST /books` for every placed token; Gamma's quote is the fallback and carries no depth. |
| Placement | A market is shown only on an ESPN game where BOTH teams match on a whole name: abbreviation, location, display name, nickname, or Kalshi's city + initials ("New York M", "Chicago WS"). The ET date must agree (Polymarket: start within 3h). An ambiguous match, or two markets on one game, is dropped and counted on the page. Never a substring. |
| Grade | Per venue, on the worse side. **clean**: ≤3¢ wide and ≥$250 at the ask. **thin**: ≤3¢ but under $250. **wide**: ≤10¢. **none**: anything else, which is where Kalshi's placeholder books land. |
| Cheaper | The lower ask per side, marked only when both books grade above `none` and the gap is ≥ ½¢. It is **before fees** (Kalshi 0.07·p·(1−p), Polymarket its own), and the page says so. |

**First run (Sunday evening, 2026-09-13):**
- **NFL:** Kalshi 24/24, Polymarket 24/24. Every game is on both venues.
- **MLB:** 39/39 and 38/38.
- **College football:** with FBS only, 75 of 119 Kalshi and 75 of 128
  Polymarket. The rest were FCS games. With group 81 added: **127 games,
  Kalshi 118/119, Polymarket 127/128.**
- **WNBA and NHL:** Polymarket only; Kalshi had no open events.
- **NBA:** empty until the season starts.

⚠️ **Not built yet:**
- Spreads and totals: Kalshi's `KX*SPREAD`/`KX*TOTAL` ladders against
  Polymarket's `spreads`/`totals`. Only the same line is comparable.
- Sportsbooks (Pinnacle, DraftKings, FanDuel): need a paid Odds API plan.
- A Game Center per US game: rows link out to both venues instead.
- Alerts.

## Both venues everywhere, the best odds in front (2026-09-20)

By the user's decision: every board, the Game Center and the in-play agents
carry **Polymarket AND Kalshi**, and show the **cheaper of the two** — "apostar
a melhor odd possivel" as a product claim and as a real improvement to a paper
record. Football and the six US sports are now **one component**, `BoardView`.

| | |
|---|---|
| `site/app/lib/venues.ts` | the whole vocabulary — venue, quote, book grade, `bestFor` — for football and the US sports both |
| `agent/venues.py` | the same rule for the trader, with Kalshi's football client. 30 tests |
| `site/app/lib/kalshiSoccer.ts` · `/api/venues/soccer` | Kalshi's football board, swept and cached |
| `site/app/lib/kalshiGame.ts` | one fixture's Kalshi prices against Polymarket's own markets |
| `site/app/lib/venueMatch.ts` · `etDate.ts` · `teamMatch.ts` | the cross-venue join |
| `db/054` · `H-BEST-VENUE` (id 37) | s16 **obs_version 6**, s17 **obs_version 7** |

Measured on a live football board, 2026-09-20: **60 of 113 fixtures on both
exchanges, 89 outcomes with a strictly cheaper venue — 52 Kalshi, 37
Polymarket — median saving 1.3pp net of fees, largest 5.4pp.**

```bash
cd agent && source ../ingest/.venv/bin/activate
python venues.py --probe                          # sweep Kalshi's football
python venues.py --fixture "AC Milan" "US Lecce"  # one fixture, both ladders
python -m pytest tests/test_venues.py -q
```

### Kalshi's football costs 139 requests, and it rate-limits

`/events` takes ONE `series_ticker` at a time — a comma list returns nothing,
and there is **no category or tag filter** — and football is spread across 139
game series. Measured on this exact sweep:

| | |
|---|---|
| 10 concurrent, no pacing | **112 of 139 refused with 429** |
| 6 workers, 0.10s apart | 105 retries, 27.9s |
| 5 workers, 0.15s apart | 57 retries, 31.1s |
| **4 workers, 0.25s apart** | **0 retries, 34.7s** ← what ships |

Pushing the pace does not make it faster: every 429 costs a backoff and a
second request.

🔑 **So the index and the prices are separate tiers.** Which fixture exists and
under which tickers changes when Kalshi lists a game → swept rarely, cached 15
min. Prices change every tick → re-read on a 45s clock through
`/markets?tickers=`, which DOES take a batch: ~3 requests for the whole board.
The goals ladders (`KX*TOTAL`, `KX*1HTOTAL`) are swept only for the
competitions the 1X2 sweep just found a fixture in — 138 TOTAL series against
139 GAME ones, and about 25 are playing.

⚠️ **The site merges in the BROWSER.** Polymarket's whole board is one paged
Gamma sweep; waiting 34s for Kalshi server-side would make everyone pay for a
column only some fixtures have. `/api/venues/soccer` is its own route with its
own `maxDuration`, and a Kalshi outage costs the Kalshi column and nothing else.

⚠️ **Nothing may block a poll loop.** The in-play agents run a 60-second cycle
against a live match, so `venues.shared_index(background=True)` refreshes on a
**background thread** and the agent reads whatever snapshot is loaded — none at
all on a cold process. The trade then books on Polymarket exactly as before: a
missing second quote is not a wrong one.

### What the fee does, and the claim that was wrong

Kalshi's taker fee is `0.07·p·(1−p)` against Polymarket's `0.05·p·(1−p)` — 40%
more. The comparison is net of both.

⛔ **"Netting the fee stops Kalshi winning prices it should not" is FALSE**, and
it shipped in four files for about four hours before the tests refuted it. The
fee difference is `0.02·p·(1−p)`, which maxes at **0.005 — exactly `MIN_GAP`**,
the half-cent below which two quotes are one price. So a gap big enough to call
cannot be eaten by it. Searched exhaustively over every price and every gross
gap: the largest net disadvantage a gross-cheaper Kalshi quote can carry is
**+0.000004**. The search is kept as the test that refuted it
(`test_the_fee_never_flips_a_clear_winner`).

What netting it DOES do:
- **halves the saving** — a one-cent gross advantage on Kalshi is worth about
  half a cent once the fee lands;
- **decides the tie** — where both print the same price near even money the fee
  difference reaches 0.5pp on its own and Polymarket is genuinely cheaper.

### Three joins that were wrong, each of which produced a plausible board

🐛 **Kalshi publishes no kick-off for football.** `occurrence_datetime` is the
expected **settlement** — measured at kick-off **+3h on 55 of 67 pairs** and
+2h to +4.5h on the rest. Reading it as a start time and allowing a ±3h window
"worked" only because the true offset sat exactly on the boundary, and it
silently dropped every competition whose games run longer. WHEN now comes from
the **ET calendar date** off Kalshi's own event ticker
(`KXBRASILEIROCGAME-26SEP20VITCRU` → `20260920`). Matched fixtures 57 → 60.

🐛 **Gamma's listing quote is not the book.** Measured across 810 football
markets: Gamma's ask differs from the live CLOB by **>1pp on 15.3%** and
**>3pp on 8.4%**, worst on in-play totals — a Dinamo Zagreb O/U 8.5 quoted
1.000 against 0.020 on the book. Half a cent decides which venue is called
cheaper, so every compared outcome is now read from `POST /books` (~2 requests
for a matchday, `site/app/lib/clob.ts`) and each `VenueBook` says whether its
prices are `clob`, `gamma` or `kalshi`.

🐛 **A price was gated on the fixture's 1X2 spread, not its own.** Santa Cruz v
Floresta: a 2¢ match-result ladder vouching for an Over 2.5 quoted **bid 0.35 /
ask 0.69**, which produced a **27.3pp "saving"**. `bestFor` now grades each leg
on its own book — the 100-game review's finding applied where it belongs. Top
saving on the same board fell to 5.4pp.

🐛 **ESPN's date-RANGE query 400s.** `dates=20260919-20260929` answered on 09-13
and returns `400 Failed to get events endpoint` on 09-20, for every US sport,
while the same window asked one day at a time answers fine. All six US boards
were down until their cache expired. The range is now an optimisation and the
per-day fan-out is the guarantee.

⚠️ **Every board cache key was bumped** (`scout-board-v2`, `sport-board-v2`,
`kalshi-soccer-index-v2`, `kalshi-soccer-priced-v2`). The Data Cache outlives a
deploy, and a fixture written under an older shape is served straight into the
new renderer.

### What the agents do now

s16 and s17 read both books for the line they want and book the paper trade at
whichever is cheaper net of fees. **It changes the price, not the rule** — the
pressure gate, the minute window, the score requirement and the fair-value
tables are untouched, so the same fixtures enter, cheaper where Kalshi is
cheaper. The book gates (max ask, max spread, min depth, s17's `MIN_ODDS`) read
the venue actually being bought rather than always Polymarket's, and the fee is
that venue's.

Every row carries `venue` / `alt_venue_ask` / `venue_saving_pp`, so the
Polymarket-only counterfactual is **recoverable per entry** rather than
estimated — as paired as a comparison gets.

⚠️ **s18 is unchanged.** Kalshi lists no "favourite leading at half time"
market, so there is nothing to compare it against; its rows carry
`venue = 'polymarket'`, which is the truth rather than a default.
⚠️ **The pressure daemon must be restarted** for any of it to apply, as always.

### Still on Polymarket alone

The NFL agent, the strategy factory, the settled-market sweep, the Lab runner
and both recorders. `agent/venues.py` is the layer they would each plug into;
the NFL agent is the obvious next one, because it already enumerates every
token and picks the best net EV — extending the candidate set to Kalshi is the
same decision with more candidates.

---

## CLV framework (how we measure edge)

We have both Pinnacle opening and closing odds, so CLV is measurable now:

- **Entry price** = Pinnacle opening odds (PSH/PSD/PSA, stored as "Pinnacle (legacy)")
- **Benchmark** = Pinnacle closing odds (PSCH/PSCD/PSCA, stored as "Pinnacle (closing)")
- **CLV** = (entry odds / closing odds) − 1
- Consistently positive CLV on a defined set of events = real edge
- Positive ROI with negative CLV = luck. Always measure both.

---

## Key metrics

| Metric | Why it matters |
|---|---|
| **CLV (Closing Line Value)** | Gold standard. Beat the closing line = real edge. |
| **Pinnacle closing as benchmark** | Sharpest available closing line in our dataset — primary fair-value oracle. |
| **Betfair Exchange closing as benchmark** | Cross-check against the second-sharpest market (~13k matches). |
| **Polymarket spread vs Betfair/Pinnacle implied** | Direct edge signal — where Polymarket diverges from sharp consensus. |
| **Yield %** | Profit / total staked. More stable than ROI on small samples. |
| **p-value vs ROI=0** | Require p < 0.05 before promoting. |
| **Sample size** | Minimum 200 selections before any conclusion. |

---

## Methodological rules (non-negotiable)

1. **No lookahead bias.** Every feature used must have been available before kickoff.
2. **Train/test split.** Walk-forward validation. Never evaluate on discovery data.
3. **Reject silent p-hacking.** Every hypothesis tested goes into `research_hypotheses`.
4. **Minimum 200 selections** before any conclusion.
5. **CLV is king.** Positive ROI with negative CLV = luck. Always measure both.
6. **The agent must be creative.** Reject obvious hypotheses unless data confirms them.

---

## Content strategy (nopredictions.com + X / Twitter)

- **Brand:** NOPREDICTIONS · domain: nopredictions.com
- **Tagline:** *No predictions. Just edges.*
- **Language:** English
- **Sport:** Football only
- **Primary venue:** Polymarket · **Sharp benchmark:** Betfair Exchange
- **Angle:** AI agent finding mispricings on Polymarket — riding the AI + prediction-markets wave at the same time
- **Hook:** 15 years of human market experience handed to an AI — can it find edges humans miss on the prediction market everyone is watching?
- **Transparency:** every hypothesis, every position, every failure posted publicly with signed timestamps before the event resolves
- **Key recurring threads:** thread per hypothesis, weekly P&L digest, the research graveyard, "what Polymarket got wrong this week"

---

## Tech stack

- **Database:** Supabase (Postgres) — free tier
- **Ingestion:** Python 3.9+, pandas, psycopg2-binary, requests, beautifulsoup4
- **Agent:** Anthropic SDK (Python), model `claude-opus-4-6`
- **Website:** Next.js 14 + Supabase JS + Tailwind CSS, deployed on Vercel
- **Content:** X (Twitter) in English

---

## Sources

- Football-Data.co.uk: https://www.football-data.co.uk/mmz4281/{YYYY}/{CODE}.csv
- FBref: https://fbref.com (rate limit: 1 req / 3s)
- Polymarket Gamma API: https://docs.polymarket.com/
- Betfair Historical Data (benchmark): https://historicdata.betfair.com/
- Betfair API docs (benchmark): https://developer.betfair.com/
- Supabase docs: https://supabase.com/docs
- Anthropic API docs: https://docs.anthropic.com
