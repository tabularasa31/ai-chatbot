"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  LifeBuoy,
  Lock,
  MessageCircle,
  SendHorizontal,
  Ticket,
} from "lucide-react";
import { cn } from "@/components/ui/utils";

export type ChatWidgetBelowAssistantContext = {
  messageIndex: number;
  userQuestion: string;
  assistantContent: string;
};

type ChatWidgetMessage = {
  role: "assistant" | "user" | "error";
  content: string;
};

interface ChatWidgetProps {
  clientId: string;
  locale?: string | null;
  compact?: boolean;
  /** Optional UI rendered below each assistant bubble (e.g. eval rating). */
  renderBelowAssistant?: (ctx: ChatWidgetBelowAssistantContext) => ReactNode;
}

const CHAT9_SITE_URL = process.env.NEXT_PUBLIC_APP_URL || "https://getchat9.live";
const ESC_TICKET_RE = /\[\[escalation_ticket:([^\]]+)\]\]/;

function parseEscalationTicket(content: string): string | null {
  const match = content.match(ESC_TICKET_RE);
  return match ? match[1].trim() : null;
}

function stripEscalationToken(content: string): string {
  return content.replace(/\[\[escalation_ticket:[^\]]+\]\]\s*/g, "").trim();
}

function formatApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === "object" && first !== null && "msg" in first) {
      return String((first as { msg: unknown }).msg);
    }
  }
  return fallback;
}

function precedingUserQuestion(messages: ChatWidgetMessage[], assistantIndex: number): string {
  for (let i = assistantIndex - 1; i >= 0; i -= 1) {
    if (messages[i].role === "user") return messages[i].content;
  }
  return "";
}

function widgetFooterText(chatClosed: boolean, sessionId: string): string {
  if (chatClosed) return "Диалог завершён. Начните новую сессию на сайте, чтобы продолжить.";
  if (sessionId) return "AI отвечает по базе знаний и может передать диалог в поддержку.";
  return "Задайте вопрос, и бот постарается помочь до подключения команды поддержки.";
}

