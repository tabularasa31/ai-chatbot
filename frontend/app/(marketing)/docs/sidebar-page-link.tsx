'use client';

import type { PageTree } from 'fumadocs-core/server';
import { SidebarItem } from 'fumadocs-ui/layouts/docs/sidebar';
import { usePathname } from 'next/navigation';

export function SidebarPageLink({ item }: { item: PageTree.Item }) {
  const isCurrent = usePathname() === item.url;
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
