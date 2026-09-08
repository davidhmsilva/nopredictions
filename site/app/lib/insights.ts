/** Insights — the articles, read from Markdown in the repo.
 *
 *  Files live in `site/content/insights/*.md`, one per article, with YAML
 *  frontmatter. Publishing is a commit: the piece is versioned with the code,
 *  reviewable in a diff, and there is no CMS to pay for or keep logged into.
 *
 *  ⚠️ This module reads the filesystem, so it may only be imported from server
 *     components and route handlers. A client component that imports it gets a
 *     build error about `fs`, which is the right failure — it means the page
 *     was about to ship the whole content directory to the browser.
 *
 *  🔑 The rendered HTML comes from `marked` and is injected with
 *     `dangerouslySetInnerHTML`. That is safe here for one specific reason and
 *     no other: the input is a file in this repository, written by whoever can
 *     already deploy the site. The moment an article can come from anywhere
 *     else — a database row, a form, a reader — this needs sanitising first.
 */

import fs from 'fs'
import path from 'path'
import matter from 'gray-matter'
import { marked } from 'marked'

const DIR = path.join(process.cwd(), 'content', 'insights')

/** The kinds of thing that go here. The label is what the reader sees. */
export const KINDS = {
  research: 'Research',
  note: 'Note',
  news: 'News',
  explainer: 'Explainer',
} as const

export type Kind = keyof typeof KINDS

export interface Article {
  slug: string
  title: string
  /** One sentence. Used on the card, in the <meta description>, and nowhere
   *  else — so it has to stand alone without the title above it. */
  summary: string
  /** ISO date. The file is the record; nothing is inferred from mtime, which
   *  changes when git checks a file out. */
  date: string
  kind: Kind
  /** Optional: the finding or table this piece is reporting, so a claim can be
   *  traced back to the thing that measured it. */
  source?: string
  /** Draft articles are visible in development and never in production. */
  draft: boolean
  readingMinutes: number
  html: string
}

export type ArticleCard = Omit<Article, 'html'>

function isKind(v: unknown): v is Kind {
  return typeof v === 'string' && v in KINDS
}

/** ~200 words a minute, floored at 1. Rounded honestly rather than rounded up
 *  to make a piece look longer. */
function readingMinutes(body: string): number {
  const words = body.trim().split(/\s+/).filter(Boolean).length
  return Math.max(1, Math.round(words / 200))
}

function parse(file: string): Article | null {
  const slug = file.replace(/\.md$/, '')
  const raw = fs.readFileSync(path.join(DIR, file), 'utf8')
  const { data, content } = matter(raw)

  // A file missing a title or a date is a mistake, not an article. It is
  // skipped rather than rendered with "undefined" in the heading — but it is
  // logged, because a piece silently missing from the index is worse.
  if (!data.title || !data.date) {
    console.warn(`[insights] ${file}: missing title or date — skipped`)
    return null
  }

  return {
    slug,
    title: String(data.title),
    summary: String(data.summary ?? ''),
    date: new Date(data.date).toISOString(),
    kind: isKind(data.kind) ? data.kind : 'note',
    source: data.source ? String(data.source) : undefined,
    draft: data.draft === true,
    readingMinutes: readingMinutes(content),
    html: marked.parse(content, { async: false }) as string,
  }
}

function all(): Article[] {
  if (!fs.existsSync(DIR)) return []
  return fs
    .readdirSync(DIR)
    .filter((f) => f.endsWith('.md'))
    .map(parse)
    .filter((a): a is Article => a !== null)
    // Drafts are for the person writing them. `NODE_ENV` is the gate rather
    // than a query parameter, so a draft cannot be reached by guessing a URL
    // on the live site.
    .filter((a) => !a.draft || process.env.NODE_ENV !== 'production')
    .sort((a, b) => b.date.localeCompare(a.date))
}

export function listArticles(): ArticleCard[] {
  return all().map(({ html, ...card }) => card)
}

export function getArticle(slug: string): Article | null {
  return all().find((a) => a.slug === slug) ?? null
}

export function articleSlugs(): string[] {
  return all().map((a) => a.slug)
}

/** "8 September 2026" — the same everywhere, and never locale-dependent, so
 *  the server and the browser cannot render two different dates. */
export function formatDate(iso: string): string {
  const d = new Date(iso)
  const months = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ]
  return `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}
