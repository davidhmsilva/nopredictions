/** Stripe, and the rule that it is allowed not to exist yet.
 *
 *  🔑 Every export here is written so that a deployment with no Stripe keys
 *     behaves like a site that has no paid plan — not like a site that is
 *     broken. `stripeConfigured()` is the gate, and the pricing page, the
 *     checkout route and the portal route all ask it before doing anything.
 *     The alternative — throwing at import time on a missing env var — takes
 *     down the whole app for the sake of a feature nobody has bought yet.
 */

import Stripe from 'stripe'

export const STRIPE_SECRET = process.env.STRIPE_SECRET_KEY ?? ''
export const STRIPE_WEBHOOK_SECRET = process.env.STRIPE_WEBHOOK_SECRET ?? ''

/** The two prices, created in the Stripe dashboard and named here. */
export const PRICE_IDS = {
  monthly: process.env.STRIPE_PRICE_MONTHLY ?? '',
  yearly: process.env.STRIPE_PRICE_YEARLY ?? '',
} as const

export type BillingPeriod = keyof typeof PRICE_IDS

export function stripeConfigured(): boolean {
  return Boolean(STRIPE_SECRET && PRICE_IDS.monthly && PRICE_IDS.yearly)
}

/** True while the keys are Stripe's test-mode keys. The pricing page says so
 *  out loud, because a checkout that takes a 4242 card and grants Pro is a
 *  thing a real visitor must never mistake for a purchase. */
export function stripeTestMode(): boolean {
  return STRIPE_SECRET.startsWith('sk_test_')
}

let client: Stripe | null = null

export function stripe(): Stripe {
  if (!STRIPE_SECRET) {
    throw new Error('STRIPE_SECRET_KEY is not set')
  }
  if (!client) {
    client = new Stripe(STRIPE_SECRET, {
      // Pinned. An SDK that silently follows Stripe's newest API version is an
      // SDK that changes behaviour on a deploy that touched nothing.
      // Matches the version the installed SDK's types are generated against
      // (stripe@22.6.1). Bumping the SDK without bumping this is a type error,
      // which is the point: the pin should be a decision, not a default.
      apiVersion: '2026-08-26.dahlia',
      appInfo: { name: 'NOPREDICTIONS', url: 'https://nopredictions.com' },
    })
  }
  return client
}

/** The site's own origin, for redirect URLs.
 *
 *  Vercel sets VERCEL_URL to the deployment's own hostname, which on a preview
 *  is not the domain anyone typed. NEXT_PUBLIC_SITE_URL wins where it is set
 *  so a checkout always returns to the site the user was actually on. */
export function siteUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_SITE_URL
  if (explicit) return explicit.replace(/\/$/, '')
  if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}`
  return 'http://localhost:3000'
}
