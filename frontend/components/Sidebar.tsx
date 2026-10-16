"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  BookOpen,
  ChartColumn,
  CodeXml,
  FileSearch,
  Inbox,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { api } from "@/lib/api";
import { useClientMe, useInboxSummary } from "@/hooks/useApi";
import { CountBadge } from "@/components/ui/count-badge";
import { cn } from "@/components/ui/utils";

type BadgeKey = "inbox" | "gaps";

type NavItem = {
  href: string;
  activeOn: string[];
  label: string;
  Icon: LucideIcon;
  ownerOnly?: boolean;
  adminOnly?: boolean;
  badge?: BadgeKey;
};

const INBOX_BADGE_POLL_MS = 30_000;

const BADGES: Record<BadgeKey, { tone: "brand" | "quiet"; meaning: string }> = {
  inbox: { tone: "brand", meaning: "need attention" },
  gaps: { tone: "quiet", meaning: "new" },
};

const NAV_GROUPS: NavItem[][] = [
  [
    { href: "/inbox", activeOn: ["/inbox"], label: "Inbox", Icon: Inbox, badge: "inbox" },
    { href: "/gap-analyzer", activeOn: ["/gap-analyzer"], label: "Doc gaps", Icon: FileSearch, badge: "gaps" },
    { href: "/knowledge", activeOn: ["/knowledge"], label: "Knowledge", Icon: BookOpen },
    { href: "/analytics", activeOn: ["/analytics"], label: "Analytics", Icon: ChartColumn },
  ],
  [
    { href: "/dashboard", activeOn: ["/dashboard"], label: "Install widget", Icon: CodeXml },
    { href: "/settings", activeOn: ["/settings", "/widget-settings"], label: "Settings", Icon: Settings, ownerOnly: true },
  ],
  [
    { href: "/admin/metrics", activeOn: ["/admin"], label: "Platform metrics", Icon: Activity, adminOnly: true },
  ],
];

function NavLink({
  item,
  active,
  count,
}: {
  item: NavItem;
  active: boolean;
  count: number;
}) {
  const { Icon, badge } = item;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex h-11 items-center gap-3 rounded-control px-3 text-ui font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-chrome-accent",
        active
          ? "bg-chrome-active text-chrome-accent shadow-[inset_2px_0_0_rgb(var(--c-chrome-accent))]"
          : "text-chrome-fg-muted hover:bg-chrome-hover hover:text-chrome-fg",
      )}
    >
      <Icon size={16} aria-hidden="true" className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {badge && <CountBadge count={count} {...BADGES[badge]} />}
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [gapCount, setGapCount] = useState(0);
  const onGapAnalyzer = pathname.startsWith("/gap-analyzer");

  // Assume owner until /tenants/me says otherwise: the dashboard's own owner
  // sees no flicker, and an operator loses the owner-only links a moment later.
  const { data: client } = useClientMe();
  const isAdmin = client?.is_admin ?? false;
  const isOwner = client?.role === "owner" || client === undefined;

  const { data: inboxSummary } = useInboxSummary(INBOX_BADGE_POLL_MS);
  const counts: Record<BadgeKey, number> = {
    inbox: inboxSummary?.attention_count ?? 0,
    gaps: gapCount,
  };

  useEffect(() => {
    api.gapAnalyzer
      .getSummary()
      .then((data) => setGapCount(data.summary.new_badge_count))
      .catch(() => {});
  }, [onGapAnalyzer]);

  const isActive = (item: NavItem) =>
    item.activeOn.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  const isVisible = (item: NavItem) =>
    (!item.ownerOnly || isOwner) && (!item.adminOnly || isAdmin);

  return (
    <aside className="fixed bottom-0 left-0 top-12 flex w-52 flex-col border-r border-chrome-line bg-chrome p-3">
      <nav aria-label="Main" className="flex flex-1 flex-col gap-6">
        {NAV_GROUPS.map((group) => ({ key: group[0].href, items: group.filter(isVisible) }))
          .filter(({ items }) => items.length > 0)
          .map(({ key, items }) => (
            <div key={key} className="flex flex-col gap-1">
              {items.map((item) => (
                <NavLink
                  key={item.href}
                  item={item}
                  active={isActive(item)}
                  count={item.badge ? counts[item.badge] : 0}
                />
              ))}
            </div>
          ))}
      </nav>
    </aside>
  );
}
