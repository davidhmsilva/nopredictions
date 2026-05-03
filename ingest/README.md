# Stage A — Football-Data.co.uk ingestion

Downloads historical match data from [football-data.co.uk](https://www.football-data.co.uk/)
and loads it into the schema from `db/001_schema.sql`.

**Scope:** 22 European leagues × ~15 seasons (2010-11 → current). ~80k matches.

---

## Setup

1. Make sure you've already run `db/001_schema.sql` and `db/002_seeds.sql`
   against your Supabase project.

2. Install dependencies:
   ```bash
   cd ingest
   python -m venv .venv
   source .venv/bin/activate        # on Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Configure DB connection:
   ```bash
   cp .env.example .env
   # edit .env and paste your Supabase connection string
   ```

---

## Usage

```bash
# Dry run first — downloads CSVs, parses them, counts rows. No DB writes.
python stage_a_football_data.py --dry-run --leagues ENG-PR --seasons 2023-24

# Sanity check on one league/season
python stage_a_football_data.py --leagues ENG-PR --seasons 2023-24

# Full backfill (22 leagues × 16 seasons — takes ~10-20 min)
python stage_a_football_data.py --continue-on-error

# Incremental update (re-run current season only)
python stage_a_football_data.py --seasons 2025-26 --continue-on-error
```

The script is **idempotent**: re-running updates existing rows via `ON CONFLICT
DO UPDATE`. Safe to run on a cron for current-season updates.

CSVs are cached to `.cache/fd/` so re-runs don't re-download.

---

## What gets ingested per match

- **Core:** kickoff, home/away teams, final score, half-time score
- **Stats (when available):** shots, shots on target, corners, fouls, cards
- **Odds (closing preferred, opening as fallback):**
  - 1X2 from Bet365, Pinnacle (closing + opening), Betway, William Hill, BetVictor, Betfair Exchange
  - Market Max and Market Avg
  - Over/Under 2.5 goals where available
- **Teams** are auto-created with aliases tracked per source

Asian Handicap columns are **not** ingested in this first pass. Schema
supports them; we can add them later if they prove useful for strategies.

---

## What's **not** in Stage A (coming later)

- xG and advanced stats (Stage B — FBref scraper)
- Polymarket / Kalshi markets (Stage C/D)
- UEFA club competitions (no FD coverage; comes with Stage B)
- Asian handicap columns (easy add when needed)

---

## Operational notes

### Time zones
Kickoff time is stored as `TIMESTAMPTZ` but is actually local league time
interpreted as UTC. This is **imprecise** but adequate since backtests group
by date, not by time-of-day. Proper localization is a future enhancement.

### Team name mapping
Football-Data has its own team naming (e.g. "Nott'm Forest", "Man United").
The script creates canonical teams from these names and tracks the FD name
as an alias in `team_aliases`. When we later ingest FBref ("Manchester Utd")
or Polymarket data, we'll add those as additional aliases for the same team.

### CSV format evolution
Football-Data has added columns over the years. Old seasons (pre-2015) don't
have closing odds — the script automatically falls back to opening odds.
Very old seasons (pre-2005) may be missing many columns; the script tolerates
their absence.

### Rate limiting
Football-Data CSVs are static files on a normal web server. No rate limiting
needed for typical backfills (~350 CSVs total).

### Database size
After a full 2010-11 → 2025-26 backfill across all 22 leagues, expect:
- ~80k rows in `matches`
- ~400k rows in `match_odds` (9 bookmakers × most matches)
- ~70k rows in `match_stats`
- Total: ~200 MB with indexes — well within Supabase free tier.

---

## Troubleshooting

**`bookmakers table empty`** — You didn't run `002_seeds.sql`. Run it.

**`league not seeded: XXX`** — Your schema is missing a league. Re-run
`002_seeds.sql`.

**Connection errors from Supabase** — Check that your `DATABASE_URL` uses
the right host. For long-running ingestion, use the **direct connection**
string (port 5432), not the pooler.

**A specific CSV fails to download** — Football-Data occasionally has
missing files (especially mid-season before data is posted, or some lower
divisions). Use `--continue-on-error`.
