import type { Metadata } from 'next'
import { LegalPage, Mail } from '../components/LegalPage'

export const metadata: Metadata = {
  title: 'Refund Policy — NOPREDICTIONS',
  description: 'A full refund within 14 days of your first Pro payment. Cancel any time after that.',
  alternates: { canonical: '/refunds' },
}

export default function RefundsPage() {
  return (
    <LegalPage
      path="/refunds"
      title="Refund Policy"
      lede={<>Short version: if Pro isn&apos;t for you, tell us within 14 days and you get all your money back.</>}
    >
      <h2>14-day money-back guarantee</h2>
      <p>
        If you ask within 14 days of your first Pro payment, monthly or yearly, we refund it in
        full. You don&apos;t need to give a reason.
      </p>

      <h2>After 14 days</h2>
      <p>
        You can cancel at any time from your account page. You keep Pro until the end of the month
        or year you have already paid for, and you are not charged again. We don&apos;t refund the
        unused part of a period that has already started, and automatic renewals are not
        refundable.
      </p>

      <h2>How to ask for a refund</h2>
      <p>
        Email <Mail /> from the address on your account, or tell us the email you paid with. We
        process refunds through Stripe to your original payment method. They usually appear within
        5 to 10 business days, depending on your bank. Pro ends when the refund is issued.
      </p>

      <h2>Your legal rights</h2>
      <p>
        This policy adds to your rights under the law and does not replace them. If you are a
        consumer in the European Union, the 14-day guarantee covers your right to withdraw from a
        distance contract.
      </p>
    </LegalPage>
  )
}
