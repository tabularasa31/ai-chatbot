"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";
import { MessageCircle, Send, Ticket } from "lucide-react";
import { cn } from "@/components/ui/utils";
import {
  appendSystemMarker,
  createTextMessage,
  getLastEndedMarkerIndex,
  type ChatWidgetMessage,
} from "@/lib/widget-conversation";

export type ChatWidgetBelowAssistantContext = {
  messageIndex: number;
  userQuestion: string;
  assistantContent: string;
};

interface ChatWidgetProps {
  botId: string;
  locale?: string | null;
  compact?: boolean;
  /** When provided, session init is called first to enable identified mode. */
  identityToken?: string | null;
  /** Required alongside identityToken for session init. */
  apiKey?: string | null;
  /** Optional UI rendered below each assistant bubble (e.g. eval rating). */
  renderBelowAssistant?: (ctx: ChatWidgetBelowAssistantContext) => ReactNode;
  /** Whether the widget panel is currently visible. Used to trigger scroll-to-bottom on reopen. */
  isOpen?: boolean;
}

/** Recursively extract plain text from a hast node (works after rehype-highlight). */
function hastToText(node: unknown): string {
  if (!node || typeof node !== "object") return "";
  const n = node as Record<string, unknown>;
  if (n.type === "text") return String(n.value ?? "");
  if (Array.isArray(n.children)) {
    return (n.children as unknown[]).map(hastToText).join("");
  }
  return "";
}

function CodeCopyButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  async function handleCopy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={copied ? "Copied!" : "Copy code"}
      title={copied ? "Copied!" : "Copy code"}
      className="absolute right-2.5 top-2.5 z-10 inline-flex h-7 w-7 items-center justify-center rounded-md border border-slate-700/50 bg-slate-800/80 text-slate-200 transition-colors hover:bg-slate-700/90"
    >
      {copied ? (
        <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4">
          <path d="M5 12.5 9.5 17 19 7.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4">
          <rect x="9" y="9" width="10" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
          <rect x="5" y="5" width="10" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.75" />
        </svg>
      )}
    </button>
  );
}

const MD_COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => (
    // eslint-disable-next-line jsx-a11y/anchor-has-content -- content is spread from react-markdown props at runtime
    <a {...props} target="_blank" rel="noopener noreferrer" />
  ),
  img: () => null,
  // Prevent react-markdown's default <pre> wrapper — our code component handles it.
  pre: ({ children }) => <>{children}</>,
  code: ({ node, className, children, ...props }) => {
    const isBlock = !!className;
    if (isBlock) {
      // Extract raw text from hast node for copy (children are already highlighted spans).
      const rawCode = hastToText(node).replace(/\n$/, "");
      return (
        <div className="relative my-2">
          <CodeCopyButton code={rawCode} />
          <pre className="overflow-x-auto whitespace-pre rounded-lg bg-slate-900 p-4 pr-12 text-xs text-slate-100">
            <code className={className}>{children}</code>
          </pre>
        </div>
      );
    }
    return (
      <code
        className="rounded bg-slate-700 px-1 py-0.5 font-mono text-xs text-slate-100"
        {...props}
      >
        {children}
      </code>
    );
  },
};

const CHAT9_SITE_URL = process.env.NEXT_PUBLIC_APP_URL || "https://getchat9.live";
const SESSION_STORAGE_TTL_MS = 24 * 60 * 60 * 1000;
const RETRYABLE_SESSION_ERROR_CODES = new Set([
  "session_invalid",
  "session_not_found",
  "session_forbidden",
  "session_closed",
]);

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

function formatApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (typeof detail === "object" && detail !== null && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === "object" && first !== null && "msg" in first) {
      return String((first as { msg: unknown }).msg);
    }
  }
  return fallback;
}

function apiErrorCode(detail: unknown): string | null {
  if (typeof detail === "object" && detail !== null && "code" in detail) {
    const code = (detail as { code?: unknown }).code;
    if (typeof code === "string" && code.trim()) return code;
  }
  return null;
}

function precedingUserQuestion(messages: ChatWidgetMessage[], assistantIndex: number): string {
  for (let i = assistantIndex - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.type === "user") return message.text;
  }
  return "";
}

