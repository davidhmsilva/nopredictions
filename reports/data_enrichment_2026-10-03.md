# Data enrichment — what was missing, what is free, what is now loaded (2026-10-03)

The question: what data is missing for long-run pattern analysis, using only
free sources? Answered by auditing the database first, then loading every
free source that closes a gap. Four new stages (K, L, M, N), three
migrations (065-067), no paid source, no key.

## The gaps, before

| gap | measured | why it mattered |
|---|---|---|
| No odds outside the 22 Football-Data leagues | MLS, Brasileirão, Argentina, Liga MX, Colombia, Chile, Libertadores: 13.3k matches, **0 odds**; internationals 0 | the leagues Polymarket lists most could not be tested in the Lab or the factory |
| No Asian handicap | `match_odds.ah_*` empty on **all 530,862 rows** | the AH line is the sharpest one-number summary of strength |
| No O/U 2.5 price before 2019-20 | 0 matches before 2019-07 | totals research ran on half the sample; the in-play tables' pre-match-total bucket had no input before 2019 |
| Minute-level goals for the Big 5 only | Understat, 15,120 matches, ending 2024-25 | late goals / first half / favourite-at-HT fair values were fitted on the leagues PM lists least |
| No red cards with a minute | 0 | the largest in-play state change was invisible |
| No in-play price history | `pm_ticks` starts 2026-07-21 | every in-play question about PM had to wait for forward data — the verdict gates were months away |
| No cups or European games | 0 | rest days and congestion were computed from league games only |
| No referee, attendance, manager, formation, line-up | 0 | — |
| xG | 18,948 matches (Big 5 to 2024-25 + some Americas) | nothing for 2025-26 |

## Loaded today

