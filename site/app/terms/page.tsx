import type { Metadata } from 'next'
import Link from 'next/link'
import { LegalPage, Mail, OPERATOR } from '../components/LegalPage'
import { FREE_DAILY_LIMIT, PRICING } from '../lib/planTerms'

export const metadata: Metadata = {
  title: 'Terms of Service — NOPREDICTIONS',
  description: 'The terms for using NOPREDICTIONS, free and Pro.',
  alternates: { canonical: '/terms' },
}

export default function TermsPage() {
  return (
    <LegalPage
      path="/terms"
      title="Terms of Service"
      lede={
        <>
          These terms apply to nopredictions.com and everything on it (the &ldquo;Service&rdquo;).
          By using the Service or creating an account you agree to them. If you do not agree,
          please do not use the Service.
        </>
      }
    >
      <h2>1. Who we are</h2>
      <p>
        The Service is operated by {OPERATOR} (&ldquo;we&rdquo;, &ldquo;us&rdquo;). You can reach us
        at <Mail />.
      </p>

      <h2>2. What the Service is — and is not</h2>
      <p>
        NOPREDICTIONS is an information and research tool. It shows prices published by prediction
        markets such as Polymarket and Kalshi, compares them, and adds sports data and analysis
        tools.
      </p>
      <ul>
        <li>
          <b>We do not take bets, place trades or hold anyone&apos;s money.</b> Any trade you make
          happens on a third-party platform, under that platform&apos;s own terms.
        </li>
        <li>
          <b>Nothing on the Service is advice or a tip.</b> It is not financial, investment,
          betting or legal advice, and it is not a recommendation to buy or sell anything.
        </li>
        <li>
          <b>We are not affiliated with</b> Polymarket, Kalshi, ESPN, DraftKings or any other
          platform or data source we show. Their names and logos belong to them and are used only to
          identify where a price comes from.
        </li>
      </ul>

      <h2>3. Who can use it</h2>
      <p>
        You must be at least 18, or the age of majority where you live if that is higher (21 in some
        US states for some activities). Prediction markets and sports event contracts are
        restricted or prohibited in some countries and US states. It is your responsibility to know
        and follow the laws that apply to you, and to use any third-party platform only where you
        are allowed to.
      </p>

      <h2>4. Information is provided &ldquo;as is&rdquo;</h2>
      <p>
        We work hard to show accurate numbers, but prices change by the second and come from third
        parties. Anything on the Service can be delayed, incomplete or wrong — including prices,
        scores, statistics, which platform is cheaper, and text written by AI. Always check the price
        on the platform itself before you trade. Backtests and paper-trading agents are simulations:
        they do not involve real money, and past results do not predict future results.
      </p>
      <p>
        Betting and trading carry real risk of loss. Only use money you can afford to lose. If
        gambling is causing you problems, call 1-800-GAMBLER (US) or contact a local support service.
      </p>

      <h2>5. Your account</h2>
      <p>
        Give us accurate information, keep your password to yourself, and tell us at <Mail /> if you
        think someone else has used your account. One account per person. You are responsible for
        what happens under your account.
      </p>

      <h2>6. Plans and payment</h2>
      <p>
        <b>Free.</b> The odds pages and match pages are free. A free account includes{' '}
        {FREE_DAILY_LIMIT.lab} Lab tests and {FREE_DAILY_LIMIT.wallet} wallet reads a day, a limited
        number of agents and a watchlist.
      </p>
      <p>
        <b>Pro.</b> Pro costs US${PRICING.monthlyUsd} a month or US${PRICING.yearlyUsd} a year, plus
        any tax that applies, and removes the daily limits described on the{' '}
        <Link href="/pricing">pricing page</Link>. Payments are processed by Stripe; we never see or
        store your card details.
      </p>
      <ul>
        <li>
          <b>Renewal.</b> Pro renews automatically at the end of each month or year until you
          cancel.
        </li>
        <li>
          <b>Cancelling.</b> You can cancel at any time from your account page. You keep Pro until
          the end of the period you have paid for, and you are not charged again.
        </li>
        <li>
          <b>Refunds.</b> You can get a full refund within 14 days of your first payment. See the{' '}
          <Link href="/refunds">Refund Policy</Link>.
        </li>
        <li>
          <b>Price changes.</b> If we change the price, we will tell you by email at least 30 days
          before it applies to your next renewal, and you can cancel before then.
        </li>
      </ul>

      <h2>7. Fair use</h2>
      <p>Please do not:</p>
      <ul>
        <li>scrape, copy or resell the Service or its data, or access it with bots beyond normal personal use;</li>
        <li>try to get around the daily limits, for example with multiple accounts;</li>
        <li>interfere with, overload or try to break into the Service;</li>
        <li>use the Service for anything illegal.</li>
      </ul>
      <p>We may suspend or close accounts that do.</p>

      <h2>8. Your content and ours</h2>
      <p>
        The theories you write in the Lab and the agents you save are yours. You let us store and
        run them so the Service can work for you. We do not publish them without your permission.
        Everything else on the Service — the software, design, text and analysis — belongs to us or
        our licensors.
      </p>

      <h2>9. Liability</h2>
      <p>
        To the extent the law allows, we are not liable for losses arising from your use of the
        Service or from decisions you make with it, including trading or betting losses, and our
        total liability to you is limited to what you paid us in the 12 months before the claim.
        Nothing in these terms limits liability that cannot be limited by law, or your rights as a
        consumer.
      </p>

      <h2>10. Changes and ending</h2>
      <p>
        We may change or stop parts of the Service, and update these terms. If a change matters, we
        will say so on the Service or by email before it takes effect. You can stop using the
        Service and delete your account at any time by writing to <Mail />.
      </p>

      <h2>11. Law and disputes</h2>
      <p>
        These terms are governed by the laws of Portugal. If you are a consumer, you also keep the
        protection of the mandatory laws of the country where you live, and you can bring a claim
        in your local courts. Consumers in Portugal may also use an alternative dispute resolution
        entity; the list is published at www.consumidor.gov.pt. Please write to us first — most
        problems are quicker to fix that way.
      </p>
    </LegalPage>
  )
}
