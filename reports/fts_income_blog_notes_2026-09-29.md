# FTS Income blog: notes on systems and AI betting (2026-09-29)

Source: https://ftsincome.co.uk/blog/, by Ian Erskine. It is a UK Betfair trading and education business that sells data sheets (FTS Ultimate, FTS Data Advanced) and courses.
The blog has 1,766 posts. The WordPress API (`/wp-json/wp/v2/posts`) returns all of them without a login. Most are podcast stubs, "Unfiltered Friday" episodes, or 2024 SEO pieces with nothing to take away.

**Read in full:** sample size (09-29) · starting from scratch (09-22) · AI in betting (07-18) · why systems stop working (06-24) · record keeping (06-16) · copying bets (06-11) · drawdowns for investors (06-03) · CLV in European football (01-13) · kill/pause/refine (L45) · diversification (L44) · odds brackets (L43) · Over 2.5 trading (L39) · strategies by season (L30) · filtering data (L23) · fading edge (L21) · historical data mistakes · Betfair historical odds.

**Not readable as text:** the AI article is also a podcast (`/ai-in-sports-betting/`); the readable version is at `/ai-in_sports_betting/`. "Building a System" (L19) and "FTS Advanced Building A Betting System" are video only.

---

## 1. What is worth taking (ranked by relevance to us)

### 1.1 Sample size, read in both directions (09-29): the best article on the site
- They use `n ≈ 2.7 × (odds − 1) / ROI²`. Here 2.7 ≈ 1.645², a one-sided test at 5%, and variance per unit ≈ (odds − 1) at fair odds. It is the same `n = (zσ/δ)²` as [[feedback-edge-is-not-only-clv]]. Their examples: 5% at 2.0 needs 1,082 bets, at 3.0 needs 2,165, at 5.0 needs 4,330; 10% at 2.0 needs 271.
- **Read backwards:** the smallest ROI a sample can detect. At odds 3.0 that is 33% at n=50, 23% at 100, 16% at 200, 10% at 500 and 7.4% at 1,000. A spectacular 100-bet record is a reason for more suspicion, because only a ≥23% edge could show at that n.
- **"Shape before profit."** Profit needs thousands of bets. Whether live behaviour matches the backtest shows in dozens: strike rate, **average odds obtained** and **bet volume**. For example, the strike rate matches but the average odds are 2.6 against a modelled 3.1. That is a price problem, not variance.
- **Two numbers and a rule, written before the first bet:** a review point (in bets), an abandon trigger (a drawdown), and **no change between the two**. A test with a filter added in month 4 and a staking change in month 9 is several short tests, not eighteen months of evidence.
- **Paper cannot test fills.** Forward-test with tiny real stakes so you have to get matched.
- **Run genuinely different systems concurrently** rather than one after another: same calendar time, more information. Three variants of one idea are not three tests.
- A 35% strike rate over 500 bets should *expect* a losing run of about 12.

**→ For us:**
- **Product:** the Lab should print the reverse number beside every result: "at n=X and these odds, the smallest edge this sample can detect is Y%". This one line would have stopped "Draws underpriced in Serie B, EDGE FOUND +4.66%". The same line belongs on `/agent` for every paper arm.
- **Honest self-critique:** s17 is at obs_version 7 after six weeks, s16 at 6 and s18 at 4. Each bump resets the sample, which is exactly the "several interrupted tests" pattern. Each bump was justified (a bug or a gate), but the verdict gate (n≥200 per version) moves further away every time. It is worth saying so in the agent's public story and counting how many versions actually reached n=50.
- **The "shape" check is directly buildable** for `lab_strategy_runner` and the factory paper bots. Compare live strike rate, average price paid and bets per week against the backtest spec. Stage J now gives `p_exec` vs `p_mid`, so the price half of this can be measured.

