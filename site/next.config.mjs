/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // A second `next dev` in this folder (two sessions previewing at once) must
  // not share `.next` with the first — the collision surfaces as a missing
  // vendor chunk or a 500 on a page that builds fine. Unset in production.
  distDir: process.env.NEXT_DIST_DIR || '.next',
  // A type error is a bug that reached the deploy. The two that were being
  // ignored here were real: a Set the ES5 target could not iterate, and an
  // `avg_clv` typed as absent when the view returns null. Both fixed; the
  // gate stays up so the next one cannot ship.
  typescript: {
    ignoreBuildErrors: false,
  },
  // Lint still does not block a deploy. `npm run lint` is there to be read,
  // not to stand between a fix and production.
  eslint: {
    ignoreDuringBuilds: true,
  },
  async redirects() {
    // None of these is permanent: a browser caches a 308 for good, and this
    // site has changed shape three times in two months.
    const to = (destination) => (source) => ({ source, destination, permanent: false })
    return [
      // The landing page is now the homepage; keep the old URL working.
      to('/')('/landing'),

      // Older moves, still linked from places we do not control.
      // The public record is gone (2026-09-11): the pressure arms are the
      // operator's private agents, on /agent behind a sign-in.
      to('/agent')('/dashboard'),
      to('/agent')('/agent/ours'),

      // The Lab became Create (2026-10-28): the same idea box, now the first
      // step of building an agent. /test was the Lab's name before that.
      to('/create')('/lab'),
      to('/create')('/test'),

      // Everything that was not about agents left the product on 2026-10-28,
      // by the operator's decision: the odds boards, the Game Center, team
      // pages, results, dropping odds, the insights articles and the scanner
      // all land on the home page. The Wallet read a trader's record; the
      // nearest thing now is an agent's record, on the Marketplace.
      ...[
        '/scanner',
        '/soccer',
        '/:sport(nfl|cfb|mlb|nba|nhl|wnba)/:path*',
        '/game/:path*',
        '/team/:path*',
        '/teams',
        '/results/:path*',
        '/dropping-odds',
        '/insights/:path*',
      ].map(to('/')),
      to('/marketplace')('/wallet/:path*'),
    ]
  },
}

export default nextConfig
