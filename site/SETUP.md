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

---

## Accounts and paid plans (added 2026-09-08)

The code is complete and deployed. Three things need a console you have to be
logged into, and the site behaves correctly in the meantime: without Stripe
keys the pricing page's upgrade button returns "Paid plans are not switched on
yet" rather than erroring, and without the Supabase settings below sign-up
creates an account that waits on a confirmation email that may not arrive.

### 1 · Supabase — make sign-up work (5 min, required)

Dashboard → Authentication → Providers / Emails.

- **Email confirmation.** Supabase's built-in mailer is rate-limited to a
  couple of messages an hour and is documented as test-only. So either:
  - **turn OFF "Confirm email"** (Authentication → Providers → Email) and
    password sign-up works immediately, or
  - **set up SMTP** (Authentication → Emails → SMTP Settings) with Resend,
    Postmark or similar, and leave confirmation on.
- **Redirect URLs.** Authentication → URL Configuration → Redirect URLs, add:
  - `https://www.nopredictions.com/auth/callback`
  - `http://localhost:3000/auth/callback`

### 2 · Google sign-in (optional)

Supabase → Authentication → Providers → Google, with a Google Cloud OAuth
client. Then set `NEXT_PUBLIC_AUTH_GOOGLE=1` on Vercel. The button is hidden
until that variable is set, because a button that opens a Supabase error page
is worse than no button. Same for magic links: `NEXT_PUBLIC_AUTH_MAGIC_LINK=1`,
and only once SMTP actually delivers.

### 3 · Stripe — turn on the paid plan

1. Create the account, and stay in **Test mode** until a test purchase works
   end to end.
2. Product "NOPREDICTIONS Pro" with two recurring prices: **$19/month** and
   **$190/year**. Copy both price ids (`price_…`).
3. `vercel env add` each of:

   ```
   STRIPE_SECRET_KEY       sk_test_…   (then sk_live_… when you go live)
   STRIPE_PRICE_MONTHLY    price_…
   STRIPE_PRICE_YEARLY     price_…
   STRIPE_WEBHOOK_SECRET   whsec_…     (from step 4)
   ```

4. Stripe → Developers → Webhooks → add endpoint
   `https://www.nopredictions.com/api/stripe/webhook`, subscribed to:
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`.
   Copy the signing secret into `STRIPE_WEBHOOK_SECRET`.
5. Redeploy, then buy Pro with card `4242 4242 4242 4242`. `/account` should
   read PRO and the Lab's counter should disappear.

⚠️ **The webhook is the only thing that grants Pro.** `profiles` has no UPDATE
policy for the client (db/044), so a browser cannot set its own plan. If the
webhook is not configured, a successful payment changes nothing on the site.

### What is deliberately NOT built

**Alerts.** `public.alerts` exists in db/044 and nothing sends anything. The
pricing page names them as what is next rather than listing them as bought.
Wiring them needs an email provider and a sender — do not list them as a Pro
feature before both exist.
