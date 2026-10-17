'use client';

import type { PageTree } from 'fumadocs-core/server';
import { SidebarItem } from 'fumadocs-ui/layouts/docs/sidebar';
import { usePathname } from 'next/navigation';

const withoutTrailingSlash = (path: string) => path.replace(/\/$/, '');

export function SidebarPageLink({ item }: { item: PageTree.Item }) {
  const isCurrent = withoutTrailingSlash(usePathname()) === withoutTrailingSlash(item.url);
  return (
    <SidebarItem
      href={item.url}
      external={item.external}
      icon={item.icon}
      aria-current={isCurrent ? 'page' : undefined}
    >
      {item.name}
    </SidebarItem>
  );
}
