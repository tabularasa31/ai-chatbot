"use client";

import type { DocumentListItem, UrlSource } from "@/lib/api";

export type MixedRow =
  | { kind: "file"; item: DocumentListItem }
  | { kind: "url"; item: UrlSource };

export const POLLABLE_SOURCE_STATUSES = new Set(["queued", "indexing"]);

export function TypeBadge({ type }: { type: string }) {
  const styles: Record<string, string> = {
    file: "bg-indigo-400/10 text-indigo-500",
    url: "bg-amber-400/15 text-amber-600",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-[10px] font-mono font-medium ${styles[type] ?? "bg-slate-100 text-slate-600"}`}>
      {type}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    processing: "bg-yellow-100 text-yellow-800",
    embedding: "bg-blue-100 text-blue-800",
    ready: "bg-green-100 text-green-800",
    error: "bg-red-100 text-red-800",
    queued: "bg-amber-100 text-amber-800",
    indexing: "bg-sky-100 text-sky-800",
    paused: "bg-orange-100 text-orange-800",
    stale: "bg-yellow-100 text-yellow-800",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${styles[status] ?? "bg-slate-100 text-slate-800"}`}>
      {status}
    </span>
  );
}

export function KnowledgeTabs({
  activeTab,
  onChange,
}: {
  activeTab: "documents" | "profile" | "faq";
  onChange: (tab: "documents" | "profile" | "faq") => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1">
      <button
        className={`rounded-md px-3 py-1.5 text-sm ${activeTab === "documents" ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
        onClick={() => onChange("documents")}
      >
        Documents
      </button>
      <button
        className={`rounded-md px-3 py-1.5 text-sm ${activeTab === "profile" ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
        onClick={() => onChange("profile")}
      >
        Profile
      </button>
      <button
        className={`rounded-md px-3 py-1.5 text-sm ${activeTab === "faq" ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
        onClick={() => onChange("faq")}
      >
        FAQ
      </button>
    </div>
  );
}

export function formatSchedule(value: string) {
  if (value === "manual") return "Manual only";
  return value.charAt(0).toUpperCase() + value.slice(1);
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

export function stopRowClick(event: React.MouseEvent<HTMLElement>) {
  event.stopPropagation();
}

export function confidenceBadge(value?: number | null): string {
  if (value == null) return "Low";
  if (value >= 0.85) return "High";
  if (value >= 0.6) return "Medium";
  return "Low";
}
