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
~/Documents/agente/
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
│   ├── injury_tracker.py              ← real-time player injury / suspension data ✅
│   ├── market_flow.py                 ← whale activity + smart money signals ✅
│   ├── live_tracker.py                ← rolling-window pressure signals ✅
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
│   ├── resolver.py                    ← resolves trades + calculates CLV ✅
│   ├── tools/
│   │   └── db.py                      ← DB read/write helpers
│   └── _archive/                      ← retired research pipeline
│       ├── generator.py
│       ├── backtester.py
│       ├── critic.py
│       ├── prompts/
│       └── tools/runner.py
└── site/                              ← Next.js public dashboard ✅ LIVE
    ├── app/
    │   ├── layout.tsx                 ← root layout + SEO metadata
    │   ├── page.tsx                   ← main SPA (1372 lines — needs decomposition)
    │   ├── globals.css                ← dark theme + responsive styles
    │   └── lib/
    │       └── supabase.ts            ← Supabase client + query functions
    ├── package.json                   ← Next.js 14.2, React 18.3, Supabase JS 2.43
    ├── tailwind.config.ts
    ├── next.config.mjs
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
| Stage F — NBA pipeline | ✅ 15k games, Elo model, scanner |
| Stage G — International results | ✅ 8,394 matches, 48 WC teams in DC model |
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
| Monte Carlo sim engine | ✅ Vectorized, 50k sims in <600ms; passes Poisson sanity |
| InjuryTracker + MarketFlow | ✅ Real-time injury / whale-money signals |
| Resolver | ✅ Settles trades + calculates CLV |
| Public website | ✅ Live at [nopredictions.com](https://nopredictions.com) |
| Git repo | ✅ Remote: github.com/davidhmsilva/nopredictions |
| X / Twitter launch | ⏳ Pending first edge results |

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
