"use client";

import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { MessageCircle, Send } from "lucide-react";

const LANDING_DEMO_BOT_ID =
  process.env.NEXT_PUBLIC_LANDING_DEMO_BOT_ID?.trim() ??
  process.env.NEXT_PUBLIC_LANDING_DEMO_CLIENT_ID?.trim() ??
  "";

const ESC_TICKET_RE = /\[\[escalation_ticket:([^\]]+)\]\]/;

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

type Message = { role: "bot" | "user" | "error"; content: string };

function DemoChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [loading, setLoading] = useState(false);
  const [chatClosed, setChatClosed] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = messagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || chatClosed || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);

    try {
      const params = new URLSearchParams({
        botId: LANDING_DEMO_BOT_ID,
        message: text,
      });
      if (sessionId) params.set("session_id", sessionId);

      const res = await fetch(`/widget/chat?${params}`, { method: "POST" });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          formatApiDetail(
            (err as { detail?: unknown }).detail,
            `Error ${res.status}`
          )
        );
      }

      const data = (await res.json()) as {
        response: string;
        session_id: string;
        chat_ended?: boolean;
      };

      const ticket = data.response.match(ESC_TICKET_RE)?.[1] ?? null;
      const display = stripEscalationToken(data.response) || data.response;
      setMessages((prev) => [
        ...prev,
        { role: "bot", content: ticket ? `${display}\n\nTicket: ${ticket}` : display },
      ]);
      setSessionId(data.session_id);
      if (data.chat_ended) setChatClosed(true);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "error",
          content: err instanceof Error ? err.message : "Something went wrong",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {/* Messages — scroll happens inside this div only */}
      <div ref={messagesRef} className="flex-1 p-6 overflow-y-auto">
        <div className="space-y-4">
          {messages.length === 0 && !loading && (
            <p className="text-[#FAF5FF]/40 text-sm text-center pt-4">
              Ask anything about Chat9…
            </p>
          )}

          {messages.map((msg, i) => {
            if (msg.role === "user") {
              return (
                <div key={i} className="flex gap-3 justify-end">
                  <div className="bg-[#38BDF8] rounded-lg rounded-tr-none px-4 py-3 max-w-[80%]">
                    <p className="text-[#0A0A0F] whitespace-pre-wrap">{msg.content}</p>
                  </div>
                </div>
              );
            }

            const isError = msg.role === "error";
            return (
              <div key={i} className="flex gap-3">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                    isError ? "bg-red-500" : "bg-[#E879F9]"
                  }`}
                >
                  <MessageCircle size={16} className="text-[#0A0A0F]" />
                </div>
                <div
                  className={`rounded-lg rounded-tl-none px-4 py-3 max-w-[80%] ${
                    isError ? "bg-red-900/60 border border-red-700/40" : "bg-[#2D2D44]"
                  }`}
                >
                  <p className="text-[#FAF5FF] whitespace-pre-wrap">{msg.content}</p>
                </div>
              </div>
            );
          })}

          {loading && (
            <div className="flex gap-3">
              <div className="w-8 h-8 bg-[#E879F9] rounded-full flex items-center justify-center flex-shrink-0">
                <MessageCircle size={16} className="text-[#0A0A0F]" />
              </div>
              <div className="bg-[#2D2D44] rounded-lg rounded-tl-none px-4 py-3">
                <span className="flex gap-1 items-center h-5">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#FAF5FF]/40 animate-bounce [animation-delay:-0.3s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-[#FAF5FF]/40 animate-bounce [animation-delay:-0.15s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-[#FAF5FF]/40 animate-bounce" />
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-[#1E1E2E] p-4">
        {chatClosed ? (
          <p className="text-center text-[#FAF5FF]/40 text-sm py-1">
            Chat ended
          </p>
        ) : (
          <div className="flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              placeholder="Type your message..."
              disabled={loading}
              className="flex-1 bg-[#0A0A0F]/50 border border-[#1E1E2E] rounded-lg px-4 py-3 text-[#FAF5FF] placeholder-[#FAF5FF]/40 focus:outline-none focus:ring-2 focus:ring-[#E879F9] disabled:opacity-50"
            />
            <button
              onClick={handleSend}
              disabled={loading || !input.trim()}
              className="bg-[#E879F9] text-[#0A0A0F] px-5 py-3 rounded-lg hover:bg-[#f099fb] hover:scale-105 transition-all disabled:opacity-50 disabled:hover:scale-100"
            >
              <Send size={20} />
            </button>
          </div>
        )}
      </div>
    </>
  );
}

export function DemoBlock() {
  return (
    <section id="demo" className="max-w-7xl mx-auto px-6 py-20">
      {/* whileInView + once:true — анимация играет один раз при появлении и больше не сбрасывается */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.1 }}
        transition={{ duration: 0.6 }}
        className="text-center mb-12"
      >
        <h2 className="text-[#FAF5FF] text-4xl md:text-5xl mb-4">
          See Chat9 in action
        </h2>
        <p className="text-[#FAF5FF]/60 text-xl">
          Ask it anything about our docs
        </p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.1 }}
        transition={{ duration: 0.6, delay: 0.2 }}
        className="max-w-4xl mx-auto"
      >
        <div className="bg-[#12121A] backdrop-blur-sm border border-[#1E1E2E] rounded-2xl overflow-hidden h-[600px] flex flex-col">
          {/* Header */}
          <div className="bg-[#0A0A0F]/50 border-b border-[#1E1E2E] px-6 py-4 flex items-center gap-3 flex-shrink-0">
            <div className="w-10 h-10 bg-[#E879F9] rounded-full flex items-center justify-center">
              <MessageCircle size={20} className="text-[#0A0A0F]" />
            </div>
            <div>
              <div className="text-[#FAF5FF] font-medium">Chat9 Assistant</div>
              <div className="text-[#FAF5FF]/60 text-sm">Online</div>
            </div>
          </div>

          {LANDING_DEMO_BOT_ID ? (
            <DemoChat />
          ) : (
            <div className="flex-1 flex items-center justify-center px-6">
              <p className="text-[#FAF5FF]/40 text-sm text-center leading-relaxed max-w-sm">
                Live demo unavailable — set{" "}
                <code className="text-[#FAF5FF]/60 text-xs">
                  NEXT_PUBLIC_LANDING_DEMO_BOT_ID
                </code>{" "}
                to your public bot ID. Legacy{" "}
                <code className="text-[#FAF5FF]/60 text-xs">
                  NEXT_PUBLIC_LANDING_DEMO_CLIENT_ID
                </code>{" "}
                still works for compatibility.
              </p>
            </div>
          )}
        </div>
      </motion.div>
    </section>
  );
}
