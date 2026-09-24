import type { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import { AppShell } from '../../components/AppShell'
import { Newsletter } from '../../components/Newsletter'
import { KINDS, articleSlugs, formatDate, getArticle, listArticles } from '../../lib/insights'

/** One article. Statically generated from the file, so a reader pays no server
 *  work at all — the whole page is on the CDN.
 *
 *  ⚠️ The prose is constrained to a readable MEASURE rather than to the
 *     container: at 680px and 15.5px it was running 87 characters a line,
 *     where comfortable is 60-75. Tables and code blocks are deliberately left
 *     out of that constraint — a data table narrowed to reading width has to
 *     scroll, which is a worse trade than a wide table.
 */

export function generateStaticParams() {
  return articleSlugs().map((slug) => ({ slug }))
}

export function generateMetadata({ params }: { params: { slug: string } }): Metadata {
  const a = getArticle(params.slug)
  if (!a) return { title: 'Not found — NOPREDICTIONS' }
  return {
    title: `${a.title} — NOPREDICTIONS`,
    description: a.summary,
    alternates: { canonical: `/insights/${params.slug}` },
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

  // What to read next. Two, newest first, never this one.
  const more = listArticles()
    .filter((x) => x.slug !== a.slug)
    .slice(0, 2)

  return (
    <AppShell>
      <article className="in-article">
        <Link href="/insights" className="in-back">← Insights</Link>

        <header className="in-head">
          <div className="in-meta">
            <span className="np-badge">{KINDS[a.kind]}</span>
            <time dateTime={a.date}>{formatDate(a.date)}</time>
            <span className="in-mins">{a.readingMinutes} min</span>
            {a.draft && <span className="np-badge is-warn">DRAFT</span>}
          </div>
          <h1>{a.title}</h1>
          {a.summary && <p className="in-article-sum">{a.summary}</p>}
          {a.source && (
            <p className="in-src in-article-src">
              <strong>Measured on:</strong> {a.source}
            </p>
          )}
        </header>

        {/* Safe here for one specific reason: the input is a Markdown file in
            this repository, written by whoever can already deploy the site. If
            an article ever comes from a database row or a form, this needs
            sanitising first — see app/lib/insights.ts. */}
        <div className="np-prose" dangerouslySetInnerHTML={{ __html: a.html }} />

        <footer className="in-foot">
          <p>
            The agent behind these numbers trades on paper and says so on{' '}
            <Link href="/agent/ours">its own record</Link>. Nothing here is betting
            advice.
          </p>
        </footer>

        {more.length > 0 && (
          <section className="in-more">
            <div className="tp-section-head">
              <h2>Read next</h2>
            </div>
            <div className="in-list">
              {more.map((m) => (
                <Link key={m.slug} href={`/insights/${m.slug}`} className="in-row">
                  <div className="in-row-main">
                    <div className="in-meta">
                      <span className="np-badge">{KINDS[m.kind]}</span>
                      <time dateTime={m.date}>{formatDate(m.date)}</time>
                      <span className="in-mins">{m.readingMinutes} min</span>
                    </div>
                    <h3>{m.title}</h3>
                    {m.summary && <p>{m.summary}</p>}
                  </div>
                  <span className="in-row-go" aria-hidden="true">→</span>
                </Link>
              ))}
            </div>
          </section>
        )}

        <Newsletter source={`article:${a.slug}`} />
      </article>
    </AppShell>
  )
}
