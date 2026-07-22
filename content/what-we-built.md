# We Built a Machine That Refuses to Predict

Everyone with a laptop and an API key is building a tipster bot right now. Point an
LLM at a football match, ask it who wins, post the answer with a confident emoji.
It's the easiest thing in the world to build and the least valuable thing you can own.

We built the opposite.

NOPREDICTIONS is an AI trading agent that hunts for **mispricings** in football
prediction markets. It doesn't have opinions about who will win. It has an opinion
about whether the *price* is wrong — and it only takes that opinion when it can
show its work against the sharpest markets on earth.

The name is a promise and a constraint. No predictions. Just edges.

---

## Two waves, one intersection

Two things happened at once.

**AI got genuinely good at research.** Not "write me a blog post" good — *sit with a
dataset for six hours and not get bored* good. The kind of patient, unglamorous,
hypothesis-testing labour that quant desks pay analysts six figures to do.

**Prediction markets had their cultural moment.** Polymarket went from crypto
curiosity to the thing everyone screenshots. Enormous attention, growing volume,
and — critically — comparatively thin systematic sharp money. Lots of people with
views. Not many people with models.

Sit at the intersection of those two waves and you get a very specific bet: an AI
agent that does real quantitative work on the market everyone is watching but
comparatively few are pricing properly.

That's the whole thesis. Everything below is machinery in service of it.

---

## The architecture of not fooling yourself

Here's the uncomfortable truth about betting models: **it is trivially easy to build
one that looks profitable and isn't.** Backtest on data you discovered the pattern
in. Slice until a subgroup shines. Measure returns over 40 bets and call it edge.
The literature is a graveyard of strategies that worked until they met a real market.

So the first thing we built wasn't a model. It was a set of rules designed to stop
us from lying to ourselves:

1. **No lookahead.** Every feature must have existed before kickoff. No exceptions,
   no "well, the injury news was basically out."
2. **Walk-forward validation.** Train on the past, test on the future, roll forward.
   Never evaluate on the data that generated the idea.
3. **Every hypothesis is pre-registered**, including the ones that die. The failures
   go into the database with the successes.
4. **Minimum 200 selections** before anyone says the word "edge."
5. **CLV is king.** Closing Line Value — did we beat the price the sharpest market
   settled on? Positive returns with negative CLV is luck wearing a suit. We measure
   both, always, and we believe the CLV.

Those five rules are why this project is slower than a tipster bot and why it's
worth more.

---

## The truth oracle

Most betting systems compare their model to a bookmaker and declare victory when
they disagree. That's backwards — disagreeing with a soft book means nothing, and
disagreeing with a sharp one usually means *you're* wrong.

We use a three-body system:

- **Betfair Exchange and Pinnacle** are the **truth oracle**. Betfair is the most
  efficient sports market in the world — no bookmaker margin, prices set by sharp
  money competing against sharp money. Pinnacle's closing line is the industry's
  gold standard for fair value. These tell us what a price *should* be.
- **Our own models** produce an independent fair value from first principles. We
  build our own line before we look at anyone else's, so we don't just launder the
  market's opinion back to ourselves.
- **Polymarket** is where we trade — the venue whose prices we think diverge.

Edge only exists where our line and the sharp consensus agree *and* Polymarket
disagrees with both. Two independent witnesses before we spend a dollar. That single
design decision has killed more of our ideas than any backtest.

---

## What's actually under the hood

Roughly **28,000 lines of Python**, **28 database migrations**, a **Next.js dashboard**,
and a Postgres instance holding about **147,000 matches**. Piece by piece:

### The data spine

Seven ingestion pipelines feeding one canonical schema:

- **~124,000 matches** across 22 European leagues, 2010 to today — results, half-time
  scores, match stats, and closing odds from nine bookmakers.
- **Pinnacle opening *and* closing odds** on ~95k matches each. This is the part
  nobody bothers with, and it's the part that makes CLV measurable at all.
- **Betfair Exchange closing** prices as a second sharp cross-check.
- **16,000+ matches with expected goals**, scraped from FBref and Understat.
- **ClubElo** rating histories for every club.
- **8,394 international matches** — World Cup, Euros, Nations League, qualifiers —
  so all 48 World Cup 2026 nations are priceable.
- **10,006 NBA games** with closing lines, because a second sport is the cheapest
  test of whether a method generalises or was just fitted to football.

Team identity is the unsexy hard problem here: Football-Data, FBref, Betfair and
Polymarket all spell the same club differently. There's an alias table doing quiet,
thankless work under everything.

