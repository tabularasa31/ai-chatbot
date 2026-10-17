import type { ReactNode } from 'react';
import { RootProvider } from 'fumadocs-ui/provider';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { source } from '@/lib/source';
import { Logo } from '@/components/Logo';
import { SchemeSurface } from '@/components/ui/scheme-surface';
import { ThemeSwitch } from '@/components/ui/theme-switch';
import './docs.css';

export default function DocsSectionLayout({ children }: { children: ReactNode }) {
  return (
    <RootProvider theme={{ enabled: false }}>
      <SchemeSurface />
      <DocsLayout
        tree={source.pageTree}
        nav={{
          enabled: true,
          title: <Logo size={18} textClassName="text-body text-fg" />,
        }}
        disableThemeSwitch
        sidebar={{
          defaultOpenLevel: 1,
          collapsible: false,
          footer: <ThemeSwitch />,
        }}
        containerProps={{ className: 'min-h-screen bg-canvas text-fg' }}
      >
        {children}
      </DocsLayout>
    </RootProvider>
  );
}
