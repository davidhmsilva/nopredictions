import type { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import { AppShell } from '../../components/AppShell'
import { Newsletter } from '../../components/Newsletter'
import { KINDS, articleSlugs, formatDate, getArticle } from '../../lib/insights'

/** One article. Statically generated from the file, so a reader pays no server
 *  work at all — the whole page is on the CDN. */

export function generateStaticParams() {
  return articleSlugs().map((slug) => ({ slug }))
}

export function generateMetadata({ params }: { params: { slug: string } }): Metadata {
  const a = getArticle(params.slug)
  if (!a) return { title: 'Not found — NOPREDICTIONS' }
  return {
    title: `${a.title} — NOPREDICTIONS`,
    description: a.summary,
    openGraph: {
      title: a.title,
      description: a.summary,
      type: 'article',
      publishedTime: a.date,
    },
  }
}

export default function ArticlePage({ params }: { params: { slug: string } }) {
  const a = getArticle(params.slug)
  if (!a) notFound()

  return (
    <AppShell>
      <article className="np-article">
        <Link href="/insights" className="np-article-back">← Insights</Link>

        <header className="np-article-head">
          <div className="np-post-meta">
            <span className="np-badge">{KINDS[a.kind]}</span>
            <time dateTime={a.date}>{formatDate(a.date)}</time>
            <span className="np-post-mins">{a.readingMinutes} min</span>
            {a.draft && <span className="np-badge is-warn">DRAFT</span>}
          </div>
          <h1>{a.title}</h1>
          {a.summary && <p className="np-article-sum">{a.summary}</p>}
          {a.source && (
            <p className="np-article-src">
              <strong>Measured on:</strong> {a.source}
            </p>
          )}
        </header>

        {/* Safe here for one specific reason: the input is a Markdown file in
            this repository, written by whoever can already deploy the site. If
            an article ever comes from a database row or a form, this needs
            sanitising first — see app/lib/insights.ts. */}
        <div className="np-prose" dangerouslySetInnerHTML={{ __html: a.html }} />

        <footer className="np-article-foot">
          <p>
            The agent behind these numbers trades on paper and says so on{' '}
            <Link href="/agent">its own tab</Link>. Nothing here is betting advice.
          </p>
        </footer>

        <Newsletter source={`article:${a.slug}`} />
      </article>
    </AppShell>
  )
}
