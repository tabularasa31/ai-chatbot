"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  CircleAlert,
  LifeBuoy,
  Lock,
  MessageCircle,
  SendHorizontal,
  Sparkles,
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
  badge?: string;
  title?: string;
  subtitle?: string;
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
  badge = "Chat9 Assistant",
  title = "Talk to support",
  subtitle = "Get grounded answers fast and escalate to a human when the conversation needs it.",
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
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-[28px] border border-[#D9E3F1] bg-white shadow-[0_28px_90px_rgba(15,23,42,0.12)]">
      <div className="border-b border-[#E7EEF8] px-4 pb-4 pt-4 sm:px-5">
        <div className="rounded-[24px] border border-white/80 bg-[linear-gradient(180deg,rgba(255,255,255,0.98)_0%,rgba(247,250,255,0.98)_100%)] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.95),0_16px_36px_rgba(148,163,184,0.12)]">
          <div className="flex items-start gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,#0F172A_0%,#2563EB_45%,#E879F9_100%)] text-white shadow-[0_16px_30px_rgba(37,99,235,0.25)]">
              <Sparkles size={18} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center rounded-full border border-[#DBEAFE] bg-[#EEF5FF] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-[#2563EB]">
                  {badge}
                </span>
                {sessionId ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-[#F8FAFC] px-2.5 py-1 text-[11px] font-medium text-[#475569]">
                    <span className="h-2 w-2 rounded-full bg-[#22C55E]" />
                    Session active
                  </span>
                ) : null}
              </div>
              <h2 className="text-lg font-semibold tracking-[-0.02em] text-[#0F172A] sm:text-[1.35rem]">
                {title}
              </h2>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-[#526071]">{subtitle}</p>
            </div>
          </div>

          {(activeTicket || chatClosed) && (
            <div className="mt-4 flex flex-wrap gap-2">
              {activeTicket ? (
                <div className="inline-flex items-center gap-2 rounded-full border border-[#BBF7D0] bg-[#ECFDF3] px-3 py-1.5 text-xs font-medium text-[#166534]">
                  <Ticket size={14} />
                  Ticket {activeTicket}
                </div>
              ) : null}
              {chatClosed ? (
                <div className="inline-flex items-center gap-2 rounded-full border border-[#E2E8F0] bg-[#F8FAFC] px-3 py-1.5 text-xs font-medium text-[#475569]">
                  <Lock size={14} />
                  Chat closed
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>

      <div
        ref={messagesRef}
        className="min-h-0 flex-1 overflow-y-auto bg-[linear-gradient(180deg,#FBFDFF_0%,#F7FAFF_100%)] px-4 py-5 sm:px-5"
      >
        {messages.length === 0 && !loading ? (
          <div className="flex h-full min-h-[320px] items-center justify-center">
            <div className="max-w-md rounded-[28px] border border-[#E3EBF5] bg-white/90 px-8 py-10 text-center shadow-[0_24px_60px_rgba(148,163,184,0.14)] backdrop-blur">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,#DBEAFE_0%,#F5D0FE_100%)] text-[#1D4ED8]">
                <MessageCircle size={24} />
              </div>
              <h3 className="mt-5 text-xl font-semibold tracking-[-0.02em] text-[#0F172A]">
                Start a conversation
              </h3>
              <p className="mt-2 text-sm leading-6 text-[#64748B]">
                Ask a clear question and the assistant will respond from your configured knowledge base.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            {messages.map((msg, i) => {
              if (msg.role === "user") {
                return (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[85%] rounded-[22px] rounded-br-[8px] bg-[linear-gradient(135deg,#1D4ED8_0%,#38BDF8_55%,#7C3AED_100%)] px-4 py-3 text-[15px] leading-6 text-white shadow-[0_22px_36px_rgba(37,99,235,0.22)]">
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
                        "flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl text-white shadow-[0_12px_28px_rgba(15,23,42,0.16)]",
                        isError
                          ? "bg-[linear-gradient(135deg,#DC2626_0%,#F97316_100%)]"
                          : "bg-[linear-gradient(135deg,#0F172A_0%,#2563EB_45%,#E879F9_100%)]",
                      )}
                    >
                      {isError ? <CircleAlert size={16} /> : <MessageCircle size={16} />}
                    </div>
                    <div
                      className={cn(
                        "max-w-[85%] rounded-[22px] rounded-bl-[8px] px-4 py-3 text-[15px] leading-6 shadow-[0_20px_48px_rgba(148,163,184,0.14)]",
                        isError
                          ? "border border-[#FECACA] bg-[#FFF1F2] text-[#991B1B]"
                          : "border border-[#DCE5F2] bg-white text-[#0F172A]",
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
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,#0F172A_0%,#2563EB_45%,#E879F9_100%)] text-white shadow-[0_12px_28px_rgba(15,23,42,0.16)]">
                  <MessageCircle size={16} />
                </div>
                <div className="rounded-[22px] rounded-bl-[8px] border border-[#DCE5F2] bg-white px-4 py-3 shadow-[0_20px_48px_rgba(148,163,184,0.14)]">
                  <span className="flex h-6 items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-[#94A3B8] animate-bounce [animation-delay:-0.3s]" />
                    <span className="h-2 w-2 rounded-full bg-[#94A3B8] animate-bounce [animation-delay:-0.15s]" />
                    <span className="h-2 w-2 rounded-full bg-[#94A3B8] animate-bounce" />
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>

      <div className="border-t border-[#E7EEF8] bg-[#FCFDFE] px-4 py-4 sm:px-5">
        <div className="flex gap-3">
          <div className="relative flex-1">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              placeholder={chatClosed ? "Chat closed" : "Type a message..."}
              disabled={loading || chatClosed}
              className="w-full rounded-[18px] border border-[#D6E1F0] bg-white px-4 py-3 pr-12 text-[15px] text-[#0F172A] shadow-[inset_0_1px_0_rgba(255,255,255,0.95)] outline-none transition focus:border-[#60A5FA] focus:ring-4 focus:ring-[#DBEAFE] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]"
            />
            <div className="pointer-events-none absolute inset-y-0 right-4 flex items-center text-[#94A3B8]">
              <SendHorizontal size={16} />
            </div>
          </div>
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            className="inline-flex items-center justify-center gap-2 rounded-[18px] bg-[linear-gradient(135deg,#0F172A_0%,#2563EB_45%,#E879F9_100%)] px-5 py-3 text-sm font-semibold text-white shadow-[0_20px_40px_rgba(37,99,235,0.24)] transition hover:-translate-y-0.5 hover:shadow-[0_24px_50px_rgba(37,99,235,0.3)] disabled:translate-y-0 disabled:cursor-not-allowed disabled:bg-[#CBD5E1] disabled:shadow-none"
          >
            {loading ? "Sending..." : "Send"}
          </button>
        </div>

        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <button
            type="button"
            onClick={handleEscalate}
            disabled={!canEscalate}
            className="inline-flex items-center justify-center gap-2 self-start rounded-full border border-[#D6E1F0] bg-white px-3.5 py-2 text-sm font-medium text-[#334155] transition hover:border-[#BFDBFE] hover:bg-[#F8FBFF] hover:text-[#0F172A] disabled:cursor-not-allowed disabled:text-[#94A3B8]"
          >
            <LifeBuoy size={15} />
            {loadingEscalate ? "Connecting..." : "Talk to support"}
          </button>

          <div className="flex flex-col gap-1 text-left sm:text-right">
            <p className="text-xs leading-5 text-[#64748B]">{widgetFooterText(chatClosed, sessionId)}</p>
            <a
              href={CHAT9_SITE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-[#2563EB] transition hover:text-[#1D4ED8]"
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
