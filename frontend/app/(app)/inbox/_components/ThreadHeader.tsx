import type { Thread } from "@/lib/api";
import { InlineNotice } from "@/components/ui/inline-notice";
import { answeredByEmail, heldBy, ticketSummary, visitorLabel } from "../_lib/labels";
import { StatePill } from "./StatePill";

export function ThreadHeader({ thread, viewerId }: { thread: Thread; viewerId: string | undefined }) {
  const { ticket } = thread;
  const held = heldBy(thread.chat.assigned_operator_id, thread.chat.assigned_operator_email, viewerId);
  const answered = answeredByEmail(ticket);
  return (
    <>
      <div className="shrink-0 space-y-1 border-b border-line p-4">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p dir="auto" className="min-w-0 break-words text-body font-semibold text-fg">
            {visitorLabel(thread)}
          </p>
          {thread.visitor_name && thread.visitor_email && (
            <span className="min-w-0 break-words text-caption text-fg-muted">{thread.visitor_email}</span>
          )}
          <StatePill state={thread.handoff_state} />
        </div>
        {held && <p className="text-ui text-fg-muted">{held}</p>}
        {ticket && (
          <p className="text-ui text-fg-muted">
            <span className="font-mono">{ticket.ticket_number}</span>
            {` · ${ticketSummary(ticket).join(" · ")}`}
          </p>
        )}
        {ticket?.user_note && (
          <p className="break-words text-ui text-fg">
            Visitor note: <span dir="auto">{ticket.user_note}</span>
          </p>
        )}
      </div>
      {answered && (
        <div className="shrink-0 px-4 pt-4">
          <InlineNotice>
            Answered by email from {answered.from} on {answered.at}. The reply went to the visitor, not into this
            conversation.
          </InlineNotice>
        </div>
      )}
    </>
  );
}
