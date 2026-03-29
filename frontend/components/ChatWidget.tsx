"use client";

import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

export type ChatWidgetBelowAssistantContext = {
  messageIndex: number;
  userQuestion: string;
  assistantContent: string;
};

interface ChatWidgetProps {
  clientId: string;
  locale?: string | null;
  /** Optional UI rendered below each assistant bubble (e.g. eval rating). */
  renderBelowAssistant?: (ctx: ChatWidgetBelowAssistantContext) => ReactNode;
}

const CHAT9_SITE_URL =
  process.env.NEXT_PUBLIC_APP_URL || "https://getchat9.live";

const ESC_TICKET_RE = /\[\[escalation_ticket:([^\]]+)\]\]/;

function parseEscalationTicket(content: string): string | null {
  const m = content.match(ESC_TICKET_RE);
  return m ? m[1].trim() : null;
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

function precedingUserQuestion(
  messages: Array<{ role: string; content: string }>,
  assistantIndex: number
): string {
  for (let i = assistantIndex - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].content;
  }
  return "";
}

export function ChatWidget({ clientId, locale, renderBelowAssistant }: ChatWidgetProps) {
  const [messages, setMessages] = useState<
    Array<{ role: string; content: string }>
  >([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingEscalate, setLoadingEscalate] = useState(false);
  const [chatClosed, setChatClosed] = useState(false);
  const [activeTicket, setActiveTicket] = useState<string | null>(null);

  const localeParam =
    locale && locale.trim() ? locale.trim() : undefined;

  const applyAssistantMessage = (raw: string, ended?: boolean) => {
    const ticket = parseEscalationTicket(raw);
    if (ticket) setActiveTicket(ticket);
    const display = stripEscalationToken(raw) || raw;
    setMessages((prev) => [...prev, { role: "assistant", content: display }]);
    if (ended) setChatClosed(true);
  };

  const handleSend = async () => {
    if (!input.trim() || chatClosed) return;

    setLoading(true);
    const userMessage = input;
    setInput("");

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
          formatApiDetail(
            (err as { detail?: unknown }).detail,
            `API error: ${res.status}`
          )
        );
      }

      const data = (await res.json()) as {
        response: string;
        session_id: string;
        chat_ended?: boolean;
      };

      setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
      applyAssistantMessage(data.response, data.chat_ended === true);
      setSessionId(data.session_id);
    } catch (error) {
      console.error("Error:", error);
      setMessages((prev) => [
        ...prev,
        {
          role: "error",
          content:
            error instanceof Error ? error.message : "Failed to send message",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleEscalate = async () => {
    if (!sessionId || chatClosed || loadingEscalate) return;
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
          formatApiDetail(
            (err as { detail?: unknown }).detail,
            `API error: ${res.status}`
          )
        );
      }
      const data = (await res.json()) as { message: string; ticket_number: string };
      const raw = data.message.includes("[[escalation_ticket:")
        ? data.message
        : `${data.message}\n\n[[escalation_ticket:${data.ticket_number}]]`;
      applyAssistantMessage(raw, false);
    } catch (error) {
      console.error("Escalate error:", error);
      setMessages((prev) => [
        ...prev,
        {
          role: "error",
          content:
            error instanceof Error
              ? error.message
              : "Could not reach support",
        },
      ]);
    } finally {
      setLoadingEscalate(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        padding: "16px",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      {activeTicket && (
        <div
          style={{
            marginBottom: "12px",
            padding: "10px 12px",
            borderRadius: "8px",
            background: "#ecfdf5",
            border: "1px solid #6ee7b7",
            fontSize: "13px",
            color: "#065f46",
          }}
        >
          Support ticket: <strong>{activeTicket}</strong>
        </div>
      )}
      {chatClosed && (
        <div
          style={{
            marginBottom: "12px",
            padding: "10px 12px",
            borderRadius: "8px",
            background: "#f3f4f6",
            border: "1px solid #d1d5db",
            fontSize: "13px",
            color: "#374151",
          }}
        >
          This chat is closed. Start a new session from your site to continue.
        </div>
      )}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          marginBottom: "16px",
          border: "1px solid #e0e0e0",
          borderRadius: "8px",
          padding: "12px",
          background: "#fafafa",
        }}
      >
        {messages.length === 0 && (
          <div
            style={{
              color: "#999",
              textAlign: "center",
              paddingTop: "20px",
            }}
          >
            Start a conversation...
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            style={{
              marginBottom: "12px",
              textAlign: msg.role === "user" ? "right" : "left",
            }}
          >
            <div
              style={{
                background:
                  msg.role === "user"
                    ? "#2563eb"
                    : msg.role === "error"
                      ? "#dc3545"
                      : "#e9ecef",
                color: msg.role === "user" || msg.role === "error" ? "white" : "#000",
                padding: "8px 12px",
                borderRadius: "8px",
                display: "inline-block",
                maxWidth: "80%",
              }}
            >
              {msg.content}
            </div>
            {msg.role === "assistant" && renderBelowAssistant
              ? renderBelowAssistant({
                  messageIndex: i,
                  userQuestion: precedingUserQuestion(messages, i),
                  assistantContent: msg.content,
                })
              : null}
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: "8px" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSend()}
          placeholder={chatClosed ? "Chat closed" : "Type a message..."}
          disabled={loading || chatClosed}
          style={{
            flex: 1,
            padding: "10px 14px",
            border: "1px solid #d1d5db",
            borderRadius: "8px",
            fontSize: "14px",
          }}
        />
        <Button onClick={handleSend} disabled={loading || chatClosed}>
          {loading ? "Sending..." : "Send"}
        </Button>
      </div>

      <div className="mt-2 text-center">
        <button
          type="button"
          onClick={handleEscalate}
          disabled={!sessionId || chatClosed || loadingEscalate || loading}
          className="text-sm text-blue-600 hover:text-blue-800 underline disabled:text-gray-400 disabled:no-underline"
        >
          {loadingEscalate ? "Connecting…" : "Talk to support"}
        </button>
      </div>

      <div className="mt-2.5 text-center text-[11px] leading-snug">
        <a
          href={CHAT9_SITE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-gray-400 no-underline transition-colors hover:text-gray-500 hover:underline"
        >
          Powered by Chat9 →
        </a>
      </div>
    </div>
  );
}
