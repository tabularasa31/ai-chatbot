import type { Components } from "react-markdown";
import type { ChatWidgetMessage, WidgetSource } from "@chat9/widget-shared";
import { cn } from "./utils";
import { MarkdownBody } from "./MarkdownBody";
import { t as tString } from "./strings";
import type { ChatWidgetBelowAssistantContext } from "./ChatWidget";
import type { ReactNode } from "react";

function precedingUserQuestion(messages: ChatWidgetMessage[], assistantIndex: number): string {
  for (let i = assistantIndex - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.type === "user") return message.text;
  }
  return "";
}

export { precedingUserQuestion };

export function MessageBubble({
  msg,
  index,
  messages,
  operatorLabel,
  localeParam,
  markdownComponents,
  maybeOpenLinkSafety,
  renderBelowAssistant,
  handleLlmUnavailableRetry,
  handleLlmUnavailableEscalate,
}: {
  msg: ChatWidgetMessage;
  index: number;
  messages: ChatWidgetMessage[];
  operatorLabel: string;
  localeParam: string | undefined;
  markdownComponents: Components;
  maybeOpenLinkSafety: (rawUrl: string | undefined | null) => boolean;
  renderBelowAssistant?: (ctx: ChatWidgetBelowAssistantContext) => ReactNode;
  handleLlmUnavailableRetry: (messageId: string) => Promise<void>;
  handleLlmUnavailableEscalate: (messageId: string) => Promise<void>;
}) {
  if (msg.type === "system") {
    return (
      <div key={msg.id} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 text-sm text-slate-600">
        <p className="font-medium text-slate-800">New conversation</p>
      </div>
    );
  }

  if (msg.type === "user") {
    return (
      <div key={msg.id} className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl px-4 py-2 bg-[#f3e8ff] text-gray-800">
          <p className="whitespace-pre-wrap">{msg.text}</p>
        </div>
      </div>
    );
  }

  if (msg.type === "operator") {
    // Visually distinct from the bot on purpose: the visitor
    // has to be able to tell that a person is answering them.
    // The byline is the server's, already in the language the
    // conversation is being held in.
    return (
      <div key={msg.id} className="flex items-end gap-3">
        <div className="max-w-[85%] rounded-2xl border border-violet-200 bg-violet-50 px-4 py-2 text-gray-800">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-violet-600">
            {operatorLabel}
          </p>
          <MarkdownBody text={msg.text} components={markdownComponents} />
        </div>
      </div>
    );
  }

  if (msg.type === "llm_unavailable") {
    const showRetry = msg.failureState.retryable && msg.escalationStatus !== "done";
    const showEscalate = msg.failureState.can_escalate && msg.escalationStatus !== "done";
    const busy = msg.retryInProgress || msg.escalationStatus === "in_progress";
    return (
      <div key={msg.id} className="flex items-end gap-3">
        <div className="max-w-[85%] rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900">
          <p className="whitespace-pre-wrap">{msg.text}</p>
          {(showRetry || showEscalate) ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {showRetry ? (
                <button
                  type="button"
                  onClick={() => void handleLlmUnavailableRetry(msg.id)}
                  disabled={busy}
                  className="inline-flex items-center rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-900 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {tString(localeParam, "try_again_button")}
                </button>
              ) : null}
              {showEscalate ? (
                <button
                  type="button"
                  onClick={() => void handleLlmUnavailableEscalate(msg.id)}
                  disabled={busy}
                  className="inline-flex items-center rounded-lg bg-violet-500 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-violet-600 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {tString(localeParam, "contact_support_button")}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  const isError = msg.type === "error";
  const userQuestion = msg.type === "assistant" ? precedingUserQuestion(messages, index) : "";
  return (
    <div key={msg.id}>
      <div className="flex items-end gap-3">
        <div
          className={cn(
            "max-w-[85%] rounded-2xl px-4 py-2",
            isError ? "border border-[#FECACA] bg-[#FFF1F2] text-[#991B1B]" : "bg-gray-100 text-gray-800",
          )}
        >
          {isError ? (
            <p className="whitespace-pre-wrap">{msg.text}</p>
          ) : (
            <MarkdownBody text={msg.text} components={markdownComponents} />
          )}
        </div>
      </div>

      {msg.type === "assistant" && msg.sources && msg.sources.length > 0 && (
        <div className="ml-1 mt-1.5 flex flex-wrap gap-1.5">
          {msg.sources.map((src: WidgetSource) => {
            let hostname: string | null = null;
            try {
              hostname = new URL(src.url).hostname;
            } catch {
              /* skip favicon */
            }
            return (
              <a
                key={src.url}
                href={src.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(event) => {
                  if (maybeOpenLinkSafety(src.url)) {
                    event.preventDefault();
                  }
                }}
                className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-xs text-gray-500 hover:border-gray-300 hover:text-gray-700 transition-colors"
              >
                {hostname && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={`https://www.google.com/s2/favicons?domain=${hostname}&sz=16`}
                    alt=""
                    className="h-3 w-3"
                  />
                )}
                <span className="max-w-[140px] truncate">{src.title}</span>
                <svg className="h-2.5 w-2.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                  />
                </svg>
              </a>
            );
          })}
        </div>
      )}

      {msg.type === "assistant" && renderBelowAssistant && userQuestion.trim() ? (
        <div className="ml-12 mt-3 max-w-[85%]">
          {renderBelowAssistant({
            messageIndex: index,
            userQuestion,
            assistantContent: msg.text,
          })}
        </div>
      ) : null}
    </div>
  );
}
