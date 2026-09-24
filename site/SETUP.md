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

Deploys come from Git: a push (or a merged PR) to `main` goes to production,
and any other branch gets a preview URL. The Vercel project's **Root Directory
must be `site`** (Settings → Build and Deployment); with the default `./` the
build runs at the repo root and fails.

For a manual deploy from this folder:

```bash
npm install -g vercel
vercel --prod
```

Add these env vars in the Vercel dashboard:
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

- **Email confirmation stays ON.** ⛔ Do not turn it off. A payment made
  before sign-up is held against the email (`pro_grants`, db/047), and
  `handle_new_user()` hands it to whoever creates an account with that
  address. With confirmation on, that is whoever can open the inbox. With it
  off, anyone who knows a paying customer's address can sign up first and
  take their Pro.
  Supabase's built-in mailer is rate-limited to a couple of messages an hour
  and is documented as test-only, so **set up SMTP** (Authentication → Emails →
  SMTP Settings) with Resend, Postmark or similar.
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

1. Create the account and work in a **Sandbox** until a test purchase works
   end to end.
2. Product "NOPREDICTIONS Pro" with two recurring prices in USD: **$19/month**
   and **$190/year**. Copy both price ids (`price_…`).
3. A **restricted key** (`rk_test_…`), not the secret key. It needs exactly:
   Customers **write**, Checkout Sessions **write**, Subscriptions **read**,
   Customer portal **write**. Anything else it answers 403, which is the point.
4. `vercel env add <NAME> production` for each, and mark the two secrets
   *Sensitive* in the Vercel dashboard:

   ```
   STRIPE_SECRET_KEY       rk_test_…   (then rk_live_… when you go live)
   STRIPE_PRICE_MONTHLY    price_…
   STRIPE_PRICE_YEARLY     price_…
   STRIPE_WEBHOOK_SECRET   whsec_…     (from step 5)
   ```

5. Stripe → Developers → Webhooks → add endpoint
   `https://www.nopredictions.com/api/stripe/webhook`. ⚠️ **With `www`**: the
   bare domain answers 307, and Stripe does not follow redirects. API version
   `2026-08-26.dahlia` (the SDK's pin). Subscribe to exactly the `HANDLED` set
   in `app/api/stripe/webhook/route.ts`:
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`,
   `customer.subscription.paused`, `customer.subscription.resumed`,
   `invoice.paid`, `invoice.payment_failed`.
   Copy the signing secret into `STRIPE_WEBHOOK_SECRET`.
6. Settings → Billing → **Customer portal**: save it once. `/api/stripe/portal`
   uses the default configuration, and until one is saved every "Manage
   billing" click errors.
7. Settings → Billing → **Invoices / Subscriptions and emails**: email
   finalized invoices and receipts, turn on Smart Retries and the
   failed-payment emails. That is the invoicing and dunning, with no code.
8. Redeploy, then buy Pro with card `4242 4242 4242 4242`.

**Test BOTH orders — they are different code paths and only one of them is the
common case.**

- *Signed in, then pay.* `/account` should read PRO and the Lab's counter
  should disappear.
- *Pay first, no account* — the funnel the pricing page leads with. Type an
  email on `/pricing`, pay, land on `/welcome`, then create an account with
  that same address. It should be Pro the moment you do.
- *Pay twice.* Back to `/pricing` while Pro: signed in, it opens the billing
  portal instead of a second checkout; signed out with the same email, it
  refuses with "already has a subscription".
- *Cancel.* In the portal, cancel immediately: `/account` should read FREE.

The plan sync (`lib/billing.ts`) was checked on 2026-09-19 against the real
tables with a fake Stripe client: active → canceled → a replayed event (stays
canceled) → monthly cancelled + yearly active (Pro, on the yearly) → customer
deleted, and a portal-edited email never getting its own grant. The profile
branch needs an auth user and is only covered by the test purchase above.

⚠️ The pay-first path is written and **has never been run end to end**, because
there is no Stripe account yet — the checkout route refuses at the keys check
before anything else executes. What HAS been verified is the half that decides
who gets Pro: `active` and `trialing` grants resolve to `pro`, `canceled` and
`past_due` to `free` (db/047, checked against four synthetic grants). The
untested half is Stripe's own round trip.

⚠️ **`pro_grants` (db/047) is why paying without an account works.** A payment
can arrive for an email that has no `auth.users` row yet, and `profiles.id`
references that table, so there is nowhere to write it. The webhook therefore
always writes the grant keyed by EMAIL, and `handle_new_user()` claims it when
the account is created — in either order, at any later time. Email is only safe
as the join key because Supabase issues it: the grant goes to whoever proves
control of the address through auth, never to whoever types it.

⚠️ **The webhook is the only thing that grants Pro.** `profiles` has no UPDATE
policy for the client (db/044), so a browser cannot set its own plan. If the
webhook is not configured, a successful payment changes nothing on the site.

### What is deliberately NOT built

**Alerts.** `public.alerts` exists in db/044 and nothing sends anything. The
pricing page names them as what is next rather than listing them as bought.
Wiring them needs an email provider and a sender — do not list them as a Pro
feature before both exist.
