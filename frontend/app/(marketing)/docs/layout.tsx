import type { ReactNode } from 'react';
import { RootProvider } from 'fumadocs-ui/provider';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { Navigation } from '@/components/marketing/Navigation';
import { Footer } from '@/components/marketing/Footer';
import { source } from '@/lib/source';
import 'fumadocs-ui/style.css';

export default function DocsSectionLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-nd-base">
      <Navigation />
      <RootProvider theme={{ defaultTheme: 'dark', forcedTheme: 'dark' }}>
        <DocsLayout
          tree={source.pageTree}
          nav={{ enabled: false }}
          sidebar={{
            defaultOpenLevel: 1,
            collapsible: false,
          }}
          githubUrl="https://github.com/tabularasa31/ai-chatbot"
          containerProps={{
            className: 'flex-1',
          }}
        >
          {children}
        </DocsLayout>
      </RootProvider>
      <Footer />
    </div>
  );
}
