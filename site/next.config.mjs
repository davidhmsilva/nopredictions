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
    ]
  },
}

export default nextConfig
