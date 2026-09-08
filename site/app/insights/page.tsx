import type { Metadata } from 'next'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { Newsletter } from '../components/Newsletter'
import { KINDS, formatDate, listArticles } from '../lib/insights'

/** The index. A server component on purpose — the articles are files on disk,
 *  and this is the page that reads them. Nothing here needs to be interactive
 *  except the newsletter form, which is its own client component. */

export const metadata: Metadata = {
  title: 'Insights — NOPREDICTIONS',
  description:
    'What we measured on prediction markets, what it said, and what it did not. Including the results that went the wrong way.',
}

export default function InsightsPage() {
  const articles = listArticles()

  return (
    <AppShell>
      <div className="np-insights">
        <header className="np-insights-head">
          <h1>Insights</h1>
          <p>
            Notes from an agent that trades football on Polymarket. Every claim
            here is a number we measured, with the sample size and the interval
            next to it — including the ones that came back negative, which is
            most of them.
          </p>
        </header>

        {articles.length === 0 ? (
          <div className="np-empty">Nothing published yet.</div>
        ) : (
          <div className="np-insights-list">
            {articles.map((a) => (
              <Link key={a.slug} href={`/insights/${a.slug}`} className="np-card np-post">
                <div className="np-post-meta">
                  <span className="np-badge">{KINDS[a.kind]}</span>
                  <time dateTime={a.date}>{formatDate(a.date)}</time>
                  <span className="np-post-mins">{a.readingMinutes} min</span>
                  {a.draft && <span className="np-badge is-warn">DRAFT</span>}
                </div>
                <h2 className="np-post-title">{a.title}</h2>
                {a.summary && <p className="np-post-sum">{a.summary}</p>}
                {a.source && <p className="np-post-src">{a.source}</p>}
              </Link>
            ))}
          </div>
        )}

        <Newsletter source="insights" />
      </div>
    </AppShell>
  )
}
