import type { Thread } from "@/lib/api";
import { InlineNotice } from "@/components/ui/inline-notice";
import { answeredByEmail, heldBy, ticketSummary, visitorLabel } from "../_lib/labels";
import { StatePill } from "./StatePill";

export function ThreadHeader({ thread, viewerId }: { thread: Thread; viewerId: string | undefined }) {
  const { ticket } = thread;
  const held = heldBy(thread.chat.assigned_operator_id, thread.chat.assigned_operator_email, viewerId);
  const answered = answeredByEmail(ticket);
  const statusSegments = [
    held && <span key="held" title={held.title}>{held.text}</span>,
    ticket && <span key="ticket" className="font-mono">{ticket.ticket_number}</span>,
    ...(ticket ? ticketSummary(ticket).map((segment) => <span key={segment}>{segment}</span>) : []),
  ].filter(Boolean);
  return (
    <div className="shrink-0 border-b border-line p-4">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p dir="auto" className="min-w-0 break-anywhere text-body font-semibold text-fg">
            {visitorLabel(thread)}
          </p>
          {thread.visitor_name && thread.visitor_email && (
            <span className="min-w-0 break-anywhere text-caption text-fg-muted">{thread.visitor_email}</span>
          )}
          <StatePill state={thread.handoff_state} />
        </div>
        <p className="min-h-5 break-anywhere text-ui text-fg-muted">
          {statusSegments.flatMap((segment, index) => (index === 0 ? [segment] : [" · ", segment]))}
        </p>
        {ticket?.user_note && (
          <p className="break-anywhere text-ui text-fg">
            Visitor note: <span dir="auto">{ticket.user_note}</span>
          </p>
        )}
      </div>
      {answered && (
        <div className="mt-3">
          <InlineNotice singleLine>{`Answered by email · ${answered.at} · ${answered.from}`}</InlineNotice>
        </div>
      )}
    </div>
  );
}
