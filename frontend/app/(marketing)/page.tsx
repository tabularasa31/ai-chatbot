'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getToken, saveToken } from '@/lib/api';
import { Navigation } from '@/components/marketing/Navigation';
import { Hero } from '@/components/marketing/Hero';
import { Features } from '@/components/marketing/Features';
import { DemoBlock } from '@/components/marketing/DemoBlock';
import { Stats } from '@/components/marketing/Stats';
import { CTABanner } from '@/components/marketing/CTABanner';
import { Footer } from '@/components/marketing/Footer';

export default function LandingPage() {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (token) {
      saveToken(token);
      router.replace('/dashboard');
    } else {
      setChecked(true);
    }
  }, [router]);

  if (!checked) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="animate-pulse text-slate-600">Loading...</div>
      </div>
    );
  }

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