### 1.2 AI in sports betting (07-18): their audience wants it, and they are sceptical
- In their audience survey, **34% asked for AI training**, the most requested topic by far. They now sell AI courses in which members learn Python and HTML.
- **Where AI helps:** interrogating your own bet record (odds bands, markets, days); building a review framework; checking a line of reasoning for gaps.
- **Where it is oversold:** "feed it history and it finds edges" (it finds patterns in your sample, not a statistical test); AI-generated previews with no verified record; AI as a shortcut past the data, validation and variance work.
- **Where it causes harm:** fluent, confident answers to questions that need statistics (it does not know the pattern has p=0.35); **it speeds up system-hopping** because a new system with its rationale now costs minutes; it produces the format of rigour without the substance.
- Their line: a significance test *tells you* the sample is too small; an LLM just answers.

**→ For us: this is the positioning.** Their critique describes an LLM used as an oracle. NOPREDICTIONS is the counter-case: the AI builds the harness that says no. The factory ran 12,796 specs, 0 passed, under out-of-sample testing plus Benjamini-Hochberg false-discovery control. A content piece could be titled "The AI that says no 12,796 times". Their system-hopping warning also applies to us. The factory makes new specs nearly free, which is exactly why BH-FDR and forward-only promotion are necessary. That is worth saying publicly.

### 1.3 CLV in European football (01-13): nuance we already hold, useful for the product
- CLV comes from NFL/NBA spreads: two-way markets, key numbers, week-long windows. In 1X2 (three-way, no push, the draw distorting prices) a move carries less information.
- **Positive CLV in a thin market may just mean the market never corrected.** Football team news lands about an hour before kick-off, so late moves are information shocks, not refinement.
- CLV is most meaningful in **Asian handicap, two-way, liquid, big leagues**.
- Contrarian and model edges can carry negative CLV and still be real.

**→ For us:** consistent with [[feedback-edge-is-not-only-clv]]. The thin-market point matters on Polymarket: the PM close is not Pinnacle's, and on small books "beating the PM close" is weak evidence. It supports our choice of Pinnacle, not PM, as the CLV benchmark. It also argues for AH/O-U 2.5 over 1X2 as the CLV-heavy markets in the Lab.

### 1.4 Edge decay: variance or dead? (06-24, 06-03, L21, L45)
- Three decay mechanisms with different responses: **market efficiency** (mostly permanent), **model adaptation** by books reacting to action (partly reversible), **seasonal or conditional** (temporary).
- **"Results follow conditions."** Check whether the inefficiency's *conditions* still exist, independent of P&L.
- **A rolling-window vs long-run comparison** is a leading indicator, the cumulative record a lagging one.
- **Decay signals that are faster than P&L:** the price you get shortening (2.10 → 1.85: "the market has caught on"), and prices tightening minutes before kick-off, which means moving the entry point.
- Drawdowns article: pre-defined drawdown thresholds; half or quarter Kelly (Thorp); **ruin probability by Monte Carlo** (for example a 3% ROI at 2.0 on a 30-point bank over 500 bets is "material"); **weekly P&L correlation between systems**.
- Diversification (L44): target correlation **< 0.4**, and watch it in volatile periods. The same idea in different leagues is false diversification, and so is weekend-only exposure (a time correlation).

**→ For us:**
- `reports/factory_decay_*.csv` exists, so the rolling-window framing fits it. The "price obtained drifting" signal is measurable per bot.
- **Correlation is a gap.** s16, s17 and s18 share one pressure signal, one poll and mostly the same fixtures, and the 12 factory explore bots are 10 variants of "next goal". BH-FDR controls false discovery, not correlated exposure. If real money ever returns, bank sizing must use the correlated risk. Weekly P&L correlation across the running arms is a cheap query.
- **Product:** "ruin probability and expected worst losing run for this spec" is a natural Lab or agent card. FTS ships these as free interactive calculators, which are their lead magnets.

