"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { type Components } from "react-markdown";
import "highlight.js/styles/github-dark.css";
import { MessageCircle, Send, Ticket } from "lucide-react";
import { cn, withUtm } from "./utils";
import { LinkSafetyModal } from "./LinkSafetyModal";
import {
  createLlmUnavailableMessage,
  createTextMessage,
  type ChatWidgetMessage,
  type HandoffState,
  type UserHints,
  type WidgetSource,
} from "@chat9/widget-shared";
import { t as tString } from "./strings";
import { clearStoredSession, deriveStorageUserId, persistSession, readStoredSession } from "./session-storage";
import { apiErrorCode, formatApiDetail, requestWidgetTurn } from "./api/stream";
import { useWidgetHistory } from "./useWidgetHistory";
import { useOperatorPolling } from "./useOperatorPolling";
import { MessageList } from "./MessageList";

export type ChatWidgetBelowAssistantContext = {
  messageIndex: number;
  userQuestion: string;
  assistantContent: string;
};

type WidgetLinkSafetyLabels = {
  title: string;
  body: string;
  continue_label: string;
  cancel_label: string;
};

type WidgetConfig = {
  link_safety_enabled: boolean;
  allowed_domains: string[];
  link_safety_labels: WidgetLinkSafetyLabels;
};

type PendingExternalLink = {
  url: string;
  hostname: string;
};

interface ChatWidgetProps {
  botId: string;
  locale?: string | null;
  compact?: boolean;
  /** Untrusted personalization hints from the tenant frontend. Triggers
   *  a session-init call so the backend can attach them to the chat. */
  hints?: UserHints | null;
  /** Optional UI rendered below each assistant bubble (e.g. eval rating). */
  renderBelowAssistant?: (ctx: ChatWidgetBelowAssistantContext) => ReactNode;
  /** Whether the widget panel is currently visible. Used to trigger scroll-to-bottom on reopen. */
  isOpen?: boolean;
  /** Origin for API calls (e.g. "https://getchat9.live"). No trailing slash. */
  apiBase: string;
  /** Marketing site URL for the "Powered by Chat9" footer link. */
  siteUrl?: string;
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

const DEFAULT_SITE_URL = "https://getchat9.live";
const RETRYABLE_SESSION_ERROR_CODES = new Set([
  "session_invalid",
  "session_not_found",
  "session_forbidden",
]);

function normalizeAllowedDomain(domain: string): string | null {
  const value = domain
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .split(/[/?#]/, 1)[0]
    .split(":", 1)[0]
    .replace(/^\*\./, "")
    .replace(/\.$/, "");
  return value && value.includes(".") ? value : null;
}

function hostnameAllowed(hostname: string, allowedDomains: string[]): boolean {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  return allowedDomains.some((domain) => {
    const normalized = normalizeAllowedDomain(domain);
    return normalized ? host === normalized || host.endsWith(`.${normalized}`) : false;
  });
}

export function ChatWidget({
  botId,
  locale,
  compact = false,
  hints,
  renderBelowAssistant,
  isOpen = true,
  apiBase,
  siteUrl = DEFAULT_SITE_URL,
}: ChatWidgetProps) {
  const [messages, setMessages] = useState<ChatWidgetMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionHydrated, setSessionHydrated] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeTicket, setActiveTicket] = useState<string | null>(null);
  // Who is answering, as the server sees it: "bot" (nobody has escalated),
  // "waiting" (an open request nobody has picked up) or "live" (a human is in
  // the conversation). Drives the poll cadence and nothing else — the third
  // state is derived server-side, never stored.
  const [handoffState, setHandoffState] = useState<HandoffState>("bot");
  // Byline above a human's reply, localized server-side into the language the
  // conversation is being held in. English until the server says otherwise.
  const [operatorLabel, setOperatorLabel] = useState("Operator");
  // Cursor for /widget/messages: the id of the last message this widget knows
  // about. A ref rather than state — polling reads it and writes it, and a
  // re-render per poll would be pure churn.
  const cursorRef = useRef<string | null>(null);
  const pollInFlightRef = useRef(false);
  // Mirror of the ticket the poll was dispatched for. Callbacks read it
  // without taking it as a dependency, which would rebuild them on every poll.
  const activeTicketRef = useRef<string | null>(null);
  // The newest operator reply rendered but not yet reported as read. Reported
  // only while the panel is open in a visible tab; until then it waits here,
  // and the server mails the reply to the visitor if the wait outlasts its
  // grace period.
  const [pendingReadId, setPendingReadId] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState<string>("");
  const [statusStage, setStatusStage] = useState<string | null>(null);
  const [widgetConfig, setWidgetConfig] = useState<WidgetConfig | null>(null);
  const [pendingExternalLink, setPendingExternalLink] = useState<PendingExternalLink | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Tracks the storage user_id derived from the current hints so other effects can use it.
  const userIdRef = useRef<string | null>(null);

  const localeParam = locale && locale.trim() ? locale.trim() : undefined;
  const trimmedInput = input.trim();
  const canSend = Boolean(trimmedInput) && !loading;

  const linkSafetyLabels = widgetConfig?.link_safety_labels ?? {
    title: "Open external link?",
    body: "You are going to {hostname}. Continue?",
    continue_label: "Open",
    cancel_label: "Cancel",
  };

  const maybeOpenLinkSafety = useCallback((rawUrl: string | undefined | null): boolean => {
    if (!rawUrl || !widgetConfig?.link_safety_enabled) return false;
    const trimmedUrl = rawUrl.trim();
    if (
      !trimmedUrl ||
      trimmedUrl.startsWith("#") ||
      trimmedUrl.startsWith("/") ||
      trimmedUrl.startsWith("mailto:") ||
      trimmedUrl.startsWith("tel:")
    ) {
      return false;
    }

    try {
      const url = new URL(trimmedUrl, window.location.href);
      if (url.protocol !== "http:" && url.protocol !== "https:") return false;
      if (hostnameAllowed(url.hostname, widgetConfig.allowed_domains ?? [])) return false;
      setPendingExternalLink({ url: url.href, hostname: url.hostname });
      return true;
    } catch {
      return false;
    }
  }, [widgetConfig]);

  const markdownComponents = useMemo<Components>(() => ({
    ...MD_COMPONENTS,
    a: ({ node: _node, href, onClick, ...props }) => (
      // eslint-disable-next-line jsx-a11y/anchor-has-content -- content is spread from react-markdown props at runtime
      <a
        {...props}
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(event) => {
          if (maybeOpenLinkSafety(href)) {
            event.preventDefault();
            return;
          }
          onClick?.(event);
        }}
      />
    ),
  }), [maybeOpenLinkSafety]);

