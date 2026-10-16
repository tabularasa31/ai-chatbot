import type { GapAnalyzerResponse, GapItem, GapItemStatus } from "@/lib/api";
import type { SectionTab } from "@/components/ui/section-tabs";

export type GapTab = "visitor-questions" | "docs-coverage";
export type GapShow = "active" | "archived" | "all";

export const GAP_PANEL_ID = "gap-panel";

export const SHOW_OPTIONS: ReadonlyArray<{ value: GapShow; label: string }> = [
  { value: "active", label: "Open" },
  { value: "archived", label: "Archived" },
  { value: "all", label: "All" },
];

export const EMPTY_COPY: Record<GapShow, string> = {
  active: "No open gaps. New ones appear when visitors ask questions your documents don't answer.",
  archived: "Nothing archived yet.",
  all: "No gaps yet.",
};

export function gapTabs(data: GapAnalyzerResponse | null): readonly SectionTab<GapTab>[] {
  const count = (items: GapItem[] | undefined) => (items ? ` ${items.length}` : "");
  return [
    { id: "visitor-questions", label: `Visitor questions${count(data?.mode_b_items)}` },
    { id: "docs-coverage", label: `Docs coverage${count(data?.mode_a_items)}` },
  ];
}

export function itemsFor(tab: GapTab, data: GapAnalyzerResponse): GapItem[] {
  return tab === "visitor-questions" ? data.mode_b_items : data.mode_a_items;
}

export type CoverageText = { label: string; dot: "danger" | "warning" | "success" };

export function coverageText(item: GapItem): CoverageText | null {
  const percent =
    item.source === "mode_a" && item.coverage_score != null
      ? ` (about ${Math.round(item.coverage_score * 100)}% covered)`
      : "";
  if (item.classification === "uncovered") return { label: `Not covered${percent}`, dot: "danger" };
  if (item.classification === "partial") return { label: `Partly covered${percent}`, dot: "warning" };
  if (item.classification === "covered") return { label: `Covered${percent}`, dot: "success" };
  return null;
}

export function lifecycleWord(item: GapItem): string | null {
  const words: Record<GapItemStatus, string | null> = {
    active: null,
    drafting: "Drafting",
    in_review: "In review",
    resolved: item.published_faq_id ? "Published" : "Resolved",
    dismissed: "Dismissed",
    closed: "Closed",
    inactive: "Inactive",
  };
  return words[item.status];
}

export const isInDraftFlow = (item: GapItem) => item.status === "drafting" || item.status === "in_review";

export type ArchiveGroup = { id: string; title: string; caption: string | null; items: GapItem[] };

export function archiveGroups(items: GapItem[]): ArchiveGroup[] {
  const groups: ArchiveGroup[] = [
    { id: "dismissed", title: "Dismissed", caption: null, items: items.filter((i) => i.status === "dismissed") },
    {
      id: "closed",
      title: "Closed",
      caption: "Answered by your FAQ or docs",
      items: items.filter((i) => i.status === "closed" || i.status === "resolved"),
    },
    {
      id: "inactive",
      title: "Inactive",
      caption: "No new questions for a while",
      items: items.filter((i) => i.status === "inactive"),
    },
  ];
  return groups.filter((group) => group.items.length > 0);
}

export function recalculateWaitMinutes(retryAfterSeconds: number | null): number {
  return Math.max(1, Math.ceil((retryAfterSeconds ?? 0) / 60));
}