### The models

- **Dixon-Coles**, trained on 133,000 matches across 1,093 teams. Attack and defence
  strengths, home advantage, the low-score correlation correction. Retrained through
  a pipeline that *refuses to deploy* if the optimiser didn't converge or the strength
  spread collapses — a lesson learned the expensive way.
- **A vectorised Monte Carlo match simulator.** Minute-by-minute state, scoreline-dependent
  rate adjustments, 50,000 simulations in under 600 milliseconds. It prices fifteen
  market types off one engine: full-time result, half-time result, totals at every
  line, both-teams-to-score, Asian handicaps, winning margins. It's continuously
  calibrated against analytical Poisson so we know when it drifts.
- **Elo + Poisson** as an independent, deliberately dumber cross-check.
- **An NBA Elo model** running the same discipline on a different sport.

### The trading layer

Seven strategies, each a separate hypothesis with its own P&L:

pre-match sharp-consensus, in-play Poisson, Elo pre-match, Dixon-Coles pre-match,
NBA Elo, simulation pre-match, simulation in-play — plus a dedicated World Cup 2026
sub-agent with its own structural priors, and an **in-play convergence trader** that
runs 24/7 hunting the moments when live prediction-market prices detach from reality.

Between the models and the money sits an **edge engine** that applies the sharp
anchor, a haircut for model uncertainty, and hard caps on model-only conviction.
It exists to say no. It says no a lot.

### Execution reality

This is where most projects quietly cheat. Paper trading at the mid-price is a
fantasy; real markets charge you to enter and to leave.

So we execute **at the ask**, re-check the edge at the actually-executable price,
and abandon the trade if it's gone. There's a slippage cap, a minimum executable
edge, and stale-feed guards that freeze entries when the score feed and the price
feed disagree. We built a **tick recorder** that snapshots top-of-book every 60
seconds because we learned to detect goals from price jumps when the score API's
quota ran dry.

And we measured the thing everyone hand-waves: the round-trip cost of a
pre-match Polymarket football position is **1.2 to 2.5 percentage points**. That
number is a wall. It killed a whole class of strategies that looked profitable
right up until we made them pay to trade.

### The honesty instruments

The tooling we're proudest of is the tooling that tells us bad news:

- An **evaluation harness** computing yield with confidence intervals, Brier scores,
  calibration curves, and real Pinnacle CLV per strategy. Its first act was to reveal
  that a column we'd been trusting was 68% circular garbage.
- An **observation layer** that logs model-vs-market divergence continuously *without
  trading* — so hypotheses can accumulate evidence before touching money.
- A **Hypothesis Tester** where a question in plain English becomes a no-lookahead
  walk-forward backtest against 101,000 matches of Pinnacle closing lines.
- Automated **calibration and drift monitoring**, because a model that was right in
  March is not evidence of a model that's right in July.

We also built an isotonic calibration layer, discovered it failed out-of-sample,
and shipped it switched off with a note explaining why. That file is in the repo.
It's not a failure — it's the receipt.

---

## What actually makes this different

**We build our own line first.** The model doesn't peek at the market before forming
a view. If we can't derive fair value independently, we have nothing to say.

**There is no LLM in the trading loop.** AI does the research, writes the code,
generates and interrogates hypotheses. The decisions are pure math, fully auditable,
and reproducible. Nobody has to trust a vibe.

**The failures are the product.** Anyone can publish winners. We publish the capstone
negatives — the strategies that died, the biases that turned out to be artifacts, the
edges that evaporated at 2 percentage points of cost. That's the part that's actually
rare, and it's the part a serious reader can learn from.

**Everything is timestamped before it resolves.** Every position, every hypothesis,
every reasoning chain goes public *before* the event settles. No retroactive genius.
No quietly deleted losers.

**Real money, deliberately tiny.** Live positions are capped at micro size until CLV
proves an edge over a real sample. The constraint isn't caution for its own sake —
it's that paper trading lets you believe things that executing quickly disproves.

---

## The experiment

Fifteen years of human market experience, encoded into an agent, pointed at the most
watched prediction market in the world — and then run in public with the receipts
visible.

The interesting question was never "can an AI pick winners." It's **whether the
scientific method, applied relentlessly and honestly by a machine that doesn't get
bored or fall in love with its own ideas, can find something real in a market that
everyone is looking at but few are properly pricing.**

We might find that answer is no. If so, that will be documented too, in the same
detail, with the same timestamps.

That's the entire point.

**No predictions. Just edges.**

— [nopredictions.com](https://nopredictions.com)
