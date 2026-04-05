"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Lock,
  MessageCircle,
  Send,
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
  botId: string;
  locale?: string | null;
  compact?: boolean;
  /** Optional UI rendered below each assistant bubble (e.g. eval rating). */
  renderBelowAssistant?: (ctx: ChatWidgetBelowAssistantContext) => ReactNode;
}

const CHAT9_SITE_URL = process.env.NEXT_PUBLIC_APP_URL || "https://getchat9.live";
const ESC_TICKET_RE = /\[\[escalation_ticket:([^\]]+)\]\]/;
const SESSION_STORAGE_TTL_MS = 24 * 60 * 60 * 1000;

function sessionStorageKey(botId: string): string {
  return `chat9:${botId}:session`;
}

function sessionUpdatedAtStorageKey(botId: string): string {
  return `chat9:${botId}:session_updated_at`;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function clearStoredSession(botId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(sessionStorageKey(botId));
    window.localStorage.removeItem(sessionUpdatedAtStorageKey(botId));
  } catch {
    // localStorage can be blocked in embedded/privacy-restricted contexts.
  }
}

function readStoredSession(botId: string): string | null {
  if (typeof window === "undefined") return null;
  let storedSessionId: string | null = null;
  let storedUpdatedAt: string | null = null;
  try {
    storedSessionId = window.localStorage.getItem(sessionStorageKey(botId));
    storedUpdatedAt = window.localStorage.getItem(sessionUpdatedAtStorageKey(botId));
  } catch {
    return null;
  }
  if (!storedSessionId || !storedUpdatedAt) {
    clearStoredSession(botId);
    return null;
  }
  if (!isUuid(storedSessionId)) {
    clearStoredSession(botId);
    return null;
  }
  const updatedAtMs = Number(storedUpdatedAt);
  if (!Number.isFinite(updatedAtMs) || Date.now() - updatedAtMs > SESSION_STORAGE_TTL_MS) {
    clearStoredSession(botId);
    return null;
  }
  return storedSessionId;
}

function persistSession(botId: string, sessionId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(sessionStorageKey(botId), sessionId);
    window.localStorage.setItem(sessionUpdatedAtStorageKey(botId), String(Date.now()));
  } catch {
    // Persistence is best-effort; widget can continue without browser storage.
  }
}

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

export function ChatWidget({
  botId,
  locale,
  compact = false,
  renderBelowAssistant,
}: ChatWidgetProps) {
  const [messages, setMessages] = useState<ChatWidgetMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [loading, setLoading] = useState(false);
  const [chatClosed, setChatClosed] = useState(false);
  const [activeTicket, setActiveTicket] = useState<string | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const localeParam = locale && locale.trim() ? locale.trim() : undefined;
  const trimmedInput = input.trim();
  const canSend = Boolean(trimmedInput) && !loading && !chatClosed;

  useEffect(() => {
    setSessionId(readStoredSession(botId) ?? "");
    setChatClosed(false);
    setActiveTicket(null);
  }, [botId]);

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
    if (ended) {
      setChatClosed(true);
      setSessionId("");
      clearStoredSession(botId);
    }
  };

  const handleStartNewChat = () => {
    setMessages([]);
    setInput("");
    setSessionId("");
    setChatClosed(false);
    setActiveTicket(null);
    clearStoredSession(botId);
    inputRef.current?.focus();
  };

  const handleSend = async () => {
    if (!canSend) return;

    setLoading(true);
    const userMessage = trimmedInput;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMessage }]);

    try {
      const params = new URLSearchParams({
        botId,
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
      if (data.chat_ended === true) {
        setSessionId("");
      } else {
        setSessionId(data.session_id);
        persistSession(botId, data.session_id);
      }
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

  return (
    <div className="flex h-full w-full min-h-0 flex-col overflow-hidden bg-white">
      {/* Header */}
      <div className="bg-[#1a1a1a] px-6 py-4 flex items-center gap-3 flex-shrink-0">
        <div className="w-12 h-12 rounded-full bg-gradient-to-br from-[#e879f9] to-[#a855f7] flex items-center justify-center flex-shrink-0">
          <MessageCircle size={22} className="text-white" />
        </div>
        <div>
          <div className="text-white font-medium">Chat9 Assistant</div>
          <div className="text-gray-400 text-sm">Online</div>
        </div>
      </div>

      {/* Messages */}
      <div
        ref={messagesRef}
        className={cn("min-h-0 flex-1 overflow-y-auto bg-white p-6", compact ? "text-[13px]" : "")}
      >
        {(activeTicket || chatClosed) && (
          <div className={cn("flex flex-wrap gap-2", compact ? "mb-3" : "mb-4")}>
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

        {chatClosed ? (
          <div className="mb-4 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 text-sm text-slate-600">
            <p className="font-medium text-slate-800">This conversation is closed.</p>
            <p className="mt-1">You can start a new chat for another question.</p>
            <button
              type="button"
              onClick={handleStartNewChat}
              className="mt-3 inline-flex items-center rounded-lg bg-[#a855f7] px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-[#9333ea]"
            >
              Start new chat
            </button>
          </div>
        ) : null}

        {messages.length === 0 && !loading ? (
          <div className="flex h-full min-h-[320px] items-start justify-center pt-14 text-center">
            <p className={cn("text-gray-400", compact ? "text-[13px]" : "text-sm")}>Ask anything about Chat9…</p>
          </div>
        ) : (
          <div className="space-y-5">
            {messages.map((msg, i) => {
              if (msg.role === "user") {
                return (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl px-4 py-2 bg-[#f3e8ff] text-gray-800">
                      <p className="whitespace-pre-wrap text-sm">{msg.content}</p>
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
                        "max-w-[85%] rounded-2xl px-4 py-2",
                        isError
                          ? "border border-[#FECACA] bg-[#FFF1F2] text-[#991B1B]"
                          : "bg-gray-100 text-gray-800",
                      )}
                    >
                      <p className="whitespace-pre-wrap text-sm">{msg.content}</p>
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
                <div className="rounded-2xl bg-gray-100 px-4 py-3">
                  <span className="flex h-6 items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-gray-300 animate-bounce [animation-delay:-0.3s]" />
                    <span className="h-1.5 w-1.5 rounded-full bg-gray-300 animate-bounce [animation-delay:-0.15s]" />
                    <span className="h-1.5 w-1.5 rounded-full bg-gray-300 animate-bounce" />
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>

      {/* Input area */}
      <div className={cn("border-t border-gray-200 bg-white px-4 sm:px-6", compact ? "py-3" : "py-4")}>
        <div className="flex items-center gap-3">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder={chatClosed ? "Chat closed" : "Type a message..."}
            disabled={loading || chatClosed}
            className="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-[15px] text-gray-900 placeholder:text-gray-400 outline-none transition focus:ring-2 focus:ring-[#a855f7] focus:border-transparent disabled:cursor-not-allowed disabled:text-gray-400"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            className="flex-shrink-0 p-3 bg-[#a855f7] hover:bg-[#9333ea] disabled:bg-gray-300 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
            aria-label="Send message"
          >
            <Send size={18} />
          </button>
        </div>

        <div className="mt-2 text-center">
          <a
            href={CHAT9_SITE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-gray-400 transition hover:text-gray-600"
          >
            Powered by Chat9
            <span aria-hidden="true">→</span>
          </a>
        </div>
      </div>
    </div>
  );
}
