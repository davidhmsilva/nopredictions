import type { Metadata } from 'next'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { Newsletter } from '../components/Newsletter'
import { KINDS, formatDate, listArticles, type ArticleCard } from '../lib/insights'

/** The index. A server component on purpose — the articles are files on disk,
 *  and this is the page that reads them. Nothing here needs to be interactive
 *  except the newsletter form, which is its own client component.
 *
 *  The list used to be three identical cards, which gave the newest piece no
 *  more weight than the oldest. The latest one leads now and the rest are rows:
 *  a reader arriving at a writing section wants to know what is new before
 *  they want a catalogue.
 */

export const metadata: Metadata = {
  title: 'Insights — NOPREDICTIONS',
  description:
    'What we measured on prediction markets, what it said, and what it did not. Including the results that went the wrong way.',
}

function Meta({ a }: { a: ArticleCard }) {
  return (
    <div className="in-meta">
      <span className="np-badge">{KINDS[a.kind]}</span>
      <time dateTime={a.date}>{formatDate(a.date)}</time>
      <span className="in-mins">{a.readingMinutes} min</span>
      {a.draft && <span className="np-badge is-warn">DRAFT</span>}
    </div>
  )
}

export default function InsightsPage() {
  const articles = listArticles()
  const [lead, ...rest] = articles

  return (
    <AppShell>
      <div className="in-page">
        <header className="tp-head">
          <span className="tp-eyebrow">INSIGHTS</span>
          <h1 className="tp-h1">Everything we measured, including the failures.</h1>
          <p className="tp-sub">
            Notes from an agent that trades football on Polymarket. Every claim here
            is a number we measured, with the sample size and the interval next to it
            — and most of them came back negative, which is why they are worth
            reading.
          </p>
        </header>

        {articles.length === 0 ? (
          <div className="np-empty">Nothing published yet.</div>
        ) : (
          <>
            {/* The newest piece, with room to make its case. */}
            <Link href={`/insights/${lead.slug}`} className="in-lead">
              <span className="in-lead-flag">LATEST</span>
              <Meta a={lead} />
              <h2>{lead.title}</h2>
              {lead.summary && <p className="in-lead-sum">{lead.summary}</p>}
              {lead.source && <p className="in-src">{lead.source}</p>}
              <span className="in-go">
                Read it <span aria-hidden="true">→</span>
              </span>
            </Link>

            {rest.length > 0 && (
              <section className="in-rest">
                <div className="tp-section-head">
                  <h2>Everything else</h2>
                </div>
                <div className="in-list">
                  {rest.map((a) => (
                    <Link key={a.slug} href={`/insights/${a.slug}`} className="in-row">
                      <div className="in-row-main">
                        <Meta a={a} />
                        <h3>{a.title}</h3>
                        {a.summary && <p>{a.summary}</p>}
                        {a.source && <p className="in-src">{a.source}</p>}
                      </div>
                      <span className="in-row-go" aria-hidden="true">→</span>
                    </Link>
                  ))}
                </div>
              </section>
            )}
          </>
        )}

        <Newsletter source="insights" />
      </div>
    </AppShell>
  )
}
