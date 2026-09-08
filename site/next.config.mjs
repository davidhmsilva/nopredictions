/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
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
    return [
      // The landing page is now the homepage; keep the old URL working.
      { source: '/landing', destination: '/', permanent: false },

      // Routes that moved in the SaaS split. `/dashboard` was linked from the
      // old landing page as "LIVE TRACK RECORD" and `/test` was the hidden
      // Hypothesis Tester people were sent to by hand — both are out there in
      // links we do not control, so neither may 404.
      { source: '/dashboard', destination: '/agent', permanent: true },
      { source: '/test', destination: '/lab', permanent: true },
      // The scanner is gone rather than moved: the Game Center does what it did
      // and takes the same pasted URL, so its traffic belongs on the board.
      { source: '/scanner', destination: '/', permanent: true },
    ]
  },
}

export default nextConfig