### 1.5 Record keeping and copying (06-16, 06-11, 09-22)
- Record the **price available when you looked, the price you got, the time placed relative to the event, and the reason, written before the result**. The result is the least interesting column.
- Records predictably go missing at the start, during drawdowns, and after every strategy change.
- **Copying decays by construction:** followers move the price, so later copiers get a worse bet. A screenshot shows neither the original price nor the timing.

**→ For us:** our observation tables already record seen vs paid prices and the gate state before the result, so no action is needed. The copying argument bears on the wallet work. "RN1 is the one to copy" ([[wallet-swisstony]]) has to survive being copied at +N seconds, the test we ran on Grand-Vista (+14% at +5m). It is also the honest caveat for any copy-trade feature on the site.

---

## 2. Concrete claims we could test in the Lab (bt_features, Pinnacle close)

These are **their claims, not findings**. Each is cheap to check and doubles as content ("we tested FTS's claim"):

1. **"Backing home teams at 1.30–1.50 blind for five years has delivered profit"** (L43). Test it at the Pinnacle close and at the exchange price (Stage J).
2. **The PL added-time overreaction** (L45): goals per game 2.9 → 3.27 in 2023/24 with O2.5 shortening to ~1.72, then 2.93 → ~2.6 with prices back toward 1.9. Hypothesis: the market lagged the rule change and then over-extrapolated. Testable as O2.5 yield by season vs Pinnacle.
3. **League draw rates "always correct"** (L45: Bundesliga 2 draws at 15% vs 23-28% historically). Taken literally this is the gambler's fallacy. The testable version: does a league-season's early draw rate predict the rest of the season beyond the closing price? Almost certainly not.
4. **Early-season inefficiency** (L30): "limited data = bookie errors" in August to mid-September. Test it as CLV and yield by month (their own `=TEXT(date,"MMMM")` trick).
5. **Model goals vs O2.5 implied** (L39): O2.5 at 1.60 implies ~3.2 goals, at 2.00 ~2.67; bet overs where the model exceeds the implied total. This is our DC-vs-Pinnacle question, already answered: no edge ([[finding-dc-no-edge-vs-pinnacle]]).

## 3. Their in-play trading playbook (for reference, not endorsed)
- **Over 2.5 in play (L39):** select on model xG ≥ 2.76 with O2.5 at 1.65-2.0 and "market support" (shortening). **Drip the stake in** at 2', 5-6' and 10-11' to improve the average price. At 0-0 at half time, cover with 1-1 (stake ≈ main / (odds − 1) + 25%), then cover 2-0 or 0-2 after a second-half goal. The alternative is to enter at 25-30' at 0-0, priced ~2.8+.
- **At 1-0 at 65' (L23):** back O2.5, trade out on a goal, otherwise back O1.5 at evens.
- Their liquidity floor is **£500 matched pre-kick-off**, far below anything our book-quality work would call clean ([[finding-pressure-book-quality]]).
- Drip staking is a maker/taker-timing idea; our late-goals and first-half work measured that later is not cheaper on PM (db/040).

## 4. What to be sceptical of
- The same writer publishes the careful statistics (09-29) and "it will correct, it always does" about league draw rates (L45). The trading lessons read as data-driven but offer **no sample sizes, CIs or out-of-sample tests**. "Adding form filters further enhances ROI" is filter-stacking on one history, which the filtering article itself warns against.
- The 2024 posts (historical data, Betfair odds) are generic SEO text with nothing specific in them.
- Their data products are the thing being sold, so every lesson ends with "use FTS Data Advanced".

## 5. Product and market notes
- The audience is UK exchange traders who care about process. **34% want AI**, and FTS now sells AI courses with Python and HTML modules. That demand is adjacent to what the Lab does in English.
- **Their lead magnets are interactive calculators:** the sample-size "Wait Calculator", a printable forward-test protocol, a drawdown and variance checker, a ruin Monte Carlo, a Kelly spreadsheet, record-keeping milestones. Every one of these could be a free page on nopredictions.com fed by *real* exchange data, which FTS does not have for Polymarket or Kalshi.
- Their honesty register ("certainty was never really on offer") is close to ours. They are a potential audience or partner, not only a competitor.

