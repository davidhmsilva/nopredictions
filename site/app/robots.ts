import type { MetadataRoute } from 'next'

/** Everything a visitor can read is open to crawlers — home, Create, the
 *  Marketplace and its agents. The API, sign-in, account pages and a user's
 *  own agents (/agent/…, private) are kept out. */
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
