import type { HandoffState, InboxRow, InboxTicket, ThreadMessage } from "@/lib/api";
import { parseApiDate } from "@/lib/format";

const MS_PER_MINUTE = 60_000;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;

export function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const date = parseApiDate(iso);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: sameYear ? undefined : "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function waitingFor(since: string, now: number): string {
  const minutes = Math.max(0, Math.floor((now - parseApiDate(since).getTime()) / MS_PER_MINUTE));
  if (minutes < 1) return "just now";
  if (minutes < MINUTES_PER_HOUR) return `${minutes} min`;
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  if (hours < HOURS_PER_DAY) return `${hours} h`;
  return `${Math.floor(hours / HOURS_PER_DAY)} d`;
}

export function visitorLabel(row: { visitor_name: string | null; visitor_email: string | null }): string {
  return row.visitor_name || row.visitor_email || "Anonymous visitor";
}

function localPart(address: string): string {
  return address.split("@")[0];
}

export function heldBy(
  operatorId: string | null,
  operatorEmail: string | null,
  viewerId: string | undefined,
): string | null {
  if (!operatorId) return null;
  if (operatorId === viewerId) return "Held by you";
  return operatorEmail ? `Held by ${localPart(operatorEmail)}` : null;
}

export type Attribution = { text: string; title?: string };

export function previewPrefix(row: InboxRow, viewerId: string | undefined): Attribution | null {
  if (row.last_message_role === "assistant") return { text: "Bot: " };
  if (row.last_message_role !== "operator" || viewerId === undefined) return null;
  if (row.last_message_author_id === viewerId) return { text: "You: " };
  const author = row.last_message_author_label;
  return author ? { text: `${localPart(author)}: `, title: author } : null;
}

export function operatorCaption(msg: ThreadMessage, viewerId: string | undefined): Attribution {
  if (viewerId === undefined) return { text: "Operator" };
  if (msg.author_id !== null && msg.author_id === viewerId) return { text: "You" };
  const author = msg.author_label;
  return author ? { text: localPart(author), title: author } : { text: "Operator" };
}

export function answeredByEmail(ticket: InboxTicket | null): { from: string; at: string } | null {
  if (!ticket?.forwarded_reply_at || !ticket.forwarded_reply_from) return null;
  return { from: ticket.forwarded_reply_from, at: formatWhen(ticket.forwarded_reply_at) };
}

export function rowMeta(row: InboxRow, viewerId: string | undefined, now: number): string {
  const { ticket } = row;
  const held = heldBy(row.assigned_operator_id, row.assigned_operator_email, viewerId);
  const answered = answeredByEmail(ticket);
  if (answered) return `Answered by email · ${answered.from} · ${answered.at}`;
  if (row.handoff_state === "waiting" && row.waiting_since) {
    return [`Waiting ${waitingFor(row.waiting_since, now)}`, held].filter(Boolean).join(" · ");
  }
  if (row.handoff_state === "live" && held) return held;
  if (ticket?.status === "resolved") return `Resolved · ${formatWhen(ticket.resolved_at)}`;
  if (ticket?.status === "auto_closed") return `Closed after inactivity · ${formatWhen(ticket.resolved_at)}`;
  return formatWhen(row.last_activity);
}

const TICKET_STATUS: Record<string, string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  auto_closed: "Closed after inactivity",
};

const TICKET_TRIGGER: Record<string, string> = {
  user_request: "Asked for a human",
  low_similarity: "No good answer found",
  no_documents: "No documents matched",
  answer_rejected: "Answer rejected",
  llm_unavailable: "Bot unavailable",
  llm_self_offer: "Bot offered a person",
  user_complaint: "Complained about support",
  loop_detected: "Conversation looping",
  clarify_loop_limit: "Too many clarifying questions",
};

function sentenceCase(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function ticketSummary(ticket: InboxTicket): string[] {
  return [
    TICKET_STATUS[ticket.status] ?? sentenceCase(ticket.status),
    `${sentenceCase(ticket.priority)} priority`,
    TICKET_TRIGGER[ticket.trigger] ?? sentenceCase(ticket.trigger),
  ];
}

export const PILL_STATES: Partial<Record<HandoffState, { label: string; className: string }>> = {
  waiting: { label: "Waiting", className: "bg-warning-subtle text-warning" },
  live: { label: "Live", className: "bg-success-subtle text-success" },
};
