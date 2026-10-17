import type { Metadata } from 'next';

// Site-wide defaults for all marketing routes (/, /blog, /docs). Per-page
// metadata (canonical, openGraph.url, images) is set by each page/segment so
// that page-specific URLs never leak here. The homepage OG image comes from the
// `opengraph-image` file convention in this segment.
export const metadata: Metadata = {
  title: 'Chat9 — AI support chatbot for your docs',
  description:
    'Chat9 answers support questions from your documentation. Upload files or add your docs site, paste one snippet, and hand off to your team when the bot cannot answer.',
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
