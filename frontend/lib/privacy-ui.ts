export type OriginalContentStatus = "shown" | "available" | "removed";

export type PrivacyLogEvent = {
  id?: string;
  created_at: string;
  direction: string;
  entity_type: string;
  count: number;
  client_id: string;
  actor_user_id: string | null;
  action_path: string | null;
  chat_id: string | null;
  message_id: string | null;
};

export function getOriginalContentStatus({
  original,
  originalAvailable,
}: {
  original: string | null | undefined;
  originalAvailable: boolean;
}): OriginalContentStatus {
  if (original !== null && original !== undefined) {
    return "shown";
  }
  if (originalAvailable) {
    return "available";
  }
  return "removed";
}

export function getOriginalContentStatusFromFlags(
  hasVisible: boolean,
  hasAvailable: boolean
): OriginalContentStatus {
  if (hasVisible) {
    return "shown";
  }
  if (hasAvailable) {
    return "available";
  }
  return "removed";
}

export function getOriginalContentTextClassName(status: OriginalContentStatus): string {
  if (status === "shown") {
    return "text-emerald-700";
  }
  if (status === "available") {
    return "text-amber-700";
  }
  return "text-slate-500";
}

export function getOriginalContentBadgeClassName(status: OriginalContentStatus): string {
  if (status === "shown") {
    return "bg-emerald-50 text-emerald-700 border-emerald-200";
  }
  if (status === "available") {
    return "bg-amber-50 text-amber-800 border-amber-200";
  }
  return "bg-slate-100 text-slate-600 border-slate-200";
}

function csvCell(value: string | number | null | undefined): string {
  const stringValue = value == null ? "" : String(value);
  return `"${stringValue.replaceAll('"', '""')}"`;
}

export function buildPrivacyLogCsv(events: PrivacyLogEvent[]): string {
  const header = [
    "created_at_iso",
    "direction",
    "entity_type",
    "count",
    "client_id",
    "actor_user_id",
    "action_path",
    "chat_id",
    "message_id",
  ];
  const rows = events.map((event) =>
    [
      event.created_at,
      event.direction,
      event.entity_type,
      event.count,
      event.client_id,
      event.actor_user_id,
      event.action_path,
      event.chat_id,
      event.message_id,
    ]
      .map((cell) => csvCell(cell))
      .join(",")
  );
  return [header.map((cell) => csvCell(cell)).join(","), ...rows].join("\r\n") + "\r\n";
}

export function getPrivacyLogExportFilename(direction: string, sinceDays: string): string {
  const directionLabel = (direction || "all").replace(/[^a-z0-9_-]/gi, "_");
  const parsedDays = Number.parseInt(sinceDays, 10);
  const safeDays = Number.isFinite(parsedDays) && parsedDays > 0 ? String(parsedDays) : "0";
  return `privacy-log-${directionLabel}-${safeDays}d.csv`;
}
