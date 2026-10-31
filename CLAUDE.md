# NOPREDICTIONS — AI agents for prediction markets

**Brand:** NOPREDICTIONS · **Domain:** [nopredictions.com](https://nopredictions.com) · **Direction since 2026-10-28, by the operator**

## What this project is

An app with one job: **a person describes an idea, we understand it, turn it into
a strategy, and deploy it as an agent whose goal is to make that person money.**

Three things, and nothing else:

| | | |
|---|---|---|
| **Create** | `/create` | An idea in your own words → questions → a strategy with exact rules → what it would have made → Deploy |
| **My agents** | `/agent` | Run, pause, publish, rename, archive; every bet, the curve, the record |
| **Marketplace** | `/marketplace` | Agents people chose to publish, with strategy and record; copy one into your account in one press |

The test for any new work: **does it help someone get from an idea to a running
agent, keep that agent running, or find one worth copying?** If not, it does not
belong in the product. The odds boards, the Game Center, team pages, results,
dropping odds, the insights articles and the Wallet tab all left on 2026-10-28 for
that reason; their URLs redirect (`site/next.config.mjs`), their code is in git, and
the articles are in `docs/insights/`.

⚠️ **Agents paper-trade.** Practice bets of 1 unit at real Polymarket prices, settled
on the real result. Nothing in this repo places a real order for a user. "Makes
money for the user" is the goal the product is built towards, not a claim it may
make: real-money execution is not built, and building it is a decision for the
operator (custody, venue access — Polymarket is blocked in Portugal, Kalshi's
availability is unverified — and regulation).

---

## How an idea becomes an agent

This pipeline IS the product. Each step, and where it lives:

1. **Describe.** `app/create/page.tsx`. The home page's box hands over as `?idea=`,
   and the idea survives a trip through sign-up the same way.
2. **Understand.** `/api/lab/plan` + `lib/labQuestions.ts`: Claude decides the kind
   of rule (pre-match, live, not possible) and asks at most 4 questions, only about
   choices that change what the agent would do, in the user's own language. Each
   answer is appended to the idea as a plain clarification, so the next step needs
   no second input format. A failure here skips to the next step: questions are a
   help, never a gate.
3. **Write the strategy.** `/api/backtest` (pre-match, `ParsedSpecSchema` →
   `sanitizeSpec` → `Spec`) or `/api/lab/inplay` (`InplaySpec`, `cleanInplay`). Not
   possible means an honest refusal plus the nearest thing that is possible.
4. **Show it.** `components/StrategyCard.tsx`, through `lib/strategyText.ts`
   (`describeSpec`) and `lib/inplaySpec.ts` (`describeInplay`). 🔑 **Built from the
   SPEC, never from the model's paraphrase**: what the page says is what will run.
5. **Test it.** `run_backtest` in Postgres over 101,469 league matches at Pinnacle's
   close, shown by `app/create/LabResult.tsx`. Both teams to score and the other goal
   lines have no such close, so `run_backtest_venue` replays them over about a year at
   Polymarket's own prices, and the page says plainly which test this is. A live rule
   has no replay; its own record is the test, and the page says so.
6. **Deploy.** `POST /api/agents {deploy: true}` saves and switches on in one step
   (`saveLabAgent`). The backtest stored with it is recomputed on the server from the
   spec, never taken from the browser. When history says it lost money, Deploy is
   demoted to "Deploy anyway" and "Change the idea" takes the primary button.
7. **It runs.** `agent/lab_strategy_runner.py` every 5 minutes for pre-match specs,
   `agent/lab_inplay_runner.py` on the pressure poll for live rules. One paper bet of
   1 unit per fixture, settled on the CLOB `winner` flag of the token bought.
8. **Manage.** `/agent`, `/agent/<id>`. A rule is never edited in place, because the
   record was earned on it: "Build a variation" opens Create with the words prefilled
   (`?draft=`) and makes a new agent.
9. **Publish and copy.** Below.

### What an idea can be today

| kind | markets | conditions | leagues |
|---|---|---|---|
| **Pre-match** (`lib/backtest.ts`) | 1X2, Over/Under 2.5 | odds band, favourite/underdog, a named team, rest days, its last game (result and margin), runs (won, unbeaten, lost, winless, up to 10), points in the last 5 overall and at this end, goals scored, conceded and total in the last 5, seasons. On the home or away team, or on "the team" at either end and its opponent (`flatLegs`) | the 22 in `LEAGUES` |
| **Pre-match at Polymarket's prices** (`VENUE_MARKETS`, db/085) | both teams to score, Over/Under 1.5, 3.5, 4.5 | the same, but no favourite or underdog: these back no team | 19 of the 22: the Scottish Championship, League One and Two have no history |
| **Live rule** (`lib/inplaySpec.ts`) | a team to win, the draw, one more goal | team, kick-off odds, minute window, score, pressure, price; exit: hold, sell after a goal, sell at a minute | any fixture on the pressure poll, or named leagues |
| **Backtest only** | NBA moneyline, spread, total | 2014-15 → 2021-22, consensus close | cannot deploy: no NBA boards here |

Not possible yet, and the model says so: other sports live, corners, cards, other goal
lines, handicaps, half-time markets pre-match, other goal lines, league-table position, cups and
European competitions, player-level anything.

🔑 **Widening this language is the most valuable engineering work in the project**,
because it decides how many people's ideas become agents at all. A new pre-match
condition has to land in all of: `lib/backtest.ts` (`Spec`, `FILTER_FIELDS`), the prompt
in `/api/backtest`, `cleanSpec`, `bt_recent_games_fit` in SQL (db/083, read by both
`run_backtest` and `run_backtest_recent`), `refresh_bt_features` for its column,
`describeSpec`, `form_of` / `TEAM_FIGURES` in `agent/lab_strategy_runner.py`, and a column
of `bt_venue_lines` with its figure in `refresh_bt_venue_lines` (db/085: a column it lacks
reads NULL, so that replay comes back empty rather than broader). Then
check parity: the SQL against `form_of` on the same results, and `form_of` over ESPN
against `matches`. A condition the runner
cannot read must be in `run_blocker` on BOTH sides, so it tests and honestly refuses
to deploy rather than trading a different rule.

### What the runner will not do

Each of these is a way a live trade silently stops being the rule the user tested
(`agent/lab_strategy_runner.py`, `tests/test_lab_strategy_runner.py`):

- Trade a competition the backtest never saw, or enter earlier than 45 minutes
  before kick-off (the backtest bought at the close).
- Read a fixture's sibling events apart. The moneyline is on the main event and every
  goal line and BTTS on "- More Markets", so `fixtures_of` merges them first, and one
  bet per fixture holds across both. Family from `sportsMarketType`, line from `line`,
  side from the outcome label: never the question text.
- Trust Polymarket's title order for home and away. Anything that names a side needs
  ESPN to confirm it, or the fixture is skipped: a side error inverts the bet.
- Buy into a broken book: spread > 5pp or under $50 of ask depth.
- Guess a team's form. Everything a rule can ask about a team's recent games is read
  live from ESPN by the backtest's own definitions (`form_of`, league games only, 5
  games or nothing, only games before this kick-off). A team it cannot read fails the
  rule, like a NULL in the backtest. A failed request is `None`, never "no games".
  ⚠️ A club that changed division has no history in ESPN's league-scoped feed, so a form
  rule does not fire on it until it has played enough games in the new one.
