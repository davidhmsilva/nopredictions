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
    return [
      // The landing page is now the homepage; keep the old URL working.
      { source: '/landing', destination: '/', permanent: false },

      // Routes that moved in the SaaS split. `/dashboard` was linked from the
      // old landing page as "LIVE TRACK RECORD" and `/test` was the hidden
      // Hypothesis Tester people were sent to by hand — both are out there in
      // links we do not control, so neither may 404.
      // ⚠️ /agent is the empty state since 2026-09-09, not the record. The old
      //    landing page linked here as "LIVE TRACK RECORD", so it has to reach
      //    the record — sending it to a page about creating an agent would be
      //    the wrong answer to the link someone actually clicked.
      { source: '/dashboard', destination: '/agent/ours', permanent: true },
      { source: '/test', destination: '/lab', permanent: true },
      // The scanner is gone rather than moved: the Game Center does what it did
      // and takes the same pasted URL, so its traffic belongs on the board.
      { source: '/scanner', destination: '/', permanent: true },
    ]
  },
}

export default nextConfig
