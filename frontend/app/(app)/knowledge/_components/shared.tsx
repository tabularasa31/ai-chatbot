import type { DocumentListItem, UrlSource } from "@/lib/api";
import { parseApiDate } from "@/lib/format";
import type { SectionTab } from "@/components/ui/section-tabs";

export type MixedRow =
  | { kind: "file"; item: DocumentListItem }
  | { kind: "url"; item: UrlSource };

export const POLLABLE_SOURCE_STATUSES = new Set(["queued", "indexing"]);

export type KnowledgeTab = "documents" | "profile" | "faq";

export const KNOWLEDGE_TABS: readonly SectionTab<KnowledgeTab>[] = [
  { id: "documents", label: "Documents" },
  { id: "profile", label: "Profile" },
  { id: "faq", label: "FAQ" },
];

export const SCHEDULE_OPTIONS = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "manual", label: "Manual updates only" },
] as const;

export type SourceFormValues = { name: string; schedule: string; exclusions: string };

export function parseExclusions(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function formatMonthDay(iso: string): string {
  return parseApiDate(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function scheduleCaption(source: UrlSource): string {
  if (source.schedule === "manual") return "Manual updates only";
  const cadence = source.schedule === "daily" ? "Updates daily" : "Updates weekly";
  return source.next_crawl_at ? `${cadence}, next run ${formatMonthDay(source.next_crawl_at)}` : cadence;
}

export type StatusDot = "danger" | "warning" | null;

export function sourceStatus(status: string): { label: string; dot: StatusDot } {
  switch (status) {
    case "processing":
    case "embedding":
    case "indexing":
      return { label: "Indexing", dot: null };
    case "queued":
      return { label: "Queued", dot: null };
    case "ready":
      return { label: "Ready", dot: null };
    case "error":
      return { label: "Failed", dot: "danger" };
    case "paused":
      return { label: "Paused", dot: "warning" };
    case "stale":
      return { label: "Out of date", dot: "warning" };
    default:
      return { label: status, dot: null };
  }
}

export function isIndexing(status: string): boolean {
  return sourceStatus(status).label === "Indexing";
}

export function quickAnswerLabel(key: string) {
  const labels: Record<string, string> = {
    support_email: "Support email",
    documentation_url: "Documentation",
    pricing_url: "Pricing",
    trial_info: "Trial info",
    status_page_url: "Status page",
    support_chat: "Support chat",
  };
  return labels[key] ?? key;
}

export function confidenceLevel(value?: number | null): "High" | "Medium" | "Low" {
  if (value == null) return "Low";
  if (value >= 0.85) return "High";
  if (value >= 0.6) return "Medium";
  return "Low";
}