- Trust an ESPN league code because it answers. `sco.3` and `sco.4` return a season
  frozen in September 2025, so Scottish League One and Two can only run rules that do
  not name a side.
- Walk past a bound it does not know. `unreadable_bounds` pauses the rule instead.
- Settle by "first outcome won", which books a lost Under as a win.

---

## The Marketplace

Public, reachable without an account. `lib/agents.ts` (`listPublished`, `getPublished`,
`copyAgent`, `viewerRelations`), `db/082`.

- **Publish** is the owner's act, from the agent's page, with a confirmation. Only
  agents built on Create: a hand-written daemon has no rule anyone could copy.
- **Copy** is a snapshot: its own row, its own owner, its own paper trades from the
  moment it runs. The original's record is never shared, and later changes do not
  follow. One live copy per original per user; asking again opens the one they have.
- **A copy cannot be published.** The Marketplace credits whoever had the idea.
- **Authors** show as `profiles.public_name`, else a stable pseudonym hashed from the
  account id, or NOPREDICTIONS for the operator. 🔑 Never an email. "NOPREDICTIONS" is
  reserved. `AUTHOR_SQL` is the one definition.
- **Ranking** defaults to P&L, because that is what someone came to see, and every
  card carries its sample, "Early record" under 200 settled bets, and what history
  said. A lucky fortnight must not be able to pass for a result.
