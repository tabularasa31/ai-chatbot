import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const nextConfig = {
  transpilePackages: ["@chat9/widget-shared"],
  async redirects() {
    return [
      {
        // The plan screen was replaced by the seats screen. It shipped, so
        // bookmarks and open tabs exist; a temporary redirect rather than a
        // permanent one, which browsers would cache past our ability to
        // change our minds.
        source: "/settings/plan",
        destination: "/settings/seats",
        permanent: false,
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/ingest/static/:path*",
        destination: "https://eu-assets.i.posthog.com/static/:path*",
      },
      {
        source: "/ingest/:path*",
        destination: "https://eu.i.posthog.com/:path*",
      },
    ];
  },
  skipTrailingSlashRedirect: true,
};

export default withMDX(nextConfig);
