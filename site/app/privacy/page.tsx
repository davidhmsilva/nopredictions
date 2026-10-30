import type { Metadata } from 'next'
import { LegalPage, Mail, OPERATOR } from '../components/LegalPage'

export const metadata: Metadata = {
  title: 'Privacy Policy — NOPREDICTIONS',
  description: 'What NOPREDICTIONS collects, why, who processes it, and your rights.',
  alternates: { canonical: '/privacy' },
}

export default function PrivacyPage() {
  return (
    <LegalPage
      path="/privacy"
      title="Privacy Policy"
      lede={
        <>
          We collect as little as we can. You can browse the Marketplace without an account, and we run
          no advertising or analytics trackers. This page lists everything we do keep, and why.
        </>
      }
    >
      <h2>1. Who is responsible</h2>
      <p>
        {OPERATOR} is the controller of your personal data under the EU General Data Protection
        Regulation (GDPR). Contact: <Mail />.
      </p>

      <h2>2. What we collect</h2>
      <ul>
        <li>
          <b>Your account.</b> Your email address and a password, which our authentication provider
          stores only in hashed form. If you sign in with Google, we receive your email address
          from Google.
        </li>
        <li>
          <b>Your plan and payments.</b> Your plan, and for Pro: the email used at checkout, your
          Stripe customer and subscription IDs, and the subscription&apos;s status. Stripe handles
          your card; we never see or store card details.
        </li>
        <li>
          <b>Daily limits.</b> When you build an agent from your own words, we record that it
          happened and when, so the daily limit works.
        </li>
        <li>
          <b>What you create.</b> The ideas you describe, the agents you build or copy, and their
          paper records. If you publish an agent, its strategy, the words you wrote and its record
          are shown on the Marketplace under your public name, or a pseudonym if you set none —
          never your email.
        </li>
        <li>
          <b>Newsletter.</b> Your email address, only if you subscribe.
        </li>
        <li>
          <b>Technical data.</b> Your IP address is used briefly to protect the Service from abuse,
          and our hosting provider keeps standard server logs for a short time.
        </li>
      </ul>

      <h2>3. Stored in your browser</h2>
      <p>
        We use only what the Service needs to work. The session cookies set when you sign in keep
        you signed in. Your browser&apos;s local storage remembers your odds format and the email you
        typed at checkout, so it can be filled in on the sign-in page. There are no advertising, tracking or analytics cookies, which is why there is
        no cookie banner.
      </p>

      <h2>4. Why, and on what legal basis</h2>
      <ul>
        <li>
          <b>To provide the Service you asked for</b> — your account, Pro, building and running your
          agents, and the Marketplace. Legal basis: performance of our contract with you.
        </li>
        <li>
          <b>To keep the Service safe</b> and stop abuse of the free limits. Legal basis: our
          legitimate interests.
        </li>
        <li>
          <b>To keep billing records</b> as tax law requires. Legal basis: legal obligation.
        </li>
        <li>
          <b>To send the newsletter.</b> Legal basis: your consent, which you can withdraw at any
          time.
        </li>
      </ul>
      <p>We do not sell your personal data, and we do not use it for advertising.</p>

      <h2>5. Who processes it for us</h2>
      <ul>
        <li><b>Supabase</b> — database and sign-in.</li>
        <li><b>Vercel</b> — website hosting.</li>
        <li><b>Stripe</b> — payments and subscriptions.</li>
        <li>
          <b>Anthropic</b> — AI. The text of an idea you submit on Create is sent to Anthropic&apos;s
          Claude to be turned into a strategy.
        </li>
      </ul>
      <p>
        Some of these providers process data outside the European Economic Area, including in the
        United States. Where they do, the transfer is covered by the European Commission&apos;s
        Standard Contractual Clauses or the EU-US Data Privacy Framework.
      </p>

      <h2>6. How long we keep it</h2>
      <p>
        Account data is kept while you have an account, and deleted when you ask us to close it.
        Billing records are kept for as long as tax law requires (up to 10 years in Portugal).
        Newsletter addresses are kept until you unsubscribe. Server logs are kept by our providers
        for a short period.
      </p>

      <h2>7. Your rights</h2>
      <p>
        You can ask us to access, correct, delete or export your data, to restrict how we use it, or
        to stop using it where we rely on legitimate interests. Write to <Mail /> and we will answer
        within one month. You can also complain to your data protection authority — in Portugal,
        the Comissão Nacional de Proteção de Dados (www.cnpd.pt).
      </p>
      <p>
        If you live in a US state with a privacy law, such as California, you have similar rights to
        know, delete and correct your data. We do not sell or &ldquo;share&rdquo; personal
        information for cross-context behavioural advertising.
      </p>

      <h2>8. Children</h2>
      <p>The Service is for adults only. We do not knowingly collect data from anyone under 18.</p>

      <h2>9. Security</h2>
      <p>
        Data is encrypted in transit. Access to it is limited to what the Service needs, and
        passwords are never stored in readable form.
      </p>

      <h2>10. Changes</h2>
      <p>
        If we change this policy in a way that matters, we will say so on the Service or by email
        before the change takes effect.
      </p>
    </LegalPage>
  )
}