- **It has to survive the database.** The public reads go through
  `lib/marketplaceCache.ts` (Next's Data Cache, 2 minutes, stale while revalidating), so
  once loaded the Marketplace outlives a stall. Publish, unpublish, rename and archive
  drop that cache at once (`revalidateTag`). Pages wait at most a few seconds for it
  (`lib/deadline.ts`), then the browser asks.

⚠️ **`strategies.name` is UNIQUE across every account and must stay so.** The hand-written
agents find their own row with `WHERE name = %s` (nfl_agent.py, pressure_agent.py,
nba_agent.py…). A user's agent sharing one of those names would have trades booked
under the wrong row. So a clash gets the first free "<name> (n)" (`freeName`), which
the owner can rename.

---

## Product rules

1. **Honest about what it is.** Paper is said on every page that shows a record. A
   backtest is not a promise. No copy claims an agent makes money: the record says
   whether it did. This project has deleted a form that sent nowhere, a LIVE badge
   above a not-live banner and a "+-1.8%" drawn in green; do not rebuild them.
2. **The evidence speaks at the moment of decision.** The verdict sits above Deploy,
   and it reads CLV: money made while the odds never moved your way "looks like luck".
3. **A plain page for someone who bets.** Decimal odds for the operator, the reader's
   own format on the site (`lib/display.ts`, the only place a price becomes text).
   Numbers a bettor uses first; intervals and p-values one fold down, never gone.
4. **Change what was asked.** The operator's product decisions are not re-argued.
5. **Under 200 settled bets it is a record, not a result.** Sample sufficiency is
   checked before any claim, on any page and in any answer.

---

## Security: three holes this project has already closed

- **The anon key ships in the JavaScript.** RLS is on and every read of strategies and
  trades goes over `DATABASE_URL` on the server, with its scope (owner, or `is_public`)
  in the SQL (`lib/agents.ts`). A new table grants the client nothing; a table the
  browser must read needs an explicit `GRANT SELECT`, and a view must not write through
  to the table under it (db/045, db/048, db/050).
- **A quota the client can reach is not a quota.** `usage_events` has RLS with no
  policies; `claimUse` counts and inserts in one transaction behind an advisory lock.
- **A client that could update `profiles` could set `plan = 'pro'`.** Only the Stripe
  webhook grants Pro (`pro_grants`, db/047). `public_name` is set through `/api/me/name`
  on the server, for the same reason.

---

## Map

```
site/                                Next.js 14, Vercel (Root Directory = site)
  app/page.tsx                       home: the idea box, how it works, top Marketplace agents
  app/create/                        the builder: page, Clarify, LabResult, InplayResult
  app/agent/                         My agents: list, /agent/<id>, AgentView (owner + public modes)
  app/marketplace/                   list, /marketplace/<id>, MarketCard, PublicAgent
  app/account · login · pricing · welcome · terms · privacy · refunds · auth/
  app/components/                    AppShell, StrategyCard, CopyButton, HomeView, QuotaStrip
  app/api/
    lab/plan · lab/inplay · lab/quick · backtest     idea → strategy (quick = free, no model)
    agents · agents/[id]                             save, deploy, run, pause, publish, rename, archive
    marketplace · marketplace/[id] · [id]/copy · me  public list and page, copy, the viewer's relation
    me · me/name · stripe/*
  app/lib/
    agents.ts                        every read and write of an agent, scope in the SQL
    backtest.ts · labRun.ts · labQuick.ts · labQuestions.ts   the pre-match language and test
    inplaySpec.ts · inplayPrompt.ts  the live language (⚠️ mirrored in lab_inplay_runner.py)
    strategyText.ts                  a spec in words
    plan.ts · planTerms.ts           quota + limits (planTerms is client-safe)
    display.ts                       odds and dates as text, in the reader's own format
    marketplaceCache.ts              the public reads, kept through a stalled database
    deadline.ts                      a server render never waits long for the database
    supabaseBrowser.ts · supabaseAuth.ts  ⚠️ NEVER merge: the server half imports next/headers
agent/                               Python: the runners and the operator's own agents
  lab_strategy_runner.py             pre-match specs, one short cycle per run
  lab_inplay_runner.py               live rules, driven by pressure_agent.py's poll
  espn_stats.py                      scoreboard + team_games, no key ⚠️ DO NOT SET A USER-AGENT
  fixture_match.py                   the only way to match team names ⚠️ never by substring
  db_txn.py                          every daemon's connection: autocommit, no I/O in atomic()
ingest/                              the data behind the backtest (Stages A and J daily, see below)
db/                                  numbered migrations, applied by hand
docs/research_log.md                 everything learned, read it before touching what it covers
```

### Where it runs

- **Site:** a push to `main` deploys to production in ~3 minutes; other branches get
  preview URLs. `cd site && vercel --prod --yes` still works by hand.
- **Agents:** a Hetzner VPS, systemd (`deploy/hetzner/`, on the deploy branch). ⚠️ **It
  tracks `claude/sharp-gates-ua7ro2`, not `main`: pushing to `main` does not update
  the agents.** Merge main into that branch, push, then `sudo -u np git -C
  /opt/nopredictions pull --ff-only` on the server. A timer job picks up new code on
  its next run; a daemon needs `systemctl restart np-daemon@<name>`. A timer job that
  fails raises no alarm anywhere: look at `systemctl --failed`.
- **Database:** Supabase. ⚠️ Scripts run from a laptop go through the transaction pooler
  (port 6543): the session pooler (5432, pool size 15) is held by the daemons, and one
  more client can refuse the crons. It saturates at peak hours: `ECHECKOUTTIMEOUT` and
  statement timeouts took 142 job runs in 6h on 2026-10-30.
- A daemon and a site deploy move separately, so a change both sides must agree on
  (`run_blocker`, say) goes to the server first. The other order pauses agents.

### Commands

```bash
# the site
cd site && npm run dev
cd site && npx tsc --noEmit -p .        # a type error fails the Vercel build

# the runners
cd agent && source ../ingest/.venv/bin/activate
python lab_strategy_runner.py --once --dry-run
python lab_strategy_runner.py --spec '{"market":"ou25","side":"over","leagues":null}' --window 1440
python lab_strategy_runner.py --settle
python -m pytest tests/test_lab_strategy_runner.py tests/test_lab_inplay_runner.py tests/test_espn_stats.py -q

# the results behind the backtest (on the server: np-stage-a.timer, daily)
cd ingest && source .venv/bin/activate
python stage_a_football_data.py --current-season --refresh --continue-on-error

# Polymarket's prices behind the BTTS and goal-line replay (on the server: np-stage-j.timer, daily)
python stage_j_venue_history.py --pm-catalog --recent-days 5 --link --prices --venue polymarket --linked-only
```

⚠️ **Never `rm -rf .next` while a preview server is running**: `next dev` and `next build`
share it, and the collision looks like a missing vendor chunk on a page that builds
fine. `NEXT_DIST_DIR=.next-build npx next build` keeps them apart.

---

## The operator's own agents

The operator's account (`profiles.role = 'owner'`, unlimited) also holds hand-written
agents (`strategies.source = 'agent'`): Live Pressure Overs and its first-half arms,
Favourite Pressing HT, Low Pressure HT Under, Favourite Swing, Pre-Match Drift, NFL /
NBA / Nations League Every Game, Close Forecast, the strategy factory's paper bots; and
the observers and recorders (settled-market sweep, late goals, soccer and NFL live).
They are switched on the server, show on the operator's My agents and nowhere else, and
never go on the Marketplace. Their record and the findings behind them are in
`docs/research_log.md`. They are also where the agents everyone else can build come
from: a finding only reaches users when it becomes something Create can express.

The nine agents with `source = 'lab'` on the operator's account came from
`close_history_scan.py`, not from Create, so they have no stored backtest: their history
is in their words. They are the Marketplace's first listings, published on 2026-10-30
at the operator's request, under the name NOPREDICTIONS.

---

## How we think about markets (rewritten 2026-09-29, by the operator's direction)

**Sports markets are beatable, and money is made in them every day.** Bettors and
syndicates live off beating Pinnacle; Polymarket, thinner and more retail, is
easier still. Our own data already shows it: wallet GSX- made +$85k in 105 days
on a book under $29k (`reports/wallet_gsx_2026-09-02.md`); resting at the bid on
NFL lines Pinnacle prices exactly measured +0.60% CI[+0.45,+0.75]; the same
football outcome is routinely cheaper on one venue than the other (median 1.3pp
net, max 5.4pp on 2026-09-20).

So the default stance changes:

1. **Pinnacle is a benchmark, not an oracle.** Its closing line is sharp and
   still has measurable biases (favourite–longshot, lower leagues, early
   season, internationals, public-heavy names). Measure it against results too;
   its blind spots are where syndicates earn.
2. **A failed test says "this method did not find it" — never "the market is
   efficient".** Write conclusions about the method, the sample and the
   population tested, and name the next test. A negative on clubs says nothing
   about national teams; a negative against Pinnacle's close says nothing about
   Polymarket's microstructure.
3. **Start from who pays and why** (the NFL profit map is the template): slow
   price updates after news and line-ups, niche leagues nobody prices well,
   public money on big names, settlement windows, cross-venue gaps, being the
   maker instead of the taker, platform rewards, rule quirks.
4. **Evidence that someone is making money is the strongest lead there is.**
   Winning wallets are studied before models are built: what they buy, when,
   at what price, maker or taker, and which leg carries the profit.
5. **Rigor stays; it points at finding edges, not at dismissing them.** Out of
   sample, no lookahead, sample sized to the effect — and when the sample is
   too small, say "not enough data yet", collect more, and keep the idea alive.
6. **When the data we need does not exist, say so and propose getting it**
   (a new source, a paid feed, a recorder) instead of answering a different,
   easier question.

## CLV framework (how we measure edge)

We have both Pinnacle opening and closing odds, so CLV is measurable now:

- **Entry price** = Pinnacle opening odds (PSH/PSD/PSA, stored as "Pinnacle (legacy)")
- **Benchmark** = Pinnacle closing odds (PSCH/PSCD/PSCA, stored as "Pinnacle (closing)")
- **CLV** = (entry odds / closing odds) − 1
- Consistently positive CLV on a defined set of events = real edge
- Positive ROI with negative CLV = luck. Always measure both.


## Key metrics

| Metric | Why it matters |
|---|---|
| **CLV (Closing Line Value)** | Strong evidence of a pricing edge. Not the only kind: maker, settlement-window and cross-venue edges do not show up as CLV against Pinnacle. |
| **Pinnacle closing as benchmark** | Sharpest closing line in our dataset — the strongest benchmark, with its own measurable biases. |
| **Betfair Exchange closing as benchmark** | Cross-check against the second-sharpest market (~13k matches). |
| **Polymarket spread vs Betfair/Pinnacle implied** | Direct edge signal — where Polymarket diverges from sharp consensus. |
| **Yield %** | Profit / total staked. More stable than ROI on small samples. |
| **p-value vs ROI=0** | Require p < 0.05 before promoting. |
| **Sample size** | Sized to the effect and the question (see rule 4), not one number for everything. |


## Methodological rules (non-negotiable)

1. **No lookahead bias.** Every feature used must have been available before kickoff.
2. **Train/test split.** Walk-forward validation. Never evaluate on discovery data.
3. **Reject silent p-hacking.** Every hypothesis tested goes into `research_hypotheses`.
4. **Size the sample to the question.** Yield at ~2.0 odds needs thousands of bets
   to see a 2% edge; price-vs-fair and calibration arms need hundreds of
   observations; CLV needs tens. Below that, the answer is "not enough data
   yet", never "no edge".
5. **Measure CLV and ROI both.** Positive ROI with negative CLV against the close is
   probably luck; but an edge that does not trade on price (maker, settlement,
   cross-venue) is measured by its own mechanism, not by CLV.
6. **The agent must be creative.** Look where others do not, and follow the money.
7. **Negative results are about the method, never the market.** No conclusion may
   read "the market is efficient"; it says what was tested, on what, and what
   to test next.
8. **Missing data is a task, not an answer.** If the question needs data we do
   not hold, propose the source before answering anything else.

---

## Tech stack

- **Site:** Next.js 14, React 18, Tailwind + `globals.css`, Vercel
- **Auth + database:** Supabase (Postgres), Stripe for Pro
- **AI:** Anthropic SDK, the understanding and strategy-writing steps only. No LLM in the
  trading loop: an agent's decisions are its rule.
- **Agents:** Python 3.9 on the server (⚠️ no same-quote nested f-strings), psycopg2

## Sources

- Polymarket Gamma + CLOB: https://docs.polymarket.com/ (⚠️ read the Gamma traps in `docs/research_log.md`)
- ESPN public site API (no key, no custom User-Agent)
- Football-Data.co.uk: https://www.football-data.co.uk/
- Supabase: https://supabase.com/docs · Anthropic: https://docs.anthropic.com
