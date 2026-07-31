import type { Metadata } from 'next';

// Site-wide defaults for all marketing routes (/, /blog, /docs). Per-page
// metadata (canonical, openGraph.url, images) is set by each page/segment so
// that page-specific URLs never leak here. The homepage OG image comes from the
// `opengraph-image` file convention in this segment.
export const metadata: Metadata = {
  title: 'Chat9 - Your support mate, always on',
  description:
    'Meet your new support mate. Works 24/7. Sends you a daily report. Gets better every week.',
  robots: {
    index: true,
    follow: true,
  },
  openGraph: {
    type: 'website',
    siteName: 'Chat9',
    locale: 'en_US',
  },
  twitter: {
    card: 'summary_large_image',
  },
};

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
