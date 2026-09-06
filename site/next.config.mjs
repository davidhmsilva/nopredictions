/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  typescript: {
    ignoreBuildErrors: true,
  },
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
