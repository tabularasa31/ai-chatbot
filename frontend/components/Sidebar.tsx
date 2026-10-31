"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BookOpen,
  ChartColumn,
  CodeXml,
  Inbox,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { useClientMe, useInboxSummary, useUnansweredBadge } from "@/hooks/useApi";
import { FOCUS_RING } from "@/components/ui/button";
import { CountBadge } from "@/components/ui/count-badge";
import { cn } from "@/components/ui/utils";

type BadgeKey = "inbox" | "unanswered";

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

const BADGES: Record<BadgeKey, { tone: "brand" | "quiet"; meaning: (count: number) => string }> = {
  inbox: { tone: "brand", meaning: () => "need attention" },
  unanswered: {
    tone: "quiet",
    meaning: (count) => (count === 1 ? "new unanswered question" : "new unanswered questions"),
  },
};

const NAV_GROUPS: NavItem[][] = [
  [
    { href: "/inbox", activeOn: ["/inbox"], label: "Inbox", Icon: Inbox, badge: "inbox" },
    { href: "/knowledge", activeOn: ["/knowledge"], label: "Knowledge", Icon: BookOpen, badge: "unanswered" },
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
        FOCUS_RING.chrome,
        "focus-visible:ring-inset focus-visible:ring-offset-0",
        active
          ? "bg-chrome-active text-chrome-accent shadow-chrome-selected"
          : "text-chrome-fg-muted hover:bg-chrome-hover hover:text-chrome-fg active:bg-chrome-press active:text-chrome-fg",
      )}
    >
      <Icon size={16} aria-hidden="true" className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {badge && <CountBadge count={count} tone={BADGES[badge].tone} meaning={BADGES[badge].meaning(count)} />}
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();

  const { data: client } = useClientMe();
  const roleKnown = client !== undefined;
  const isAdmin = client?.is_admin ?? false;
  const isOwner = client?.role === "owner";

  const { data: inboxSummary } = useInboxSummary(INBOX_BADGE_POLL_MS);
  const { data: unansweredBadge } = useUnansweredBadge();
  const counts: Record<BadgeKey, number> = {
    inbox: inboxSummary?.attention_count ?? 0,
    unanswered: unansweredBadge?.new_count ?? 0,
  };

  const isActive = (item: NavItem) =>
    item.activeOn.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  const isGated = (item: NavItem) => item.ownerOnly || item.adminOnly;
  const isVisible = (item: NavItem) =>
    !isGated(item) || (roleKnown && (!item.ownerOnly || isOwner) && (!item.adminOnly || isAdmin));
  const isReserved = (item: NavItem) => isGated(item) && !roleKnown;

  return (
    <aside className="fixed bottom-0 left-0 top-12 flex w-52 flex-col border-r border-chrome-line bg-chrome p-3">
      <nav aria-label="Main" className="flex flex-1 flex-col gap-6">
        {NAV_GROUPS.map((group) => ({ key: group[0].href, items: group.filter((item) => isVisible(item) || isReserved(item)) }))
          .filter(({ items }) => items.length > 0)
          .map(({ key, items }) => (
            <div key={key} className="flex flex-col gap-1">
              {items.map((item) =>
                isVisible(item) ? (
                  <NavLink
                    key={item.href}
                    item={item}
                    active={isActive(item)}
                    count={item.badge ? counts[item.badge] : 0}
                  />
                ) : (
                  <div key={item.href} aria-hidden="true" className="h-11" />
                ),
              )}
            </div>
          ))}
      </nav>
    </aside>
  );
}
