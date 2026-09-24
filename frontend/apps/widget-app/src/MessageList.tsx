import type { Components } from "react-markdown";
import type { ChatWidgetMessage } from "@chat9/widget-shared";
import type { ReactNode } from "react";
import { cn } from "./utils";
import { MarkdownBody } from "./MarkdownBody";
import { LoadingIndicator } from "./LoadingIndicator";
import { MessageBubble } from "./MessageBubble";
import type { ChatWidgetBelowAssistantContext } from "./ChatWidget";

export function MessageList({
  messages,
  compact,
  operatorLabel,
  localeParam,
  markdownComponents,
  maybeOpenLinkSafety,
  renderBelowAssistant,
  handleLlmUnavailableRetry,
  handleLlmUnavailableEscalate,
  loading,
  streamingText,
  statusStage,
}: {
  messages: ChatWidgetMessage[];
  compact: boolean;
  operatorLabel: string;
  localeParam: string | undefined;
  markdownComponents: Components;
  maybeOpenLinkSafety: (rawUrl: string | undefined | null) => boolean;
  renderBelowAssistant?: (ctx: ChatWidgetBelowAssistantContext) => ReactNode;
  handleLlmUnavailableRetry: (messageId: string) => Promise<void>;
  handleLlmUnavailableEscalate: (messageId: string) => Promise<void>;
  loading: boolean;
  streamingText: string;
  statusStage: string | null;
}) {
  return (
    <div className={cn("space-y-5", compact ? "text-[13px]" : "text-sm")}>
      {messages.map((msg, i) => (
        <MessageBubble
          key={msg.id}
          msg={msg}
          index={i}
          messages={messages}
          operatorLabel={operatorLabel}
          localeParam={localeParam}
          markdownComponents={markdownComponents}
          maybeOpenLinkSafety={maybeOpenLinkSafety}
          renderBelowAssistant={renderBelowAssistant}
          handleLlmUnavailableRetry={handleLlmUnavailableRetry}
          handleLlmUnavailableEscalate={handleLlmUnavailableEscalate}
        />
      ))}

      {loading && streamingText ? (
        <div className="flex items-end gap-3">
          <div className="max-w-[85%] rounded-2xl bg-gray-100 px-4 py-2 text-gray-800">
            <MarkdownBody text={streamingText} components={markdownComponents} />
          </div>
        </div>
      ) : loading ? (
        <div className="flex items-end gap-3">
          <LoadingIndicator stage={statusStage} />
        </div>
      ) : null}
    </div>
  );
}
