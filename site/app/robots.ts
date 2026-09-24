import type { MetadataRoute } from 'next'

/** Everything a visitor can read is open to crawlers; the API, sign-in and
 *  account pages are not worth crawling and are kept out. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: ['/api/', '/account', '/login', '/auth/', '/welcome', '/agent/'],
      },
    ],
    sitemap: 'https://www.nopredictions.com/sitemap.xml',
    host: 'https://www.nopredictions.com',
  }
}
