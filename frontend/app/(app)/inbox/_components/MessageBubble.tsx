import type { ThreadMessage } from "@/lib/api";
import { MarkdownMessage } from "@/components/ui/markdown-message";
import { cn } from "@/components/ui/utils";
import { formatTime } from "@/lib/format";
import { operatorCaption } from "../_lib/labels";

export function MessageBubble({ msg, viewerId }: { msg: ThreadMessage; viewerId: string | undefined }) {
  const isUser = msg.role === "user";
  const isOperator = msg.role === "operator";
  const caption = isUser ? { text: "Visitor" } : isOperator ? operatorCaption(msg, viewerId) : { text: "Bot" };
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "min-w-0 max-w-prose rounded-bubble px-4 py-2 text-ui text-fg",
          isUser && "bg-brand-subtle",
          isOperator && "border border-line bg-brand-subtle",
          !isUser && !isOperator && "bg-surface-alt",
        )}
      >
        <p className="truncate text-caption font-medium text-fg-muted" title={caption.title}>
          {caption.text}
        </p>
        {isUser ? (
          <p dir="auto" className="whitespace-pre-wrap break-words">
            {msg.content}
          </p>
        ) : (
          <div dir="auto">
            <MarkdownMessage text={msg.content} />
          </div>
        )}
        <p className="mt-1 text-caption tabular-nums text-fg-muted">{formatTime(msg.created_at)}</p>
      </div>
    </div>
  );
}
