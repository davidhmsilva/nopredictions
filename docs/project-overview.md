# Project overview

## What it is

**No Predictions** is a sports prediction-market research tool for US users of **Kalshi** and **Polymarket US**.

- **All sports:** NFL, NBA, MLB, NHL, college football (CFB), WNBA and soccer.
- **Research only:** no tips, no trades placed on the user's behalf. The user decides and places any trade themselves, on the app they choose.
- **18+.**

## Team

- Founder: **David Silva** — solo and part-time.

## Stack

- **Site:** Next.js 14 in `site/`, with Supabase auth, Stripe billing and Claude-based briefs.
- **Data / agents:** Python in `agent/` and `ingest/`.
- **Always-on processes:** run on a VPS (set up 27 Sep 2026).

## Product surfaces

| Surface | What it is |
|---|---|
| Board / home | Today's games across sports and both venues |
| Sport boards | Per-sport schedules (NFL, NBA, …) |
| Game page | Both prices side by side, fees included |
| Lab | Backtests |
| Agents | Paper-trading agents (no real trades) |
| Wallet | Wallet reader |
| Pricing | Free + Pro ($19/mo) |

See [decisions.md](decisions.md) for how these are prioritised and [positioning-and-copy.md](positioning-and-copy.md) for how they're presented.
