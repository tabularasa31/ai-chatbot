import type { Metadata } from 'next';
import { Navigation } from '@/components/marketing/Navigation';
import { Hero } from '@/components/marketing/Hero';
import { Features } from '@/components/marketing/Features';
import { DemoBlock } from '@/components/marketing/DemoBlock';
import { Stats } from '@/components/marketing/Stats';
import { CTABanner } from '@/components/marketing/CTABanner';
import { Footer } from '@/components/marketing/Footer';
import { getSiteUrl } from '@/lib/site';

const TITLE = 'Chat9 — AI support chatbot for your docs';
const DESCRIPTION =
  'Chat9 turns your docs into a 24/7 AI support agent. Upload your knowledge base, embed the widget, and let it answer customers automatically — with a daily report in your inbox.';

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: {
    canonical: '/',
  },
  openGraph: {
    type: 'website',
    url: `${getSiteUrl()}/`,
    siteName: 'Chat9',
    locale: 'en_US',
    title: TITLE,
    description: DESCRIPTION,
  },
  twitter: {
    card: 'summary_large_image',
    title: TITLE,
    description: DESCRIPTION,
  },
};

function LandingJsonLd() {
  const siteUrl = getSiteUrl();
  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Organization',
        '@id': `${siteUrl}/#organization`,
        name: 'Chat9',
        url: `${siteUrl}/`,
        description: DESCRIPTION,
      },
      {
        '@type': 'WebSite',
        '@id': `${siteUrl}/#website`,
        url: `${siteUrl}/`,
        name: 'Chat9',
        description: DESCRIPTION,
        publisher: { '@id': `${siteUrl}/#organization` },
      },
      {
        '@type': 'SoftwareApplication',
        name: 'Chat9',
        applicationCategory: 'BusinessApplication',
        operatingSystem: 'Web',
        url: `${siteUrl}/`,
        description: DESCRIPTION,
      },
    ],
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
    />
  );
}

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-[#0A0A0F] font-['Inter']" style={{ scrollBehavior: 'smooth' }}>
      <LandingJsonLd />
      <Navigation />
      <main>
        <Hero />
        <Features />
        <DemoBlock />
        <Stats />
        <CTABanner />
      </main>
      <Footer />
    </div>
  );
}
