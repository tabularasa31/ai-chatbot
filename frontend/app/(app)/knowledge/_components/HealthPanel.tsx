"use client";

import type { DocumentHealthStatus } from "@/lib/api";
import { Tooltip } from "@/components/ui/tooltip";

export function healthLabel(health: DocumentHealthStatus | null | undefined): string {
  if (health == null) return "Checking…";
  if (health.error || health.score === null) return "Unavailable";
  if (health.score >= 80) return "Good";
  if (health.score >= 50) return "Fair";
  return "Needs attention";
}

function summarizeSourceIssue({
  status,
  warning,
  error,
}: {
  status: string;
  warning?: string | null;
  error?: string | null;
}) {
  if (error) {
    return { label: "Needs attention", dotClass: "bg-red-500", note: error };
  }
  if (warning) {
    return { label: "Warning", dotClass: "bg-amber-400", note: warning };
  }
  if (status === "ready") {
    return { label: "Good", dotClass: "bg-emerald-500", note: "No active warnings." };
  }
  if (status === "paused" || status === "error") {
    return {
      label: "Needs attention",
      dotClass: "bg-red-500",
      note: "Source requires action before indexing can continue.",
    };
  }
  return {
    label: "Pending",
    dotClass: "bg-slate-300",
    note: "Source processing is still in progress.",
  };
}

export function HealthCell({
  health,
  isEmbedding,
}: {
  health: DocumentHealthStatus | null | undefined;
  isEmbedding?: boolean;
}) {
  if (isEmbedding) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-400">
        <span className="h-2 w-2 animate-pulse rounded-full bg-slate-300" />
        Pending
      </span>
    );
  }

  let dotClass = "bg-slate-300";
  const label = healthLabel(health);
  if (health != null && !health.error && health.score !== null) {
    if (health.score >= 80) dotClass = "bg-emerald-500";
    else if (health.score >= 50) dotClass = "bg-amber-400";
    else dotClass = "bg-red-500";
  }

  const warnings = health?.warnings ?? [];
  const checkedAt = health?.checked_at ? new Date(health.checked_at).toLocaleString() : null;
  const tooltipLines =
    health == null
      ? ["Health check is still running."]
      : health.error
        ? ["Health check is currently unavailable."]
        : warnings.length > 0
          ? warnings.map((warning) => warning.message)
          : ["No issues found."];

  return (
    <Tooltip
      className="z-10 text-xs text-slate-600"
      content={
        <>
          {tooltipLines.map((line) => (
            <span key={line} className="block">
              {line}
            </span>
          ))}
          {checkedAt && <span className="mt-2 block text-[10px] text-slate-300">Checked: {checkedAt}</span>}
        </>
      }
    >
      <span className={`h-2 w-2 rounded-full ${dotClass}`} />
      <span className="font-medium">{label}</span>
    </Tooltip>
  );
}

export function SourceHealthCell({
  status,
  warning,
  error,
}: {
  status: string;
  warning?: string | null;
  error?: string | null;
}) {
  const { label, dotClass, note } = summarizeSourceIssue({ status, warning, error });

  return (
    <Tooltip className="z-10 max-w-[240px] text-xs text-slate-600" content={note}>
      <span className={`h-2 w-2 rounded-full ${dotClass}`} />
      <span className="font-medium">{label}</span>
      {(warning || error) && <span className="max-w-[220px] truncate text-slate-400">{note}</span>}
    </Tooltip>
  );
}
