import { Navigation } from '@/components/marketing/Navigation';
import { Hero } from '@/components/marketing/Hero';
import { Features } from '@/components/marketing/Features';
import { DemoBlock } from '@/components/marketing/DemoBlock';
import { Stats } from '@/components/marketing/Stats';
import { CTABanner } from '@/components/marketing/CTABanner';
import { Footer } from '@/components/marketing/Footer';

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-[#0A0A0F] font-['Inter']" style={{ scrollBehavior: 'smooth' }}>
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
