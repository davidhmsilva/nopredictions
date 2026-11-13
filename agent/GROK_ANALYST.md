# Grok Analyst — setup

Autonomous paper-trading agent for the Grok vs Claude competition.
Scans Polymarket sports + esports, asks Grok what to buy and at what stake,
enforces book quality (depth must cover the stake, spread ≤ 4pp), and writes
paper trades under a strategy that shows in the operator's My Agents.

## Requirements

- `XAI_API_KEY` in the environment (VPS / `.env`)
- `DATABASE_URL` (transaction pooler port 6543 is fine)
- The strategy row below must exist and be owned by the operator account
  (so it appears under /agent)

## One-time SQL (run once on the DB)

```sql
-- Replace <OWNER_PROFILE_ID> with the operator's profiles.id
-- (the account with role = 'owner')

INSERT INTO strategies (name, source, status, owner_id, notes)
VALUES (
  'Grok Analyst — all sports / esports',
  'agent',
  'paper',
  <OWNER_PROFILE_ID>,
  'Autonomous Grok agent for the Grok vs Claude competition. Variable stake, book-quality gated. Measure by 2-week paper P&L.'
)
ON CONFLICT (name) DO NOTHING;
```

If your `strategies` table uses different column names (hypothesis_id required,
etc.), adapt or insert via the same path the other hand-written agents use.

## Run

```bash
cd agent && source ../ingest/.venv/bin/activate
python grok_analyst_agent.py --once --dry-run   # see what Grok would do
python grok_analyst_agent.py --once             # one cycle, write paper trades
python grok_analyst_agent.py                    # daemon loop (every 5 min)
```

## What it does

- Horizon: events starting in the next 3 h or live in the last 2 h
- Asks Grok (model `grok-3`) for 0–5 trades with stake_usd + reasoning
- Only papers a trade if `can_fill()` passes (spread ≤ 4pp, depth ≥ 1.5× stake, stake between $5 and $5k)
- Writes to `paper_trades` so it appears on the site under the operator's agents
- Includes recent 7-day P&L in the prompt so Grok can learn from its own record

## Competition

Compare 2-week paper P&L of this agent vs Claude-generated Lab agents (or any other baseline you choose).
