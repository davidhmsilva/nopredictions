'use client'

/** An agent's strategy as its owner reads it — on Create before it is
 *  deployed, on the agent's page after, and on the Marketplace before anyone
 *  copies it. What it backs, the conditions a game has to meet, where it looks,
 *  and how it bets.
 *
 *  Both kinds are written from the SPEC the runner reads (describeSpec for a
 *  pre-match rule, describeInplay for a live one), so the words on the page
 *  and the agent's behaviour cannot drift apart.
 */

import type { Spec } from '../lib/backtest'
import { oddsText, useOddsFormat } from '../lib/display'
import { describeInplay, inplayBuyText, isInplaySpec, type InplaySpec } from '../lib/inplaySpec'
import { describeSpec } from '../lib/strategyText'

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="st-row">
      <dt>{k}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function Conditions({ lines, none }: { lines: string[]; none: string }) {
  if (!lines.length) return <>{none}</>
  return (
    <ul className="st-when">
      {lines.map((l, i) => (
        <li key={i}>{cap(l)}</li>
      ))}
    </ul>
  )
}

export function StrategyCard({
  spec,
  label = 'The strategy',
}: {
  spec: Spec | InplaySpec
  label?: string
}) {
  const fmt = useOddsFormat()

  if (isInplaySpec(spec)) {
    const r = describeInplay(spec)
    return (
      <section className="st-card" aria-label={label}>
        <div className="st-label">{label}</div>
        <dl className="st-rows">
          <Row k="Buys">{cap(inplayBuyText(spec))}, during the match</Row>
          <Row k="When">
            <Conditions lines={r.entry} none="Any live match" />
          </Row>
          <Row k="Gets out">{r.exit}</Row>
          <Row k="How">One paper bet of 1 unit per match, at Polymarket&apos;s price, from the moment it runs.</Row>
        </dl>
      </section>
    )
  }

  const t = describeSpec(spec, (d) => oddsText(d, fmt))
  return (
    <section className="st-card" aria-label={label}>
      <div className="st-label">{label}</div>
      <dl className="st-rows">
        <Row k="Bets on">{t.bet}</Row>
        <Row k="When">
          <Conditions lines={t.when} none="Every game — no other condition" />
        </Row>
        <Row k="Where">{t.where}</Row>
        <Row k="How">{t.how}</Row>
      </dl>
    </section>
  )
}
