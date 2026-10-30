-- 081_look_observations.sql
--
-- The forward record of the Game Center's CHEAP cards ("Worth a look").
--
-- A card says a Polymarket price sits below a measured rate by more than the
-- taker fee. Whether that is money is the question this table answers: every
-- CHEAP card the page would show is written once, at the price it showed, and
-- settled on Polymarket's own resolution of the token.
--
-- One row per (fixture, card, score): the FIRST minute a card reads CHEAP in a
-- given score state. The same card later in the same state is the same bet at
-- a different price; recording it again would weight long, quiet states by
-- their length. A goal is a new state and gets its own row.
--
-- Written by site/scripts/record-looks.ts, which builds the page's payload
-- with the page's own code (app/api/game/route.ts), so the record and the
-- page cannot disagree about what was shown.
--
-- Research data: RLS on, no policies, no grants (see db/045, db/048).

create table if not exists look_observations (
    id              bigserial primary key,
    observed_at     timestamptz not null default now(),
    slug            text not null,
    event_title     text,
    competition     text,
    minute          int,
    goals           int,
    clock_source    text,
    look_id         text not null,
    side            text not null,
    market          text not null,
    market_slug     text,
    token_id        text not null,
    tier            text,
    ask             numeric not null,
    odds            numeric not null,
    fair_prob       numeric,
    fair_raw_prob   numeric,
    calibrated      boolean not null default false,
    imported        boolean not null default false,
    n               int,
    edge_pp         numeric,
    fee_pp          numeric,
    spread_pp       numeric,
    depth_usd       numeric,
    -- settlement: Polymarket's resolution of the token bought
    won             boolean,
    settled_at      timestamptz,
    settle_source   text,
    unique (slug, look_id, goals)
);

create index if not exists look_observations_unsettled
    on look_observations (observed_at) where settled_at is null;

alter table look_observations enable row level security;
revoke all on look_observations from anon, authenticated;
revoke all on sequence look_observations_id_seq from anon, authenticated;