export function ChatWidget({
  clientId,
  locale,
  compact = false,
  renderBelowAssistant,
}: ChatWidgetProps) {
  const [messages, setMessages] = useState<ChatWidgetMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingEscalate, setLoadingEscalate] = useState(false);
  const [chatClosed, setChatClosed] = useState(false);
  const [activeTicket, setActiveTicket] = useState<string | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);

  const localeParam = locale && locale.trim() ? locale.trim() : undefined;
  const trimmedInput = input.trim();
  const canSend = Boolean(trimmedInput) && !loading && !chatClosed;
  const canEscalate = Boolean(sessionId) && !chatClosed && !loadingEscalate && !loading;

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  const applyAssistantMessage = (raw: string, ended?: boolean) => {
    const ticket = parseEscalationTicket(raw);
    if (ticket) setActiveTicket(ticket);
    const display = stripEscalationToken(raw) || raw;
    setMessages((prev) => [...prev, { role: "assistant", content: display }]);
    if (ended) setChatClosed(true);
  };

  const handleSend = async () => {
    if (!canSend) return;

    setLoading(true);
    const userMessage = trimmedInput;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMessage }]);

    try {
      const params = new URLSearchParams({
        clientId,
        message: userMessage,
      });
      if (sessionId) params.set("session_id", sessionId);
      if (localeParam) params.set("locale", localeParam);

      const res = await fetch(`/widget/chat?${params}`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          formatApiDetail((err as { detail?: unknown }).detail, `API error: ${res.status}`),
        );
      }

      const data = (await res.json()) as {
        response: string;
        session_id: string;
        chat_ended?: boolean;
      };

      applyAssistantMessage(data.response, data.chat_ended === true);
      setSessionId(data.session_id);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: "error",
          content: error instanceof Error ? error.message : "Failed to send message",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleEscalate = async () => {
    if (!canEscalate) return;
    setLoadingEscalate(true);
    try {
      const params = new URLSearchParams({ clientId, session_id: sessionId });
      const res = await fetch(`/widget/escalate?${params}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trigger: "user_request", user_note: null }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          formatApiDetail((err as { detail?: unknown }).detail, `API error: ${res.status}`),
        );
      }
      const data = (await res.json()) as { message: string; ticket_number: string };
      const raw = data.message.includes("[[escalation_ticket:")
        ? data.message
        : `${data.message}\n\n[[escalation_ticket:${data.ticket_number}]]`;
      applyAssistantMessage(raw, false);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: "error",
          content: error instanceof Error ? error.message : "Could not reach support",
        },
      ]);
    } finally {
      setLoadingEscalate(false);
    }
  };

  return (
    <div className="flex h-full w-full min-h-0 flex-col overflow-hidden rounded-[28px] border border-[#DCE3EF] bg-white shadow-[0_28px_90px_rgba(15,23,42,0.12)]">
      <div className="bg-[#0A0A0F]/50 border-b border-[#1E1E2E] px-6 py-4 flex items-center gap-3 flex-shrink-0">
        <div className="w-10 h-10 bg-[#E879F9] rounded-full flex items-center justify-center">
          <MessageCircle size={20} className="text-[#0A0A0F]" />
        </div>
        <div>
          <div className="text-[#FAF5FF] font-medium">Chat9 Assistant</div>
          <div className="text-[#FAF5FF]/60 text-sm">Online</div>
        </div>
      </div>

      <div
        ref={messagesRef}
        className={cn("min-h-0 flex-1 overflow-y-auto bg-[#F8FAFC] p-6", compact ? "text-[13px]" : "")}
      >
        {(activeTicket || chatClosed) && (
          <div className={cn("mb-4 flex flex-wrap gap-2", compact ? "mb-3" : "mb-4")}>
            {activeTicket ? (
              <div className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600">
                <Ticket size={14} />
                Ticket {activeTicket}
              </div>
            ) : null}
            {chatClosed ? (
              <div className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600">
                <Lock size={14} />
                Chat closed
              </div>
            ) : null}
          </div>
        )}

        {messages.length === 0 && !loading ? (
          <div className="flex h-full min-h-[320px] items-start justify-center pt-14 text-center">
            <p className={cn("text-slate-400", compact ? "text-[13px]" : "text-sm")}>Ask anything about Chat9…</p>
          </div>
        ) : (
          <div className="space-y-5">
            {messages.map((msg, i) => {
              if (msg.role === "user") {
                return (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[85%] rounded-[18px] rounded-br-[8px] border border-slate-200 bg-slate-100 px-4 py-3 text-[15px] leading-6 text-slate-700 shadow-[0_10px_22px_rgba(148,163,184,0.08)]">
                      <p className="whitespace-pre-wrap">{msg.content}</p>
                    </div>
                  </div>
                );
              }

              const isError = msg.role === "error";
              return (
                <div key={i}>
                  <div className="flex items-end gap-3">
                    <div
                      className={cn(
                        "max-w-[85%] rounded-[18px] rounded-bl-[8px] px-4 py-3 text-[15px] leading-6 shadow-[0_10px_22px_rgba(148,163,184,0.08)]",
                        isError
                          ? "border border-[#FECACA] bg-[#FFF1F2] text-[#991B1B]"
                          : "border border-slate-200 bg-white text-slate-700",
                      )}
                    >
                      <p className="whitespace-pre-wrap">{msg.content}</p>
                    </div>
                  </div>

                  {msg.role === "assistant" && renderBelowAssistant ? (
                    <div className="ml-12 mt-3 max-w-[85%]">
                      {renderBelowAssistant({
                        messageIndex: i,
                        userQuestion: precedingUserQuestion(messages, i),
                        assistantContent: msg.content,
                      })}
                    </div>
                  ) : null}
                </div>
              );
            })}

            {loading ? (
              <div className="flex items-end gap-3">
                <div className="rounded-[18px] rounded-bl-[8px] border border-slate-200 bg-white px-4 py-3 shadow-[0_10px_22px_rgba(148,163,184,0.08)]">
                  <span className="flex h-6 items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#94A3B8] animate-bounce [animation-delay:-0.3s]" />
                    <span className="h-1.5 w-1.5 rounded-full bg-[#94A3B8] animate-bounce [animation-delay:-0.15s]" />
                    <span className="h-1.5 w-1.5 rounded-full bg-[#94A3B8] animate-bounce" />
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>

      <div className={cn("border-t border-[#E2E8F0] bg-[#F8FAFC] px-4 sm:px-6", compact ? "py-3" : "py-4")}>
        <div className="relative">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder={chatClosed ? "Chat closed" : "Type a message..."}
            disabled={loading || chatClosed}
            className="w-full rounded-[20px] border border-slate-200 bg-white px-4 py-3 pr-12 text-[15px] text-slate-700 outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-200 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            className="absolute inset-y-0 right-4 inline-flex items-center text-slate-400 transition hover:text-slate-600 disabled:cursor-not-allowed disabled:text-slate-300"
            aria-label="Send message"
          >
            <SendHorizontal size={18} />
          </button>
        </div>

        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <button
            type="button"
            onClick={handleEscalate}
            disabled={!canEscalate}
            className="inline-flex items-center justify-center gap-2 self-start rounded-full border border-[#E2D7EB] bg-white px-3.5 py-2 text-sm font-medium text-[#6E6880] transition hover:border-[#E879F9]/40 hover:bg-[#FBF3FE] hover:text-[#221F2D] disabled:cursor-not-allowed disabled:text-[#A7A1B5]"
          >
            <LifeBuoy size={15} />
            {loadingEscalate ? "Connecting..." : "Talk to support"}
          </button>

          <div className="flex flex-col gap-1 text-left sm:text-right">
            <p className="text-xs leading-5 text-[#7A748A]">{widgetFooterText(chatClosed, sessionId)}</p>
            <a
              href={CHAT9_SITE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-[#64748B] transition hover:text-[#475569]"
            >
              Powered by Chat9
              <span aria-hidden="true">→</span>
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
