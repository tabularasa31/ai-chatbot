import type { ReactNode } from 'react';
import { RootProvider } from 'fumadocs-ui/provider';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { source } from '@/lib/source';
import { Logo } from '@/components/Logo';
import './docs.css';

export default function DocsSectionLayout({ children }: { children: ReactNode }) {
  return (
    <RootProvider theme={{ defaultTheme: 'light', forcedTheme: 'light' }}>
      <DocsLayout
        tree={source.pageTree}
        nav={{
          enabled: true,
          title: <Logo size={18} textClassName="text-[15px]" />,
        }}
        sidebar={{
          defaultOpenLevel: 1,
          collapsible: false,
        }}
        containerProps={{ className: 'min-h-screen' }}
      >
        {children}
      </DocsLayout>
    </RootProvider>
  );
}
