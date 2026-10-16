"use client";

import type { ReactNode } from "react";
import { SectionTabs, sectionTabId } from "@/components/ui/section-tabs";
import { KNOWLEDGE_TABS, type KnowledgeTab } from "./shared";

const PANEL_ID = "knowledge-panel";

export function KnowledgeFrame({
  activeTab,
  onTabChange,
  children,
}: {
  activeTab: KnowledgeTab;
  onTabChange: (tab: KnowledgeTab) => void;
  children: ReactNode;
}) {
  return (
    <div className="max-w-7xl space-y-6">
      <h1 className="text-h1 text-fg">Knowledge</h1>
      <SectionTabs
        label="Knowledge"
        tabs={KNOWLEDGE_TABS}
        activeId={activeTab}
        onChange={onTabChange}
        panelId={PANEL_ID}
      />
      <div
        role="tabpanel"
        id={PANEL_ID}
        aria-labelledby={sectionTabId(PANEL_ID, activeTab)}
        className="space-y-6"
      >
        {children}
      </div>
    </div>
  );
}