| stage | source (free) | what | volume |
|---|---|---|---|
| **K** `stage_k_fd_extra.py --extra-leagues` | football-data.co.uk/new/ | 16 countries, results + closing 1X2 (Pinnacle to 2026-01, Betfair Exchange, max, average, Bet365), 2012 → now | **53,668 new matches** in 13 new competitions; **9,661 Stage G matches priced** (they had none); 200k odds rows |
| **K** `--enrich` | the 22 Stage A CSVs | AH line + prices (closing AHCh and the earlier AHh); pre-closing max/average 1X2 + O/U + AH as `MAXO`/`AVGO` (Betbrain until 2018-19, Football-Data's own after); xG from 2026-27 (19 of 22 divisions); referees (England, Scotland) | AH on **124,345 matches**; O/U on 124,568 (**69,927 before 2019**); 1,089 xG; 47k referees |
| B (re-run) | Understat | match xG 2025-26 | 1,415 matches |
| **L** `stage_l_espn_events.py` | ESPN public scoreboard | every goal and card with minute and stoppage minute, ~75 competitions incl. cups, Europe, internationals, 2010 → now | **304,771 matches**, 1.84M events, 262,949 timelines that reproduce the final score, **185,590 linked** to `matches`, **63,817 red cards** |
| **M** `stage_m_price_paths.py` | PM CLOB `/prices-history`, Kalshi candlesticks | 1-minute price path of every settled football market, kick-off −90' → +180' | PM 55,458 markets (from 2024-08, 14.7M points), Kalshi 31,288 (from 2025-05, 5.1M points with bid/ask) — files, `ingest/data/price_paths/`, ~40 MB |
| **N** `stage_n_transfermarkt.py` | dcaribou/transfermarkt-datasets (CC0) | 88,958 games 2006 → 2026-07 incl. cups and Europe; attendance, venue, managers, formations, referees; XI market value as of the day; rotation | **58,034 of 58,206** league games linked; context on 97k matches; 162,230 club-games of line-up features |

Database: 2.98 GB → 3.63 GB. Football matches 146,451 → **200,119**; leagues
with matches 40 → 53.

### How the joins were kept honest

Every link needs the date, the **same final score** and both team names
through `fixture_match.team_score`, unique best only, one-to-one. Names alone
fail on exactly the spellings that matter — "Athletico-PR" against
"Atletico Paranaense" scores 0.00, "UNAM Pumas" against "U.N.A.M. - Pumas"
0.20, "Atlético de Madrid" against "Ath Madrid" 0.50 — so each linker adds a
**pairing pass**: a row with one side already proven (two agreeing name
matches; Transfermarkt and ESPN publish stable club ids) whose candidates — same
score, inside the window, that team on that side — number exactly one, is that
match, and the other spelling is learned from it. Recovered: 672 Football-Data
rows, 10,179 Transfermarkt games, 20,589 ESPN matches. Not merged, by design:
10 play-off draws decided after extra time, where the sources disagree on the
score.

ESPN timelines were checked against the money: on three EPL / La Liga fixtures
every goal shows in the Polymarket path as the jump you would expect, 2-5
minutes after listed kick-off + minute + 15' (late kick-offs, long half-times).
No hour-sized clock error.

## Traps found on the way

| | |
|---|---|
| Supabase session pool | **All 15 session-mode slots were held by idle daemon connections** (some idle 15h). One extra client refuses the agents' crons with `EMAXCONNSESSION`. Every new loader connects through the transaction pooler (`ingest/db_pool.py`, port 6543). Flagged as its own task. |
| Kalshi history | Markets settled before the cutoff at `/historical/cutoff` (2026-08-04) 404 on the live candlestick route and live at `/historical/markets/<t>/candlesticks` — with different field names (`close` vs `close_dollars`, `volume` vs `volume_fp`). Reading one shape stored every historical quote as None. |
| ESPN | `dates=YYYY&limit=1000` returns a whole calendar year in one request (a range `YYYYMMDD-YYYYMMDD` answers 400). Own goals are credited to the team that benefits. Box stats on the scoreboard are zeros for finished games. |
| Football-Data `/new/` | Times are UK local (J1 "09:00" = 08:00Z in BST); seasons switch from "2012/2013" to "2014" inside one file; Argentina mixes the Copa de la Liga in; one Swiss file carries two Challenge League play-offs. |
| Football-Data typos | `1314_SC2.csv` has `BbAHh = -275` for −2.75. Lines off the 0.25 grid or outside ±10 are dropped; prices outside (1, 1000) too. 8 older Stage A rows carry 0.000 prices from the source; left untouched. |
| Re-runs | Stage K's overlap window must come from Stage G's rows only (`fd_source IS NULL`); counting its own rows would skip every new result as "unmatched". |

## Still missing — free, not done

1. **Betfair historical data, BASIC plan** — free with a Betfair account:
   1-minute last-traded price of every Betfair football market since 2016,
   in-play included. The sharpest in-play history that exists and the right
   benchmark for the PM paths. Needs the account holder to log in at
   historicdata.betfair.com, take the free BASIC plan for Soccer and download
   the files; parsing them is straightforward. Availability from Portugal not
   verified.
2. **Understat shot-level data** (every shot with minute, xG, x/y) for the
   Big 5 + RFPL since 2014 — ~20k requests, ~8h at a polite rate. Would test
   in-play pressure historically on ~50× the forward sample. Not run:
   `finding-live-reading-ceiling` says box-score pressure adds almost nothing
   over free state, so the crawl is long for a likely null.
3. **ESPN box scores** (shots, corners, possession) for the leagues
   Football-Data has no stats for — one `summary` request per match, ~100k.
4. **Open-Meteo historical weather** per stadium — needs coordinates
   (geocode Transfermarkt's stadium names). Weak prior: the NFL weather angles
   were dead against the close.
5. **NBA odds 2022-23 → now** — no maintained free source found.
6. **A free sharp close after 2026-01** — none that is legitimate. Pinnacle's
   public API is closed, OddsPortal is scraping, The Odds API's free tier is
   500 credits a month. Betfair's close (already loaded) stays the fallback.
7. **StatsBomb open data** — event data for a handful of competitions; marginal.

## Read before using

- PM `p` is **(best bid + best ask)/2 with a missing bid counted as 0 and a
  missing ask as 1** (measured 2026-10-04): the mid on a two-sided book, but
  ask/2, (bid+1)/2 or 0.5000 on a one-sided or empty one — artefacts that
  manufacture a longshot-bias shape. Never a fill. Filter them (see
  `reports/pm_inplay_calibration_2026-10-04.md`); executable questions belong
  to the Kalshi bid/ask or the soccer_live tape.
- `t` is minutes from the **listed** kick-off. Anchor the match clock per
  fixture (the first goal's price jump against ESPN's minute) before any
  minute-level work.
- The `/new/` leagues carry 1X2 closes only — no HT score, no stats, no O/U.
  HT comes from ESPN where linked (`timeline_ok`).
- New-league kick-offs are real UTC; Stage A's 22 leagues keep their
  London-clock convention (`finding-fd-kickoff-london-time`).
- 398 new teams. A club that also appears elsewhere under another spelling can
  hold two ids; `team_aliases` (source `espn`, `fd-<cc>`) and `tm_club_map`
  carry what the linkers proved.
- Transfermarkt ends 2026-07-06 until its maintainer refreshes it: history,
  not live context.
- ESPN detail thins before ~2016 outside the big leagues (Turkey, Belgium,
  Ireland, Uruguay, Costa Rica…); use `timeline_ok` rows only.
- **The Lab will change on its next refresh.** `refresh_bt_lab()` (db/062)
  reads every league with a Pinnacle or Betfair close except the NBA, so it
  will add the 13 new leagues and the newly priced Americas — ~63k matches,
  with no HT score and no O/U on the `/new/` rows. If the Lab should stay on
  the 22 leagues for now, filter there before it next runs. A product
  decision, not made here.

## What it unlocks — proposed, not tested

Each needs pre-registering (`research_hypotheses`) before it is run.

1. **Is PM's in-play price calibrated?** PM paths × ESPN timelines,
   2024-26: realised − price by minute, score state and market family. The
   open question of "Over Late Goals" (PM's side unmeasured) and the late-game
   longshot bias, on history instead of months of forward data.
2. **The same at executable prices** on Kalshi's bid/ask, 2025-26.
3. **Red cards**: goal rates after a red by minute, score and league — the
   in-play tables never had them.
4. **In-play tables on the leagues PM lists**: late goals, first half,
   favourite at HT refitted on 177k linked timelines instead of the Big 5.
5. **Rotation and XI value against the close**: is rotation after a European
   midweek fully priced (Transfermarkt `xi_changes`, `days_since_prev`)?
6. **Totals and AH 2012-2019**: the factory's pre-match totals universe
   roughly doubles.
7. **Opening → closing drift** 2010-2026 (`MAXO`/`AVGO` → `MAX`/`AVG`, `PS` → `PSC`).
8. **The 13 new leagues** in the Lab and the factory, closing odds from 2012.

## Refresh

```bash
cd ingest && source .venv/bin/activate
python stage_k_fd_extra.py --extra-leagues --refresh     # weekly: new results in the 16 countries
python stage_k_fd_extra.py --enrich --seasons 2026-27    # after Stage A: AH / xG / referees, live season
python stage_l_espn_events.py --refresh-current          # daily: this year's timelines + relink
python stage_m_price_paths.py                            # after Stage J: newly settled markets (resumable)
python stage_n_transfermarkt.py --refresh                # monthly, when the dataset moves
python -m pytest tests/test_data_enrichment_parsers.py -q
```

None of these is scheduled yet. On Hetzner they belong after Stage A and
Stage J.
