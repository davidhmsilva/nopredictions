# Pre-registration: NFL primetime moneyline underdogs

**Registered:** 2026-10-08, before inspecting outcomes in the local NFL cache.

## Claim and mechanism

Nationally televised NFL games attract more casual/public attention and brand-driven support for recognizable favourites. If that flow overprices favourites, the opposing moneyline underdog may be cheap enough to beat its quoted probability after transaction costs.

## Exact rule

- Universe: all settled NFL regular-season and playoff games in the local historical NFL archive with a closing consensus moneyline and known primetime flag.
- Entry: buy the moneyline outcome marked `is_fav = 0` when `primetime = 1`; one bet per game. No team-name filters, odds-band filters, exclusions or exit optimization.
- Entry price: the archive's de-vigged consensus close plus the registered 0.005 half-spread proxy. Add the Lab's 5% Polymarket taker-fee formula, `ask * (1 + 0.05 * (1 - ask))`.
- Settlement: final game result. Pushes are excluded only if the source marks them as such.
- Split: kick-off date before 2021-06-01 is discovery; 2021-06-01 onward is the untouched test period. No parameter selection after seeing test outcomes.

## Success threshold

Call it a promising lead only if the held-out period has at least 200 games, net ROI per dollar staked is positive with a game-level bootstrap 95% CI entirely above zero, the edge is positive in at least three separate held-out seasons, and it remains positive after an extra 2 percentage points of entry cost. Otherwise report a negative result or insufficient sample, not market efficiency.

## Controls and caveats

- Report held-out all-game underdogs as a descriptive control, not a second hypothesis.
- Report the result by held-out season to show concentration; do not promote a favorable season slice.
- Historical prices are a sportsbook consensus close, not actual Polymarket or Kalshi fills. The half-spread and taker-fee adjustment is a conservative PM-style proxy, but does not reproduce either venue's historical order book.
- No conclusions about NBA, MLB, NHL, WNBA, college football or soccer follow from this test.