---

## 6. Video: "FTS Ultimate Walkthrough" (YouTube 3AUMrpY2S1Q, 18 min, from the auto transcript)

**What FTS Ultimate is:** an Excel database plus an online dashboard, running since 2012 (£ membership, season 26-27).
- **Coverage:** 11 leagues (BEL, NED, ENG PL + Championship, FRA 1, GER 1-2, ITA, POR, ESP, TUR). It keeps five seasons plus the current one, with sheets updated Tuesday (for midweek fixtures) and Friday (for the weekend).
- **Columns in each sheet:**
  - Betfair back odds at **08:00** with the book % (e.g. 105.7% means a gappy market);
  - the **5-min pre-KO** 1X2 price and the 08:00 → 5-min move (backed in or drifted);
  - O/U 1.5 / 2.5 / 3.5 at 5 min pre-KO;
  - the **HT correct-score lay price read at 55'**, a fixed point to measure against;
  - six-game home/away-specific form as a traffic light (green > 2 ppg, amber 1-2, red < 1);
  - two models: the old "FTS odds" (includes Ian's *subjective* preseason ratings) and a new one (xG and shot quality, no subjectivity, Elo with home advantage fitted per league), each with model-vs-Betfair value columns.
- **Dashboard:**
  - league metrics against the five-year average (e.g. Serie A 25-26 was 0.3 goals per game below it, 114 goals over the season; over 1.5 down about 7pp; the draw price fell from 4.12 to 3.85);
  - a fixture deep dive with form, H2H, Elo odds, model xG per side and fair odds for every market against Betfair at 08:00;
  - a staking calculator giving EV, a range over 100 bets, the worst losing streak and the maximum drawdown.
- **"AI selections"** (new for 26-27):
  - Described as a walk-forward, "out of sample" process: build on 21/22-22/23, test on 23/24, then roll forward.
  - **Claimed record:** about 1,200 bets a season, 5 of 5 seasons profitable, about +440 points at advised stakes, **yield 7.65%**, max drawdown about 40 points (60-65 at level stakes). Priced at Betfair 5 min pre-KO. The live run was −5 points at the time of the video. There is a public bet log.
  - They recommend never laying above 10.0 because doing so "makes about 20 points difference". That filter was chosen after seeing the results.

**What to be sceptical of:**
- **Commission is not mentioned.** Betfair takes 2-5% of net winnings. At 7.65% gross, commission eats a large part of it.
- **Chosen after seeing results:** the lay-cap filter, the advised stakes and the process itself were refined after the 25-26 AI picks.
- **An edge that size is a red flag.** The dashboard example calls model 2.22 against Betfair 3.35 "nice edge". A ~15pp gap against an exchange is, in our experience, model error ([[finding-dc-no-edge-vs-pinnacle]], [[finding-dc-away-longshots]]).
- **Gambler's fallacy on camera:** "if [the strike rate] was 20%… you're due a winning run" and "it can't possibly stay around that number".
- **Mostly positive:** they publish a forward bet log and a walk-forward design, and the forward log is the part worth watching.

**For us:**
- **Competitor overlap:** their product is close to our Lab plus the Game Center, but only in Excel, on Betfair, over 11 leagues and five seasons. We have 124k matches, 22 leagues since 2010, Pinnacle, and now Polymarket and Kalshi prices (Stage J).
- **Worth copying:**
  - league-vs-five-year-average panels (goals, strike rates *and* average market prices, which explain "same strike rate, less money");
  - the 08:00 → 5-min move column;
  - a staking and range calculator.
- **Their 7.65% claim is roughly testable** on Football-Data's Betfair Exchange closing columns (BFEC*, 2025-26 onward). That would require their selections, which sit behind the paywall.