export function ChatWidget({
  botId,
  locale,
  compact = false,
  identityToken,
  apiKey,
  renderBelowAssistant,
  isOpen = true,
}: ChatWidgetProps) {
  const [messages, setMessages] = useState<ChatWidgetMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionHydrated, setSessionHydrated] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [chatClosed, setChatClosed] = useState(false);
  const [activeTicket, setActiveTicket] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState<string>("");
  const messagesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const localeParam = locale && locale.trim() ? locale.trim() : undefined;
  const trimmedInput = input.trim();
  const canSend = Boolean(trimmedInput) && !loading && !chatClosed;

  useEffect(() => {
    setSessionHydrated(false);
    setHistoryLoaded(false);
    setChatClosed(false);
    setActiveTicket(null);

    const stored = readStoredSession(botId);

    if (identityToken && apiKey && !stored) {
      fetch("/api/widget-session/init", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey, identity_token: identityToken }),
      })
        .then((r) => r.json())
        .then((data: { session_id?: string }) => {
          if (data.session_id) {
            persistSession(botId, data.session_id);
            setSessionId(data.session_id);
          }
        })
        .catch(() => {
          // fall through to anonymous session
        })
        .finally(() => setSessionHydrated(true));
    } else {
      setSessionId(stored);
      setSessionHydrated(true);
    }
  }, [botId, identityToken, apiKey]);

  useEffect(() => {
    if (!sessionHydrated || !sessionId || historyLoaded) return;
    let cancelled = false;
    setLoading(true);
    const params = new URLSearchParams({ botId, session_id: sessionId });
    fetch(`/widget/history?${params}`)
      .then(async (r) => {
        if (r.status === 404) {
          // Session no longer exists on the backend — start fresh
          if (!cancelled) {
            clearStoredSession(botId);
            setSessionId(null);
          }
          return null;
        }
        if (!r.ok) {
          // Transient error (5xx, network) — keep session, silently skip history
          return null;
        }
        return r.json() as Promise<{
          messages: { role: string; content: string }[];
          chat_ended: boolean;
          ticket_number?: string | null;
        }>;
      })
      .then((data) => {
        if (cancelled || !data) return;
        if (data.messages.length > 0) {
          const hydrated = data.messages
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => createTextMessage(m.role as "user" | "assistant", m.content));
          setMessages(hydrated);
          if (data.ticket_number) setActiveTicket(data.ticket_number);
          if (data.chat_ended) {
            setChatClosed(true);
            setMessages((prev) => appendSystemMarker(prev, "conversation_ended"));
          }
        }
      })
      .catch(() => {
        // Network-level failure — keep session for next page load
      })
      .finally(() => {
        if (!cancelled) {
          setHistoryLoaded(true);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [sessionHydrated, sessionId, historyLoaded, botId]);

  useEffect(() => {
    if (!isOpen) return;
    const el = messagesRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, loading, isOpen]);

  const appendSystemMessage = useCallback((subtype: "conversation_ended" | "new_conversation") => {
    setMessages((prev) => {
      return appendSystemMarker(prev, subtype);
    });
  }, []);

  const handleChatEnded = useCallback(() => {
    setChatClosed(true);
    setSessionId(null);
    appendSystemMessage("conversation_ended");
    clearStoredSession(botId);
  }, [appendSystemMessage, botId]);

  const applyAssistantMessage = useCallback((
    payload: {
      text: string;
      chat_ended?: boolean;
      ticket_number?: string | null;
    },
  ) => {
    if (payload.ticket_number) setActiveTicket(payload.ticket_number);
    setMessages((prev) => [
      ...prev,
      createTextMessage("assistant", payload.text),
    ]);
    if (payload.chat_ended === true) {
      handleChatEnded();
    }
  }, [handleChatEnded]);

  const requestWidgetTurn = useCallback(async ({
    message,
    attemptSessionId,
    onChunk,
  }: {
    message: string;
    attemptSessionId: string | null;
    onChunk?: (partialText: string) => void;
  }) => {
    const params = new URLSearchParams({
      botId,
    });
    if (attemptSessionId) params.set("session_id", attemptSessionId);

    const res = await fetch(`/widget/chat?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        locale: localeParam,
      }),
    });

    if (!res.ok || !res.body) {
      const payload = (await res.json().catch(() => ({}))) as {
        detail?: unknown;
        text?: string;
        session_id?: string;
        chat_ended?: boolean;
      };
      return { res, payload };
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";
    const payload: {
      detail?: unknown;
      text?: string;
      session_id?: string;
      chat_ended?: boolean;
      ticket_number?: string | null;
    } = {};

    const handleEvent = (eventData: string) => {
      const raw = eventData.trim();
      if (!raw) return;
      let parsed: { type?: string; text?: string; session_id?: string; chat_ended?: boolean; ticket_number?: string; message?: string; code?: number };
      try {
        parsed = JSON.parse(raw);
      } catch {
        return;
      }
      if (parsed.type === "chunk" && typeof parsed.text === "string") {
        fullText += parsed.text;
        onChunk?.(fullText);
      } else if (parsed.type === "done") {
        payload.text = typeof parsed.text === "string" ? parsed.text : fullText;
        payload.session_id = parsed.session_id;
        payload.chat_ended = parsed.chat_ended;
        payload.ticket_number = parsed.ticket_number ?? null;
        if (typeof parsed.text === "string" && parsed.text !== fullText) {
          onChunk?.(parsed.text);
        }
      } else if (parsed.type === "error") {
        payload.detail = {
          code: parsed.code,
          message: parsed.message ?? "stream_error",
        };
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (value) {
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const dataLines = frame
            .split("\n")
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trimStart())
            .join("\n");
          if (dataLines) handleEvent(dataLines);
        }
      }
      if (done) break;
    }

    if (payload.detail !== undefined) {
      throw new Error(formatApiDetail(payload.detail, "Stream error"));
    }

    return { res, payload };
  }, [botId, localeParam]);

  const fetchGreeting = useCallback(async () => {
    const { res, payload } = await requestWidgetTurn({
      message: "",
      attemptSessionId: null,
    });
    if (!res.ok) {
      throw new Error(formatApiDetail(payload.detail, `API error: ${res.status}`));
    }
    const data = payload as {
      text: string;
      session_id: string;
      chat_ended?: boolean;
      ticket_number?: string | null;
    };
    applyAssistantMessage(data);
    if (data.chat_ended !== true) {
      setSessionId(data.session_id);
      persistSession(botId, data.session_id);
    }
  }, [applyAssistantMessage, botId, requestWidgetTurn]);

  useEffect(() => {
    // Wait for history fetch to complete (or determine there's no stored session)
    const needsHistoryFetch = sessionHydrated && sessionId && !historyLoaded;
    if (!sessionHydrated || needsHistoryFetch || sessionId || messages.length > 0 || loading || chatClosed) return;
    let cancelled = false;
    setLoading(true);
    void fetchGreeting()
      .catch((error) => {
        if (cancelled) return;
        setMessages((prev) => [
          ...prev,
          createTextMessage(
            "error",
            error instanceof Error ? error.message : "Failed to load greeting",
          ),
        ]);
      })
      .finally(() => {
        // Unconditionally reset loading: the React flush microtask triggered by
        // setMessages/setSessionId inside fetchGreeting runs before .finally(),
        // so `cancelled` is already true here even on success. Keeping the guard
        // would leave loading stuck at true and the input permanently disabled.
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatClosed, fetchGreeting, historyLoaded, messages.length, sessionHydrated, sessionId]);

  const handleStartNewChat = useCallback(() => {
    setInput("");
    setSessionId(null);
    setChatClosed(false);
    setActiveTicket(null);
    appendSystemMessage("new_conversation");
    clearStoredSession(botId);
    inputRef.current?.focus();
    setLoading(true);
    void fetchGreeting()
      .catch((error) => {
        setMessages((prev) => [
          ...prev,
          createTextMessage(
            "error",
            error instanceof Error ? error.message : "Failed to load greeting",
          ),
        ]);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [appendSystemMessage, botId, fetchGreeting]);

  const lastEndedMarkerIndex = useMemo(
    () => getLastEndedMarkerIndex(messages),
    [messages],
  );

  const handleSend = async () => {
    const userMessage = trimmedInput;
    if (!userMessage || !canSend || chatClosed) return;

    setLoading(true);
    setInput("");
    setStreamingText("");
    setMessages((prev) => [...prev, createTextMessage("user", userMessage)]);

    try {
      let { res, payload } = await requestWidgetTurn({
        message: userMessage,
        attemptSessionId: sessionId,
        onChunk: setStreamingText,
      });
      let detail = payload.detail;
      let code = apiErrorCode(detail);
      if (!res.ok && sessionId && code && RETRYABLE_SESSION_ERROR_CODES.has(code)) {
        clearStoredSession(botId);
        setSessionId(null);
        setStreamingText("");
        ({ res, payload } = await requestWidgetTurn({
          message: userMessage,
          attemptSessionId: null,
          onChunk: setStreamingText,
        }));
        detail = payload.detail;
        code = apiErrorCode(detail);
      }

      if (!res.ok) {
        throw new Error(formatApiDetail(detail, `API error: ${res.status}`));
      }

      const data = payload as {
        text: string;
        session_id: string;
        chat_ended?: boolean;
      };

      applyAssistantMessage(data);
      if (data.chat_ended !== true) {
        setSessionId(data.session_id);
        persistSession(botId, data.session_id);
      }
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        createTextMessage(
          "error",
          error instanceof Error ? error.message : "Failed to send message",
        ),
      ]);
    } finally {
      setStreamingText("");
      setLoading(false);
    }
  };

  const handleSendClick = () => {
    void handleSend();
  };

  return (
    <div className="flex h-full w-full min-h-0 flex-col overflow-hidden bg-white">
      {/* Header */}
      <div className="bg-nd-base-alt px-6 py-4 flex items-center gap-3 flex-shrink-0">
        <div className="w-12 h-12 rounded-full bg-gradient-to-br from-nd-accent to-violet-500 flex items-center justify-center flex-shrink-0">
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
        {activeTicket ? (
          <div className={cn("flex flex-wrap gap-2", compact ? "mb-3" : "mb-4")}>
            <div className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600">
              <Ticket size={14} />
              Ticket {activeTicket}
            </div>
          </div>
        ) : null}

        {messages.length === 0 && !loading ? (
          <div className="flex h-full min-h-[320px] items-start justify-center pt-14 text-center">
            <p className={cn("text-gray-400", compact ? "text-[13px]" : "text-sm")}>Ask anything about Chat9…</p>
          </div>
        ) : (
          <div className="space-y-5">
            {messages.map((msg, i) => {
                if (msg.type === "system") {
                  const isEnded = msg.subtype === "conversation_ended";
                  const isLastEndedMarker = isEnded && i === lastEndedMarkerIndex;
                  return (
                    <div key={msg.id} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 text-sm text-slate-600">
                      <p className="font-medium text-slate-800">
                        {isEnded ? "This conversation has ended." : "New conversation"}
                      </p>
                      {isEnded && isLastEndedMarker && chatClosed ? (
                        <button
                          type="button"
                          onClick={handleStartNewChat}
                          className="mt-3 inline-flex items-center rounded-lg bg-violet-500 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-600"
                        >
                          Start new chat
                        </button>
                      ) : null}
                    </div>
                  );
                }

                if (msg.type === "user") {
                  return (
                    <div key={msg.id} className="flex justify-end">
                      <div className="max-w-[85%] rounded-2xl px-4 py-2 bg-[#f3e8ff] text-gray-800">
                        <p className="whitespace-pre-wrap text-sm">{msg.text}</p>
                      </div>
                    </div>
                  );
                }

                const isError = msg.type === "error";
                const userQuestion = msg.type === "assistant" ? precedingUserQuestion(messages, i) : "";
                return (
                  <div key={msg.id}>
                    <div className="flex items-end gap-3">
                      <div
                        className={cn(
                          "max-w-[85%] rounded-2xl px-4 py-2",
                          isError
                            ? "border border-[#FECACA] bg-[#FFF1F2] text-[#991B1B]"
                            : "bg-gray-100 text-gray-800",
                        )}
                      >
                        {isError ? (
                          <p className="whitespace-pre-wrap text-sm">{msg.text}</p>
                        ) : (
                          <div className="prose prose-sm max-w-none text-gray-800 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              rehypePlugins={[rehypeHighlight]}
                              components={MD_COMPONENTS}
                            >
                              {msg.text}
                            </ReactMarkdown>
                          </div>
                        )}
                      </div>
                    </div>

                    {msg.type === "assistant" && renderBelowAssistant && userQuestion.trim() ? (
                      <div className="ml-12 mt-3 max-w-[85%]">
                        {renderBelowAssistant({
                          messageIndex: i,
                          userQuestion,
                          assistantContent: msg.text,
                        })}
                      </div>
                    ) : null}
                  </div>
                );
            })}

            {loading && streamingText ? (
              <div className="flex items-end gap-3">
                <div className="max-w-[85%] rounded-2xl bg-gray-100 px-4 py-2 text-gray-800">
                  <div className="prose prose-sm max-w-none text-gray-800 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      rehypePlugins={[rehypeHighlight]}
                      components={MD_COMPONENTS}
                    >
                      {streamingText}
                    </ReactMarkdown>
                  </div>
                </div>
              </div>
            ) : loading ? (
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
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder={chatClosed ? "Start a new chat to ask another question" : "Type a message..."}
            disabled={loading || chatClosed}
            className="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-[15px] text-gray-900 placeholder:text-gray-400 outline-none transition focus:ring-2 focus:ring-violet-500 focus:border-transparent disabled:cursor-not-allowed disabled:text-gray-400"
          />
          <button
            type="button"
            onClick={handleSendClick}
            disabled={!canSend}
            className="flex-shrink-0 p-3 bg-violet-500 hover:bg-violet-600 disabled:bg-gray-300 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
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
