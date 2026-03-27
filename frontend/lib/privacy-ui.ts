export type OriginalContentStatus = "shown" | "available" | "removed";

export type PrivacyLogEvent = {
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

type OriginalContentLabels = {
  shown: string;
  available: string;
  removed: string;
};

export function getOriginalContentStatus({
  original,
  originalAvailable,
}: {
  original: string | null | undefined;
  originalAvailable: boolean;
}): OriginalContentStatus {
  if (original) {
    return "shown";
  }
  if (originalAvailable) {
    return "available";
  }
  return "removed";
}

export function getOriginalContentLabel(
  status: OriginalContentStatus,
  labels: OriginalContentLabels
): string {
  return labels[status];
}

function csvCell(value: string | number | null | undefined): string {
  const stringValue = value == null ? "" : String(value);
  return `"${stringValue.replaceAll('"', '""')}"`;
}

export function buildPrivacyLogCsv(events: PrivacyLogEvent[]): string {
  const header = [
    "time",
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
  return [header.join(","), ...rows].join("\n");
}

export function getPrivacyLogExportFilename(direction: string, sinceDays: string): string {
  const directionLabel = direction || "all";
  return `privacy-log-${directionLabel}-${sinceDays}d.csv`;
}
