import type { ReactNode } from 'react';
import { RootProvider } from 'fumadocs-ui/provider';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { source } from '@/lib/source';
import { Logo } from '@/components/Logo';
import { SchemeSurface } from '@/components/ui/scheme-surface';
import { SidebarPageLink } from './sidebar-page-link';
import { ThemeMenu } from '@/components/ui/theme-menu';
import './docs.css';

export default function DocsSectionLayout({ children }: { children: ReactNode }) {
  return (
    <RootProvider theme={{ enabled: false }}>
      <SchemeSurface>
      <div className="text-fg">
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
            components: { Item: SidebarPageLink },
            footer: <ThemeMenu className="-ml-0.5" />,
          }}
          containerProps={{ className: 'min-h-screen' }}
        >
          {children}
        </DocsLayout>
      </div>
      </SchemeSurface>
    </RootProvider>
  );
}
