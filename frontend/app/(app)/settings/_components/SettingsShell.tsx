"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { SectionTabs, type SectionLinkTab } from "@/components/ui/section-tabs";
import { useClientMe } from "@/hooks/useApi";

export type SettingsTabId = "bot" | "widget" | "team" | "seats";

const SETTINGS_TABS: readonly SectionLinkTab<SettingsTabId>[] = [
  { id: "bot", label: "Bot", href: "/settings" },
  { id: "widget", label: "Widget", href: "/widget-settings" },
  { id: "team", label: "Team", href: "/settings/members" },
  { id: "seats", label: "Seats", href: "/settings/seats" },
];

export function SettingsShell({
  active,
  children,
}: {
  active: SettingsTabId;
  children: ReactNode;
}) {
  const { data: client } = useClientMe();
  const blocked = client !== undefined && client.role !== "owner";

  return (
    <div className="max-w-2xl">
      <h1 className="text-h1 text-fg">Settings</h1>
      {blocked ? (
        <div className="mt-4 space-y-2">
          <p className="text-ui text-fg-muted">Only the account owner can open Settings.</p>
          <Link
            href="/inbox"
            className="inline-flex h-11 items-center rounded-control text-ui font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
          >
            Back to Inbox
          </Link>
        </div>
      ) : (
        <>
          <div className="mt-4">
            <SectionTabs label="Settings" tabs={SETTINGS_TABS} activeId={active} />
          </div>
          <div className="mt-6">{children}</div>
        </>
      )}
    </div>
  );
}
