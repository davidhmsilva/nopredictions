-- 090 — Price Reader reads the market's behaviour, not only its price
--
-- The operator (2026-11-07): "reading the price means finding patterns and
-- behaviour in the market that may signal something — drifting odds, many
-- people backing the same thing and moving the odd". Six arms now read
-- Polymarket's public trade tape (who bought which side, how much, how many
-- wallets, the biggest bet) and the 24-hour drift. Each position keeps what
-- the market did when it was taken, so the review and the page can say why.
--
-- `excluded_sports` may now also hold a phase ("phase:live", "phase:pre"): the
-- review can take an arm's in-play or pre-match bets off the tab the same way
-- it takes a sport off.

begin;
set local lock_timeout = '5s';

alter table public.price_reader_positions
  add column if not exists signal jsonb;

comment on column public.price_reader_positions.signal is
  'What the market did when the position was taken: flow for and against in USD, wallets on each side, the biggest bet and its price, fills, the price move (pp). NULL for the level arms.';

update public.strategies
   set theory = 'A sports market shows what people are doing before the result does: money piling onto one side, a crowd of different wallets backing the same thing, one big bet, odds drifting, or a move nobody paid for. Which of those signals pays, if any, is what this agent is finding out.',
       interpretation = 'Watches every game market of every sport on Polymarket, and every bet placed on them from the public trade tape: how much money goes on each side, how many different people, the biggest bets, how the odds drift over the hour and the day. Never the score. Thirteen ways of reading that run side by side; every morning it reviews its own bets, stops what loses and promotes what wins on evidence. 1u at the Polymarket ask plus the fee, paper.'
 where name = 'Price Reader — every sport, price only';

update public.price_reader_arms set label = 'Odds shortening in the last hour' where key = 'pre_steam';

commit;
