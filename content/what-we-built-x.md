# X versions — "what we built"

---

## Option A — Thread (13 posts, all ≤280 chars)

**1/**
Everyone with an API key is building a tipster bot right now.

Point an LLM at a match, ask who wins, post it with a confident emoji.

Easiest thing in the world to build. Least valuable thing to own.

We built the opposite. 🧵

**2/**
NOPREDICTIONS is an AI agent that hunts mispricings in football prediction markets.

It has no opinion on who wins.

It has an opinion on whether the *price* is wrong — and only takes it when it can show its work against the sharpest markets on earth.

**3/**
The bet is on two waves hitting at once:

① AI got genuinely good at research. Not "write a blog post" good — *sit with a dataset for six hours and not get bored* good.

② Prediction markets had their moment. Huge attention, thin systematic sharp money.

We sit in the middle.

**4/**
Uncomfortable truth about betting models:

it is trivially easy to build one that looks profitable and isn't.

Backtest on the data you found the pattern in. Slice until a subgroup shines. Measure 40 bets. Call it edge.

So the first thing we built wasn't a model.

**5/**
It was 5 rules to stop us lying to ourselves:

• No lookahead. Ever.
• Walk-forward validation
• Every hypothesis pre-registered — including the dead ones
• Min 200 selections before the word "edge"
• CLV is king

Profit with negative CLV is luck wearing a suit.

**6/**
Most systems compare their model to a bookmaker and declare victory on disagreement.

Backwards. Disagreeing with a soft book means nothing. Disagreeing with a sharp one usually means *you're* wrong.

**7/**
So: three bodies.

Betfair + Pinnacle = the truth oracle. What a price *should* be.
Our own models = an independent line, built before we look at anyone else's.
Polymarket = where we trade.

Edge exists only where our line AND the sharps agree, and PM doesn't.

Two witnesses.

**8/**
Under the hood — ~28k lines of Python, 28 migrations, ~147,000 matches:

124k matches / 22 leagues / 2010→today
Pinnacle opening AND closing on 95k each
Betfair Exchange closing
16k matches with xG
8,394 internationals
10,006 NBA games

**9/**
The models:

• Dixon-Coles on 133k matches, 1,093 teams — with a retrain pipeline that REFUSES to deploy a non-converged fit
• A vectorised Monte Carlo simulator: 50,000 sims in <600ms, pricing 15 market types off one engine
• Elo+Poisson as a deliberately dumber cross-check

**10/**
Where most projects quietly cheat: execution.

Paper trading at mid is fantasy. Real markets charge you to enter AND to leave.

We execute at the ask, re-check edge at the actually-executable price, and walk away if it's gone.

**11/**
Then we measured the thing everyone hand-waves:

round-trip cost on a pre-match PM football position is 1.2–2.5 percentage points.

That number is a wall.

It killed an entire class of strategies that looked profitable right up until we made them pay to trade.

**12/**
What actually makes this different:

• Own line first — no laundering the market's opinion back to ourselves
• Zero LLM in the trading loop. AI does the research; the decisions are pure auditable math
• The failures are the product
• Every position timestamped BEFORE it resolves

**13/**
The question was never "can an AI pick winners."

It's whether the scientific method — run by something that never gets bored or falls for its own ideas — finds real edge in the market everyone's watching.

Maybe not. We publish that too.

No predictions. Just edges.
nopredictions.com

---

## Option B — Single long post (Premium)

Everyone with an API key is building a tipster bot. Point an LLM at a match, ask who wins, post it with a confident emoji. Easiest thing to build, least valuable thing to own.

We built the opposite.

NOPREDICTIONS is an AI agent that hunts mispricings in football prediction markets. It has no opinion on who wins — only on whether the price is wrong.

The hard part was never the model. It's trivially easy to build a betting model that looks profitable and isn't: backtest on the data you found the pattern in, slice until a subgroup shines, measure 40 bets, call it edge.

So the first thing we built was a set of rules to stop us lying to ourselves. No lookahead. Walk-forward validation. Every hypothesis pre-registered, including the dead ones. Minimum 200 selections before anyone says "edge." And CLV is king — profit with negative closing-line value is luck wearing a suit.

Then the machinery. ~28k lines of Python over 147,000 matches. Dixon-Coles trained on 133k games and 1,093 teams. A vectorised Monte Carlo simulator running 50,000 match simulations in under 600ms, pricing 15 market types off one engine. Pinnacle opening AND closing odds on 95k matches, because that's what makes CLV measurable at all.

Betfair and Pinnacle are our truth oracle — what a price should be. Our models build an independent line first, before looking at anyone else's. Polymarket is the venue. Edge only exists where our line and the sharps agree and PM doesn't. Two independent witnesses before we spend a dollar.

And we execute at the ask, not the mid — because we measured the round-trip cost of a pre-match PM football position at 1.2–2.5pp, and that wall killed a whole class of strategies that looked great until we made them pay to trade.

No LLM in the trading loop. AI does the research; the decisions are pure auditable math.

Every position timestamped before it resolves. The failures get published in the same detail as the wins.

The question was never "can an AI pick winners." It's whether the scientific method, run by something that doesn't get bored or fall in love with its own ideas, can find something real in the market everyone is watching.

No predictions. Just edges. → nopredictions.com

---

## Option C — Single standard post (≤280)

Everyone's building tipster bots.

We built an AI agent with no opinion on who wins — only on whether the price is wrong.

147k matches. Our own line before we look at the market. Zero LLM in the trading loop. Every position timestamped before it resolves.

No predictions. Just edges.
