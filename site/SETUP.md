# Alpha Football — Site Setup

## Stack
- **Next.js 14** (App Router)
- **Tailwind CSS**
- **Supabase JS** (real-time data)
- **TypeScript**

## Run locally

```bash
cd site
npm install
npm run dev
# → http://localhost:3000
```

## Deploy to Vercel

```bash
npm install -g vercel
vercel --prod
```

Vercel will auto-detect Next.js. Add these env vars in the Vercel dashboard:
```
NEXT_PUBLIC_SUPABASE_URL=https://pnpjyrvvbzsdrwtymyzz.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<your anon key>
```

## Supabase: enable public read access (RLS)

The site uses the **anon key** to read data. If RLS is enabled on your tables,
run these policies in the Supabase SQL editor:

```sql
-- Allow public reads on all site-facing tables
alter table strategies enable row level security;
create policy "Public read" on strategies for select using (true);

alter table research_hypotheses enable row level security;
create policy "Public read" on research_hypotheses for select using (true);

alter table matches enable row level security;
create policy "Public read" on matches for select using (true);

alter table leagues enable row level security;
create policy "Public read" on leagues for select using (true);

alter table match_odds enable row level security;
create policy "Public read" on match_odds for select using (true);
```

## Wire up the newsletter

The subscribe form currently just shows a confirmation message. To actually
capture emails, add a server action or API route and connect to:
- **Resend** (recommended — simple, free tier)
- **Mailchimp / ConvertKit** — if you want a full list manager

## What's live vs coming soon

| Section | Data | Status |
|---------|------|--------|
| Live Bets | `strategies` table | Empty — shows launch state |
| Leaderboard | `strategies` table | Empty — shows DB stats |
| Strategies | `research_hypotheses` | Empty — shows planned hypotheses |
| Newsletter | Static form | UI ready, needs email provider |
| About | Real DB stats + static copy | ✅ Live data |

Once the agent starts generating and promoting hypotheses, all sections
auto-populate — no code changes needed.
