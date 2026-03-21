"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";

interface ChatWidgetProps {
  clientId: string;
}

const CHAT9_SITE_URL =
  process.env.NEXT_PUBLIC_APP_URL || "https://getchat9.live";

export function ChatWidget({ clientId }: ChatWidgetProps) {
  const [messages, setMessages] = useState<
    Array<{ role: string; content: string }>
  >([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSend = async () => {
    if (!input.trim()) return;

    setLoading(true);
    const userMessage = input;
    setInput("");

    try {
      const params = new URLSearchParams({
        clientId,
        message: userMessage,
      });
      if (sessionId) params.set("session_id", sessionId);

      const res = await fetch(`/widget/chat?${params}`, { method: "POST" });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          (err as { detail?: string }).detail || `API error: ${res.status}`
        );
      }

      const data = (await res.json()) as {
        response: string;
        session_id: string;
      };

      setMessages((prev) => [
        ...prev,
        { role: "user", content: userMessage },
        { role: "assistant", content: data.response },
      ]);
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
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: "8px" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSend()}
          placeholder="Type a message..."
          disabled={loading}
          style={{
            flex: 1,
            padding: "10px 14px",
            border: "1px solid #d1d5db",
            borderRadius: "8px",
            fontSize: "14px",
          }}
        />
        <Button onClick={handleSend} disabled={loading}>
          {loading ? "Sending..." : "Send"}
        </Button>
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