  useEffect(() => {
    const userId = deriveStorageUserId(hints);
    userIdRef.current = userId;

    setSessionHydrated(false);
    setHistoryLoaded(false);
    setActiveTicket(null);
    setHandoffState("bot");
    cursorRef.current = null;

    // Purge the legacy shared-key session (pre-user-scoped namespacing) so stale
    // cross-tenant data left in existing browsers is never shown again.
    if (userId) clearStoredSession(botId);

    const stored = readStoredSession(botId, userId);

    if (hints && !stored) {
      fetch(`${apiBase}/widget/session/init`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bot_id: botId, user_hints: hints }),
      })
        .then((r) => r.json())
        .then((data: { session_id?: string }) => {
          if (data.session_id) {
            persistSession(botId, data.session_id, userId);
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
  }, [botId, hints, apiBase]);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ bot_id: botId });
    if (localeParam) params.set("locale", localeParam);

    fetch(`${apiBase}/widget/config?${params}`)
      .then(async (r) => {
        if (!r.ok) return null;
        return r.json() as Promise<WidgetConfig>;
      })
      .then((data) => {
        if (!cancelled) setWidgetConfig(data);
      })
      .catch(() => {
        if (!cancelled) setWidgetConfig(null);
      });

    return () => {
      cancelled = true;
    };
  }, [botId, localeParam, apiBase]);

  useEffect(() => {
    if (!isOpen) return;
    const el = messagesRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, loading, isOpen]);

  useEffect(() => {
    activeTicketRef.current = activeTicket;
  }, [activeTicket]);

  const applyAssistantMessage = useCallback((
    payload: {
      text: string;
      ticket_number?: string | null;
      sources?: WidgetSource[];
    },
  ) => {
    if (payload.ticket_number) setActiveTicket(payload.ticket_number);
    setMessages((prev) => [
      ...prev,
      createTextMessage("assistant", payload.text, payload.sources),
    ]);
  }, []);

  // attemptSessionId is null for a brand-new session; after conversation
  // rotation it carries the existing session so the greeting opens the new
  // conversation server-side instead of minting another session.
  const fetchGreeting = useCallback(async (attemptSessionId: string | null = null) => {
    const { res, payload } = await requestWidgetTurn({
      apiBase,
      botId,
      locale: localeParam,
      message: "",
      attemptSessionId,
    });
    if (!res.ok) {
      throw new Error(formatApiDetail(payload.detail, `API error: ${res.status}`));
    }
    const data = payload as {
      text: string;
      session_id: string;
      ticket_number?: string | null;
      sources?: WidgetSource[];
    };
    applyAssistantMessage(data);
    setSessionId(data.session_id);
    persistSession(botId, data.session_id, userIdRef.current);
  }, [applyAssistantMessage, apiBase, botId, localeParam]);

  useWidgetHistory({
    sessionHydrated,
    sessionId,
    historyLoaded,
    botId,
    apiBase,
    refs: { userIdRef, cursorRef },
    fetchGreeting,
    setters: {
      setSessionId,
      setHandoffState,
      setOperatorLabel,
      setMessages,
      setActiveTicket,
      setPendingReadId,
      setHistoryLoaded,
      setLoading,
    },
  });

  useEffect(() => {
    // Wait for history fetch to complete (or determine there's no stored session)
    const needsHistoryFetch = sessionHydrated && sessionId && !historyLoaded;
    if (!sessionHydrated || needsHistoryFetch || sessionId || messages.length > 0 || loading) return;
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
  }, [fetchGreeting, historyLoaded, messages.length, sessionHydrated, sessionId]);

  useOperatorPolling({
    apiBase,
    botId,
    sessionId,
    sessionHydrated,
    historyLoaded,
    handoffState,
    activeTicket,
    isOpen,
    pendingReadId,
    refs: { cursorRef, activeTicketRef, pollInFlightRef },
    setters: { setHandoffState, setOperatorLabel, setActiveTicket, setMessages, setPendingReadId, setHistoryLoaded },
  });

  /** Send a user message through /widget/chat and apply the response.
   *  Used both by the input-area send button and by the Try again retry path
   *  for an LLM-unavailable degraded turn (in which case `appendUserBubble`
   *  is false — the original user message is already on screen). */
  const sendUserMessage = useCallback(async (
    userMessage: string,
    { appendUserBubble }: { appendUserBubble: boolean },
  ) => {
    setLoading(true);
    setStreamingText("");
    setStatusStage(null);
    if (appendUserBubble) {
      setMessages((prev) => [...prev, createTextMessage("user", userMessage)]);
    }

    const handleChunk = (partial: string) => {
      setStreamingText(partial);
      setStatusStage(null);
    };

    try {
      let { res, payload } = await requestWidgetTurn({
        apiBase,
        botId,
        locale: localeParam,
        message: userMessage,
        attemptSessionId: sessionId,
        onChunk: handleChunk,
        onStatus: setStatusStage,
      });
      let detail = payload.detail;
      let code = apiErrorCode(detail);
      if (!res.ok && sessionId && code && RETRYABLE_SESSION_ERROR_CODES.has(code)) {
        clearStoredSession(botId, userIdRef.current);
        setSessionId(null);
        setStreamingText("");
        setStatusStage(null);
        ({ res, payload } = await requestWidgetTurn({
          apiBase,
          botId,
          locale: localeParam,
          message: userMessage,
          attemptSessionId: null,
          onChunk: handleChunk,
          onStatus: setStatusStage,
        }));
        detail = payload.detail;
        code = apiErrorCode(detail);
      }

      if (!res.ok) {
        throw new Error(formatApiDetail(detail, `API error: ${res.status}`));
      }

      // LLM-unavailable degraded path: render typed fallback with action
      // buttons instead of an assistant bubble. Backend already supplies
      // the localized text in payload.text.
      if (payload.outcome === "llm_unavailable" && payload.failure_state) {
        if (payload.session_id) {
          setSessionId(payload.session_id);
          persistSession(botId, payload.session_id, userIdRef.current);
        }
        setMessages((prev) => [
          ...prev,
          createLlmUnavailableMessage({
            text: payload.text ?? "",
            originalMessage: userMessage,
            failureState: payload.failure_state!,
          }),
        ]);
        return;
      }

      const data = payload as {
        text: string;
        session_id: string;
        sources?: WidgetSource[];
      };

      applyAssistantMessage(data);
      setSessionId(data.session_id);
      persistSession(botId, data.session_id, userIdRef.current);
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
      setStatusStage(null);
      setLoading(false);
    }
  }, [applyAssistantMessage, apiBase, botId, localeParam, sessionId]);

  const handleSend = async () => {
    const userMessage = trimmedInput;
    if (!userMessage || !canSend) return;
    setInput("");
    await sendUserMessage(userMessage, { appendUserBubble: true });
  };

  const handleSendClick = () => {
    void handleSend();
  };

  /** Try again on an LLM-unavailable bubble: reuse the original user message
   *  without producing a duplicate user bubble. The degraded bubble stays
   *  visible (in a loading state) during the retry — sendUserMessage appends
   *  the new outcome below it; only on a successful answer do we drop the
   *  stale bubble, avoiding flicker if retry returns the same failure. */
  const handleLlmUnavailableRetry = useCallback(async (messageId: string) => {
    let originalMessage: string | null = null;
    setMessages((prev) =>
      prev.map((msg) => {
        if (msg.id === messageId && msg.type === "llm_unavailable") {
          originalMessage = msg.originalMessage;
          return { ...msg, retryInProgress: true };
        }
        return msg;
      }),
    );
    if (!originalMessage) return;
    const messagesCountBefore = messages.length;
    try {
      await sendUserMessage(originalMessage, { appendUserBubble: false });
      // If sendUserMessage appended a non-degraded outcome (assistant or
      // error), drop the stale degraded bubble. If it appended another
      // llm_unavailable, the new bubble carries fresh state — also drop
      // the old one so we don't show two side by side.
      setMessages((prev) =>
        prev.length > messagesCountBefore
          ? prev.filter((m) => m.id !== messageId)
          : prev,
      );
    } finally {
      // Always clear the loading flag on the old bubble (no-op if it was
      // already removed by the success branch above).
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId && m.type === "llm_unavailable"
            ? { ...m, retryInProgress: false }
            : m,
        ),
      );
    }
  }, [messages.length, sendUserMessage]);

  /** Contact support on an LLM-unavailable bubble: POST to the escalate
   *  proxy with the original message and failure type. Backend creates the
   *  ticket without invoking the LLM. */
  const handleLlmUnavailableEscalate = useCallback(async (messageId: string) => {
    const target = messages.find(
      (m): m is Extract<ChatWidgetMessage, { type: "llm_unavailable" }> =>
        m.id === messageId && m.type === "llm_unavailable",
    );
    if (!target || !sessionId) return;
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId && m.type === "llm_unavailable"
          ? { ...m, escalationStatus: "in_progress" }
          : m,
      ),
    );
    try {
      // Use apiBase (same origin as /widget/chat & /widget/history) — a
      // relative path would target the customer site's origin when the
      // widget is embedded, causing 404 / CORS failures.
      const params = new URLSearchParams({
        bot_id: botId,
        session_id: sessionId,
      });
      const res = await fetch(`${apiBase}/widget/escalate?${params}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trigger: "llm_unavailable",
          failure_type: target.failureState.type,
          original_user_message: target.originalMessage,
        }),
      });
      if (!res.ok) throw new Error(`escalate failed: ${res.status}`);
      const data = (await res.json()) as { ticket_number?: string };
      if (data.ticket_number) setActiveTicket(data.ticket_number);
      const notifiedText = tString(localeParam, "support_notified");
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId && m.type === "llm_unavailable"
            ? { ...m, escalationStatus: "done", text: notifiedText }
            : m,
        ),
      );
    } catch (error) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId && m.type === "llm_unavailable"
            ? { ...m, escalationStatus: "idle" }
            : m,
        ),
      );
      setMessages((prev) => [
        ...prev,
        createTextMessage(
          "error",
          error instanceof Error ? error.message : "Failed to contact support",
        ),
      ]);
    }
  }, [apiBase, botId, localeParam, messages, sessionId]);

  return (
    <div className="relative flex h-full w-full min-h-0 flex-col overflow-hidden bg-white">
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
          <MessageList
            messages={messages}
            compact={compact}
            operatorLabel={operatorLabel}
            localeParam={localeParam}
            markdownComponents={markdownComponents}
            maybeOpenLinkSafety={maybeOpenLinkSafety}
            renderBelowAssistant={renderBelowAssistant}
            handleLlmUnavailableRetry={handleLlmUnavailableRetry}
            handleLlmUnavailableEscalate={handleLlmUnavailableEscalate}
            loading={loading}
            streamingText={streamingText}
            statusStage={statusStage}
          />
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
            placeholder="Type a message..."
            disabled={loading}
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
            href={withUtm(siteUrl, botId)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-gray-400 transition hover:text-gray-600"
          >
            Powered by Chat9
            <span aria-hidden="true">→</span>
          </a>
        </div>
      </div>

      {pendingExternalLink ? (
        <LinkSafetyModal
          hostname={pendingExternalLink.hostname}
          labels={linkSafetyLabels}
          onCancel={() => setPendingExternalLink(null)}
          onConfirm={() => {
            const target = pendingExternalLink.url;
            setPendingExternalLink(null);
            window.open(target, "_blank", "noopener,noreferrer");
          }}
        />
      ) : null}
    </div>
  );
}
