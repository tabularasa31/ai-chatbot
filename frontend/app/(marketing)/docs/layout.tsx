import type { ReactNode } from 'react';
import { RootProvider } from 'fumadocs-ui/provider';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { source } from '@/lib/source';
import 'fumadocs-ui/style.css';
import './docs.css';

export default function DocsSectionLayout({ children }: { children: ReactNode }) {
  return (
    <RootProvider theme={{ defaultTheme: 'light', forcedTheme: 'light' }}>
      <DocsLayout
        tree={source.pageTree}
        nav={{
          enabled: true,
          title: (
            <span className="font-bold tracking-tight" style={{ color: '#FAF5FF', fontSize: '15px' }}>
              Chat9
            </span>
          ),
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
