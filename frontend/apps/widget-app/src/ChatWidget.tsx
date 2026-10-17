"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { type Components } from "react-markdown";
import "highlight.js/styles/github-dark.css";
import { ArrowUp, Check, Copy, ExternalLink, Info, Loader2, TriangleAlert, X } from "lucide-react";
import { ActionButton } from "./ActionButton";
import { cn, withUtm } from "./utils";
import { LinkSafetyModal } from "./LinkSafetyModal";
import { LoadingIndicator } from "./LoadingIndicator";
import { MarkdownBody } from "./MarkdownBody";
import {
  appendSystemMarker,
  createErrorMessage,
  createLlmUnavailableMessage,
  createSystemMessage,
  createTextMessage,
  type ChatWidgetMessage,
  type HandoffState,
  type LlmFailureState,
  type UserHints,
  type WidgetSource,
  type WidgetTurnPayload,
} from "@chat9/widget-shared";
import { t as tString } from "./strings";

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
  /** The loader reports the panel as full-screen (phones): focus the panel, not the input. */
  fullScreen?: boolean;
  /** Origin for API calls (e.g. "https://getchat9.live"). No trailing slash. */
  apiBase: string;
  /** Marketing site URL for the "Powered by Chat9" footer link. */
  siteUrl?: string;
  /** Asks the host page to close the panel. Absent when the widget cannot be
   *  closed (inline mode, standalone tab): no header button, Escape does nothing. */
  onClose?: () => void;
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

const COPIED_FEEDBACK_MS = 2000;

function CodeCopyButton({ code, locale }: { code: string; locale: string | null }) {
  const [copied, setCopied] = useState(false);
  async function handleCopy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
  }
  const label = tString(locale, copied ? "code_copied" : "copy_code");
  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={label}
      title={label}
      className="hit-44 relative inline-flex h-8 w-8 items-center justify-center rounded-control text-chrome-fg-muted transition-colors hover:bg-chrome-hover"
    >
      {copied ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
    </button>
  );
}

const STATIC_MD_COMPONENTS: Components = {
  img: () => null,
  // Prevent react-markdown's default <pre> wrapper — our code component handles it.
  pre: ({ children }) => <>{children}</>,
  table: ({ node: _node, ...props }) => (
    <div className="my-2 overflow-x-auto">
      <table {...props} />
    </div>
  ),
};

function codeComponent(locale: string | null): Components["code"] {
  return ({ node, className, children, ...props }) => {
    if (!className) return <code {...props}>{children}</code>;
    // Children are already highlighted spans; copy needs the raw text.
    const rawCode = hastToText(node).replace(/\n$/, "");
    return (
      <div dir="ltr" className="mt-2 overflow-hidden rounded-control bg-chrome text-chrome-fg">
        <div className="flex h-9 items-center justify-end px-1">
          <CodeCopyButton code={rawCode} locale={locale} />
        </div>
        <pre className="overflow-x-auto whitespace-pre px-4 pb-4 text-caption">
          <code className={className}>{children}</code>
        </pre>
      </div>
    );
  };
}

const DEFAULT_SITE_URL = "https://getchat9.live";
const BUBBLE_CLASSES = "min-w-0 max-w-[85%] rounded-bubble px-3 py-2 [overflow-wrap:anywhere]";
const ASSISTANT_BUBBLE_CLASSES = "rounded-bl-tag border border-line bg-surface text-fg";

function sourceHostname(url: string): string | null {
  try {
    return new URL(url).hostname;
  } catch {
    return null;
  }
}
const SESSION_STORAGE_TTL_MS = 24 * 60 * 60 * 1000;
const RETRYABLE_SESSION_ERROR_CODES = new Set([
  "session_invalid",
  "session_not_found",
  "session_forbidden",
]);

function sessionStorageKey(botId: string, userId?: string | null): string {
  return userId ? `chat9:${botId}:${userId}:session` : `chat9:${botId}:session`;
}

function sessionUpdatedAtStorageKey(botId: string, userId?: string | null): string {
  return userId ? `chat9:${botId}:${userId}:session_updated_at` : `chat9:${botId}:session_updated_at`;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

// Storage-key discriminator derived from hints. Lets us namespace localStorage
// per visitor so one browser shared between accounts doesn't bleed history
// across them. Falls back to email-based synthetic id matching the backend.
function deriveStorageUserId(hints: UserHints | null | undefined): string | null {
  if (!hints) return null;
  if (hints.user_id && hints.user_id.trim()) return hints.user_id.trim();
  if (hints.email && hints.email.trim()) return `hint:${hints.email.trim()}`;
  return null;
}

function clearStoredSession(botId: string, userId?: string | null): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(sessionStorageKey(botId, userId));
    window.localStorage.removeItem(sessionUpdatedAtStorageKey(botId, userId));
  } catch {
    // localStorage can be blocked in embedded/privacy-restricted contexts.
  }
}

function readStoredSession(botId: string, userId?: string | null): string | null {
  if (typeof window === "undefined") return null;
  let storedSessionId: string | null = null;
  let storedUpdatedAt: string | null = null;
  try {
    storedSessionId = window.localStorage.getItem(sessionStorageKey(botId, userId));
    storedUpdatedAt = window.localStorage.getItem(sessionUpdatedAtStorageKey(botId, userId));
  } catch {
    return null;
  }
  if (!storedSessionId || !storedUpdatedAt) {
    clearStoredSession(botId, userId);
    return null;
  }
  if (!isUuid(storedSessionId)) {
    clearStoredSession(botId, userId);
    return null;
  }
  const updatedAtMs = Number(storedUpdatedAt);
  if (!Number.isFinite(updatedAtMs) || Date.now() - updatedAtMs > SESSION_STORAGE_TTL_MS) {
    clearStoredSession(botId, userId);
    return null;
  }
  return storedSessionId;
}

// The sliding 24h TTL is the lifetime of the visitor identity, not of a
// conversation: the server rotates conversations on its own idle timeout and
// keeps them linked to this session_id.
function persistSession(botId: string, sessionId: string, userId?: string | null): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(sessionStorageKey(botId, userId), sessionId);
    window.localStorage.setItem(sessionUpdatedAtStorageKey(botId, userId), String(Date.now()));
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
  fullScreen = false,
  apiBase,
  siteUrl = DEFAULT_SITE_URL,
  onClose,
}: ChatWidgetProps) {
  const [messages, setMessages] = useState<ChatWidgetMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionHydrated, setSessionHydrated] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [greetingPending, setGreetingPending] = useState(false);
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
  const panelRef = useRef<HTMLDivElement>(null);
  const linkOpenerRef = useRef<HTMLElement | null>(null);
  const modalOpenRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // The greeting request in flight, resolving to the session it opened (null on
  // failure). A visitor's first message waits for it so both share one session.
  const greetingInFlightRef = useRef<Promise<string | null> | null>(null);
  // Set once the automatic greeting has been requested: an empty conversation
  // is greeted exactly once, however often the effect re-runs.
  const greetingRequestedRef = useRef(false);
  // True once the visitor has sent a message since the greeting was requested;
  // a greeting that arrives afterwards is dropped rather than shown after it.
  const visitorSentRef = useRef(false);
  // Tracks the storage user_id derived from the current hints so other effects can use it.
  const userIdRef = useRef<string | null>(null);

  const localeParam = locale && locale.trim() ? locale.trim() : undefined;
  const trimmedInput = input.trim();
  const canSend = Boolean(trimmedInput) && !loading;
  const ticketNotice = activeTicket
    ? tString(
        locale,
        handoffState === "waiting" ? "ticket_waiting" : handoffState === "live" ? "ticket_live" : "ticket_open",
        { n: activeTicket },
      )
    : null;

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
      linkOpenerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setPendingExternalLink({ url: url.href, hostname: url.hostname });
      return true;
    } catch {
      return false;
    }
  }, [widgetConfig]);

  const markdownComponents = useMemo<Components>(() => ({
    ...STATIC_MD_COMPONENTS,
    code: codeComponent(locale ?? null),
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
  }), [locale, maybeOpenLinkSafety]);

  useEffect(() => {
    const userId = deriveStorageUserId(hints);
    userIdRef.current = userId;

    setSessionHydrated(false);
    setHistoryLoaded(false);
    greetingRequestedRef.current = false;
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

  const requestWidgetTurn = useCallback(async ({
    message,
    attemptSessionId,
    onChunk,
    onStatus,
  }: {
    message: string;
    attemptSessionId: string | null;
    onChunk?: (partialText: string) => void;
    onStatus?: (stage: string) => void;
  }) => {
    const params = new URLSearchParams({
      bot_id: botId,
    });
    if (attemptSessionId) params.set("session_id", attemptSessionId);

    const res = await fetch(`${apiBase}/widget/chat?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        locale: localeParam,
      }),
    });

    if (!res.ok || !res.body) {
      const payload = (await res.json().catch(() => ({}))) as WidgetTurnPayload;
      return { res, payload };
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";
    const payload: WidgetTurnPayload = {};

    const handleEvent = (eventData: string) => {
      const raw = eventData.trim();
      if (!raw) return;
      let parsed: {
        type?: string;
        text?: string;
        stage?: string;
        session_id?: string;
        ticket_number?: string;
        message?: string;
        code?: number;
        sources?: WidgetSource[];
        outcome?: string | null;
        failure_state?: LlmFailureState | null;
      };
      try {
        parsed = JSON.parse(raw);
      } catch {
        return;
      }
      if (parsed.type === "chunk" && typeof parsed.text === "string") {
        fullText += parsed.text;
        onChunk?.(fullText);
      } else if (parsed.type === "status" && typeof parsed.stage === "string") {
        onStatus?.(parsed.stage);
      } else if (parsed.type === "done") {
        payload.text = typeof parsed.text === "string" ? parsed.text : fullText;
        payload.session_id = parsed.session_id;
        payload.ticket_number = parsed.ticket_number ?? null;
        payload.sources = parsed.sources ?? [];
        payload.outcome = parsed.outcome ?? null;
        payload.failure_state = parsed.failure_state ?? null;
        // Don't replay the final text into onChunk for the degraded path:
        // the LLM-unavailable message is rendered as its own UI block, not
        // as an assistant bubble streamed token-by-token.
        if (
          parsed.outcome !== "llm_unavailable" &&
          typeof parsed.text === "string" &&
          parsed.text !== fullText
        ) {
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
  }, [botId, localeParam, apiBase]);

  // attemptSessionId is null for a brand-new session; after conversation
  // rotation it carries the existing session so the greeting opens the new
  // conversation server-side instead of minting another session.
  const fetchGreeting = useCallback(async (attemptSessionId: string | null = null) => {
    const { res, payload } = await requestWidgetTurn({
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
    // Marking history loaded keeps a late history fetch from overwriting the visitor's bubble.
    if (visitorSentRef.current) setHistoryLoaded(true);
    else applyAssistantMessage(data);
    setSessionId(data.session_id);
    persistSession(botId, data.session_id, userIdRef.current);
    return data.session_id;
  }, [applyAssistantMessage, botId, requestWidgetTurn]);

  const appendGreetingError = useCallback((error: unknown) => {
    console.error("Chat9: greeting failed", error);
    if (visitorSentRef.current) return;
    setMessages((prev) => [
      ...prev,
      createErrorMessage(tString(localeParam, "error_greeting"), { kind: "greeting" }),
    ]);
  }, [localeParam]);

  const requestGreeting = useCallback((attemptSessionId: string | null) => {
    visitorSentRef.current = false;
    setGreetingPending(true);
    const request = fetchGreeting(attemptSessionId)
      .catch((error) => {
        appendGreetingError(error);
        return null;
      })
      .finally(() => {
        greetingInFlightRef.current = null;
        setGreetingPending(false);
      });
    greetingInFlightRef.current = request;
    return request;
  }, [appendGreetingError, fetchGreeting]);

  const requestGreetingOnce = useCallback((attemptSessionId: string | null) => {
    if (greetingRequestedRef.current) return;
    greetingRequestedRef.current = true;
    void requestGreeting(attemptSessionId);
  }, [requestGreeting]);

  useEffect(() => {
    if (!sessionHydrated || !sessionId || historyLoaded) return;
    let cancelled = false;
    setLoading(true);
    const params = new URLSearchParams({ bot_id: botId, session_id: sessionId });
    fetch(`${apiBase}/widget/history?${params}`)
      .then(async (r) => {
        if (r.status === 404) {
          // Session no longer exists on the backend — start fresh
          if (!cancelled) {
            clearStoredSession(botId, userIdRef.current);
            setSessionId(null);
          }
          return null;
        }
        if (!r.ok) throw new Error(`History error: ${r.status}`);
        return r.json() as Promise<{
          messages: { id: string; role: string; content: string }[];
          ticket_number?: string | null;
          boundary_indices?: number[];
          conversation_rotated?: boolean;
          handoff_state?: HandoffState;
          operator_label?: string;
        }>;
      })
      .then((data) => {
        if (cancelled || !data) return;
        if (data.handoff_state) setHandoffState(data.handoff_state);
        if (data.operator_label) setOperatorLabel(data.operator_label);
        if (data.messages.length > 0) {
          const boundaries = new Set(data.boundary_indices ?? []);
          const hydrated: ChatWidgetMessage[] = [];
          data.messages.forEach((m, index) => {
            // Operator rows belong here as much as assistant ones do: a human
            // answering by e-mail or from the console writes into the same
            // transcript, and dropping them here is what used to make the
            // whole handoff invisible.
            if (m.role !== "user" && m.role !== "assistant" && m.role !== "operator") return;
            if (boundaries.has(index)) {
              hydrated.push(createSystemMessage("new_conversation"));
            }
            hydrated.push(createTextMessage(m.role, m.content));
          });
          // Where the cursor poll picks up. Taken from the raw list rather
          // than the hydrated one so a role the widget skips still advances it.
          const lastServerMessage = data.messages[data.messages.length - 1];
          cursorRef.current = lastServerMessage?.id ?? null;
          const lastOperatorMessage = [...data.messages].reverse().find((m) => m.role === "operator");
          if (lastOperatorMessage) setPendingReadId(lastOperatorMessage.id);
          setMessages(hydrated);
          if (data.ticket_number) setActiveTicket(data.ticket_number);
        }
        if (data.conversation_rotated) {
          // Returning visitor past the idle threshold: keep the old messages
          // as read-only context, mark the boundary, and greet afresh — the
          // greeting POST opens the new conversation server-side.
          if (data.messages.length > 0) {
            setMessages((prev) => appendSystemMarker(prev, "new_conversation"));
          }
          greetingRequestedRef.current = true;
          void requestGreeting(sessionId);
        }
      })
      .catch((error) => {
        if (cancelled) return;
        console.error("Chat9: history failed", error);
        setMessages((prev) => [
          ...prev,
          createErrorMessage(tString(localeParam, "error_history"), { kind: "history" }),
        ]);
      })
      .finally(() => {
        if (!cancelled) {
          setHistoryLoaded(true);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [sessionHydrated, sessionId, historyLoaded, botId, apiBase, localeParam, requestGreeting]);

  useEffect(() => {
    const waitingForHistory = sessionId !== null && !historyLoaded;
    if (!sessionHydrated || waitingForHistory || loading || messages.length > 0) return;
    requestGreetingOnce(sessionId);
  }, [historyLoaded, loading, messages.length, requestGreetingOnce, sessionHydrated, sessionId]);

  // How often to ask whether a human has written. The ladder is the whole
  // point: a conversation the bot is handling is not polled at all, one
  // waiting in a queue is polled lazily, and one a human is actively typing
  // into is polled briskly enough that their reply lands while the visitor is
  // still looking at the window.
  const POLL_INTERVAL_LIVE_MS = 2500;
  const POLL_INTERVAL_WAITING_MS = 20000;

  const pollForOperatorMessages = useCallback(async () => {
    if (!sessionId || pollInFlightRef.current) return;
    pollInFlightRef.current = true;
    const ticketAtDispatch = activeTicketRef.current;
    try {
      const params = new URLSearchParams({ bot_id: botId, session_id: sessionId });
      if (cursorRef.current) params.set("after_message_id", cursorRef.current);
      const res = await fetch(`${apiBase}/widget/messages?${params}`);
      if (!res.ok) return;
      const data = (await res.json()) as {
        messages?: { id: string; role: string; content: string }[];
        handoff_state?: HandoffState;
        operator_label?: string;
        cursor_stale?: boolean;
      };
      if (data.cursor_stale) {
        // The conversation rotated underneath us. Splicing this tail onto what
        // is on screen would duplicate it, so re-run the bootstrap instead.
        cursorRef.current = null;
        setHistoryLoaded(false);
        return;
      }
      if (data.handoff_state) {
        setHandoffState(data.handoff_state);
        // `bot` is the server saying there is no open request and nobody
        // holding the chat. Clearing the ticket here is what lets the poll
        // stop: `activeTicket` is otherwise set once and never unset, so
        // without this the widget would keep asking every twenty seconds for
        // as long as the tab stayed open.
        //
        // Only when the ticket has not changed under us. A poll dispatched
        // before a fresh escalation can land after it, and clearing then
        // would silence the widget on a request that had only just been
        // raised.
        if (data.handoff_state === "bot" && activeTicketRef.current === ticketAtDispatch) {
          setActiveTicket(null);
        }
      }
      if (data.operator_label) setOperatorLabel(data.operator_label);
      const incoming = data.messages ?? [];
      if (incoming.length > 0) {
        cursorRef.current = incoming[incoming.length - 1].id;
        // Only human replies are appended. The visitor's own turns and the
        // bot's are already on screen from the send that produced them, and
        // adding the server's copy would show each of them twice.
        const operatorMessages = incoming.filter((m) => m.role === "operator");
        if (operatorMessages.length > 0) {
          setMessages((prev) => [
            ...prev,
            ...operatorMessages.map((m) => createTextMessage("operator", m.content)),
          ]);
          setPendingReadId(operatorMessages[operatorMessages.length - 1].id);
        }
      }
    } catch {
      // Transient: the next tick tries again, and the cursor has not moved.
    } finally {
      pollInFlightRef.current = false;
    }
  }, [apiBase, botId, sessionId]);

  useEffect(() => {
    // An open request is enough to start polling even before the server has
    // reported a state: the escalation that just happened is exactly when a
    // human might appear. What bounds the polling is the handoff itself: once
    // the ticket is resolved the server reports `bot` and this goes quiet on
    // its own.
    const shouldPoll =
      sessionHydrated &&
      Boolean(sessionId) &&
      historyLoaded &&
      (handoffState !== "bot" || activeTicket !== null);
    if (!shouldPoll) return;

    const period =
      handoffState === "live" ? POLL_INTERVAL_LIVE_MS : POLL_INTERVAL_WAITING_MS;
    let timer: ReturnType<typeof setInterval> | undefined;

    const stop = () => {
      if (timer !== undefined) {
        clearInterval(timer);
        timer = undefined;
      }
    };
    const start = () => {
      stop();
      timer = setInterval(() => {
        void pollForOperatorMessages();
      }, period);
    };
    // A hidden tab polls nothing: the visitor is not reading, and a widget
    // left open in a background tab for a day would otherwise poll all day.
    // Coming back into view polls immediately rather than waiting out a tick.
    const resync = () => {
      if (document.hidden) {
        stop();
        return;
      }
      void pollForOperatorMessages();
      start();
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", resync);
    window.addEventListener("focus", resync);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", resync);
      window.removeEventListener("focus", resync);
    };
  }, [
    activeTicket,
    handoffState,
    historyLoaded,
    pollForOperatorMessages,
    sessionHydrated,
    sessionId,
  ]);

  useEffect(() => {
    if (!pendingReadId || !sessionId) return;
    const messageId = pendingReadId;
    let cancelled = false;
    let inFlight = false;
    const report = () => {
      if (inFlight || !isOpen || document.hidden) return;
      inFlight = true;
      const params = new URLSearchParams({ bot_id: botId, session_id: sessionId });
      fetch(`${apiBase}/widget/messages/read?${params}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: messageId }),
      })
        .then((res) => {
          // 404 is a message this conversation no longer contains (rotation);
          // nothing to report, and nothing to keep retrying on every focus.
          if (cancelled || !(res.ok || res.status === 404)) return;
          setPendingReadId((current) => (current === messageId ? null : current));
        })
        .catch(() => {
          // Transient: the next open, focus or reply reports again.
        })
        .finally(() => {
          inFlight = false;
        });
    };
    report();
    document.addEventListener("visibilitychange", report);
    window.addEventListener("focus", report);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", report);
      window.removeEventListener("focus", report);
    };
  }, [apiBase, botId, isOpen, pendingReadId, sessionId]);

  const appendSendError = useCallback((key: "error_send" | "error_rate_limit", message: string) => {
    setMessages((prev) => [
      ...prev,
      createErrorMessage(tString(localeParam, key), { kind: "send", message }),
    ]);
  }, [localeParam]);

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
      visitorSentRef.current = true;
      setMessages((prev) => [
        ...prev.filter((m) => !(m.type === "error" && m.retry.kind === "greeting")),
        createTextMessage("user", userMessage),
      ]);
    }

    const handleChunk = (partial: string) => {
      setStreamingText(partial);
      setStatusStage(null);
    };

    try {
      const greetedSessionId = greetingInFlightRef.current ? await greetingInFlightRef.current : null;
      const attemptSessionId = sessionId ?? greetedSessionId;
      let { res, payload } = await requestWidgetTurn({
        message: userMessage,
        attemptSessionId,
        onChunk: handleChunk,
        onStatus: setStatusStage,
      });
      let detail = payload.detail;
      let code = apiErrorCode(detail);
      if (!res.ok && attemptSessionId && code && RETRYABLE_SESSION_ERROR_CODES.has(code)) {
        clearStoredSession(botId, userIdRef.current);
        setSessionId(null);
        setStreamingText("");
        setStatusStage(null);
        ({ res, payload } = await requestWidgetTurn({
          message: userMessage,
          attemptSessionId: null,
          onChunk: handleChunk,
          onStatus: setStatusStage,
        }));
        detail = payload.detail;
        code = apiErrorCode(detail);
      }

      if (!res.ok) {
        console.error("Chat9: chat request failed", res.status, formatApiDetail(detail, ""));
        appendSendError(res.status === 429 ? "error_rate_limit" : "error_send", userMessage);
        return;
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
      console.error("Chat9: chat request failed", error);
      appendSendError("error_send", userMessage);
    } finally {
      setStreamingText("");
      setStatusStage(null);
      setLoading(false);
    }
  }, [appendSendError, applyAssistantMessage, botId, requestWidgetTurn, sessionId]);

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
      console.error("Chat9: escalate failed", error);
      setMessages((prev) => [
        ...prev,
        createErrorMessage(tString(localeParam, "error_support"), { kind: "escalate", messageId }),
      ]);
    }
  }, [apiBase, botId, localeParam, messages, sessionId]);

  const handleErrorRetry = useCallback((error: Extract<ChatWidgetMessage, { type: "error" }>) => {
    setMessages((prev) => prev.filter((m) => m.id !== error.id));
    if (error.retry.kind === "send") {
      void sendUserMessage(error.retry.message, { appendUserBubble: false });
    } else if (error.retry.kind === "escalate") {
      void handleLlmUnavailableEscalate(error.retry.messageId);
    } else if (error.retry.kind === "history") {
      setHistoryLoaded(false);
    } else if (!visitorSentRef.current && !greetingInFlightRef.current) {
      void requestGreeting(sessionId);
    }
  }, [handleLlmUnavailableEscalate, requestGreeting, sendUserMessage, sessionId]);

  useEffect(() => {
    if (!isOpen || !onClose || modalOpenRef.current) return;
    if (fullScreen) panelRef.current?.focus();
    else inputRef.current?.focus();
  }, [isOpen, fullScreen, onClose]);

  useEffect(() => {
    if (!onClose || pendingExternalLink) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !event.isComposing) onClose?.();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, pendingExternalLink]);

  useEffect(() => {
    if (pendingExternalLink) return;
    const opener = linkOpenerRef.current;
    linkOpenerRef.current = null;
    if (opener?.isConnected) opener.focus();
  }, [pendingExternalLink]);

  const padX = compact ? "pad-x-compact" : "pad-x";
  const modalOpen = pendingExternalLink !== null;
  modalOpenRef.current = modalOpen;

  return (
    <div
      ref={panelRef}
      tabIndex={-1}
      className="relative flex h-full w-full min-h-0 flex-col overflow-hidden bg-surface focus:outline-none"
    >
      <div
        inert={modalOpen}
        aria-hidden={modalOpen}
        className="flex min-h-0 flex-1 flex-col"
      >
        <header className="header-bar flex flex-shrink-0 items-center justify-between bg-chrome text-chrome-fg dark:border-b dark:border-chrome-line">
          <div className="min-w-0">
            <h1 className="text-body font-semibold leading-5">{tString(locale, "panel_name")}</h1>
            <a
              href={withUtm(siteUrl, botId)}
              target="_blank"
              rel="noopener noreferrer"
              className="hit-link relative inline-block text-caption text-chrome-line-strong hover:text-chrome-fg-muted focus-visible:outline-chrome-accent"
            >
              {tString(locale, "powered_by")}
            </a>
          </div>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              aria-label={tString(locale, "close_chat")}
              className="header-close hit-44 relative inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-chrome-fg transition-colors hover:bg-chrome-hover active:bg-chrome-active focus-visible:outline-chrome-accent"
            >
              <X size={16} aria-hidden="true" />
            </button>
          ) : null}
        </header>

        <div className="flex min-h-0 flex-1 flex-col bg-canvas">
        {ticketNotice ? (
          <div role="status" className="m-2 flex items-start gap-2 rounded-control bg-info-subtle px-3 py-2 text-ui text-fg">
            <Info size={16} aria-hidden="true" className="mt-0.5 flex-shrink-0 text-info" />
            {ticketNotice}
          </div>
        ) : null}

        <div
          ref={messagesRef}
          role="log"
          aria-live="polite"
          aria-busy={loading}
          className={cn("min-h-0 flex-1 space-y-3 overflow-y-auto py-4", padX)}
        >
          {messages.map((msg, i) => {
            if (msg.type === "system") {
              return (
                <div key={msg.id} className="flex items-center gap-3 text-caption text-fg-muted">
                  <span aria-hidden="true" className="h-px flex-1 bg-line" />
                  {tString(locale, "new_conversation")}
                  <span aria-hidden="true" className="h-px flex-1 bg-line" />
                </div>
              );
            }

            if (msg.type === "user") {
              return (
                <div key={msg.id} className="flex justify-end">
                  <div dir="auto" className={cn(BUBBLE_CLASSES, "accent-fill rounded-br-tag whitespace-pre-wrap text-ui")}>
                    {msg.text}
                  </div>
                </div>
              );
            }

            if (msg.type === "operator") {
              return (
                <div key={msg.id} className="flex">
                  <div className={cn(BUBBLE_CLASSES, "rounded-bl-tag border border-line bg-surface-alt")}>
                    <p className="mb-1 text-caption font-medium text-fg-muted">{operatorLabel}</p>
                    <MarkdownBody text={msg.text} components={markdownComponents} />
                  </div>
                </div>
              );
            }

            if (msg.type === "llm_unavailable") {
              const showRetry = msg.failureState.retryable && msg.escalationStatus !== "done";
              const showEscalate = msg.failureState.can_escalate && msg.escalationStatus !== "done";
              const escalateVariant = showRetry ? "secondary" : "primary";
              return (
                <div key={msg.id} className="flex">
                  <div className={cn(BUBBLE_CLASSES, ASSISTANT_BUBBLE_CLASSES)}>
                    <p dir="auto" className="whitespace-pre-wrap text-ui">{msg.text}</p>
                    {showRetry || showEscalate ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {showRetry ? (
                          <ActionButton
                            variant="primary"
                            busy={msg.retryInProgress}
                            onClick={() => void handleLlmUnavailableRetry(msg.id)}
                          >
                            {tString(locale, "try_again_button")}
                          </ActionButton>
                        ) : null}
                        {showEscalate ? (
                          <ActionButton
                            variant={escalateVariant}
                            busy={msg.escalationStatus === "in_progress"}
                            onClick={() => void handleLlmUnavailableEscalate(msg.id)}
                          >
                            {tString(locale, "contact_support_button")}
                          </ActionButton>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            }

            if (msg.type === "error") {
              return (
                <div key={msg.id} className="flex">
                  <div role="alert" className={cn(BUBBLE_CLASSES, "rounded-bl-tag bg-danger-subtle pb-3")}>
                    <p className="flex items-start gap-2 text-ui">
                      <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 flex-shrink-0 text-danger" />
                      <span dir="auto">{msg.text}</span>
                    </p>
                    <div className="mt-2">
                      <ActionButton variant="secondary" disabled={loading} onClick={() => handleErrorRetry(msg)}>
                        {tString(locale, "try_again_button")}
                      </ActionButton>
                    </div>
                  </div>
                </div>
              );
            }

            const userQuestion = msg.type === "assistant" ? precedingUserQuestion(messages, i) : "";
            return (
              <div key={msg.id}>
                <div className="flex">
                  <div className={cn(BUBBLE_CLASSES, ASSISTANT_BUBBLE_CLASSES)}>
                    <MarkdownBody text={msg.text} components={markdownComponents} />
                  </div>
                </div>

                {msg.sources && msg.sources.length > 0 ? (
                  <ul className="mt-2 max-w-[85%]">
                    {msg.sources.map((src: WidgetSource) => (
                      <li key={src.url}>
                        <a
                          href={src.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(event) => {
                            if (maybeOpenLinkSafety(src.url)) event.preventDefault();
                          }}
                          className="flex min-h-11 items-center gap-2 py-1"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="line-clamp-2 block text-ui text-brand underline [overflow-wrap:anywhere]">{src.title}</span>
                            <span className="block text-caption text-fg-muted [overflow-wrap:anywhere]">{sourceHostname(src.url)}</span>
                          </span>
                          <ExternalLink size={16} aria-hidden="true" className="flex-shrink-0 text-fg-muted" />
                          <span className="sr-only">{tString(locale, "opens_new_tab")}</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : null}

                {renderBelowAssistant && userQuestion.trim() ? (
                  <div className="mt-3 max-w-[85%]">
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
            <div className="flex">
              <div className={cn(BUBBLE_CLASSES, ASSISTANT_BUBBLE_CLASSES)}>
                <MarkdownBody text={streamingText} components={markdownComponents} />
              </div>
            </div>
          ) : loading ? (
            <LoadingIndicator stage={statusStage} locale={locale ?? null} greeting={messages.length === 0} />
          ) : greetingPending ? (
            <LoadingIndicator stage={null} locale={locale ?? null} greeting />
          ) : null}
        </div>
        </div>

        <div className={cn("pad-b-composer flex-shrink-0 bg-surface pt-2", padX)}>
          <div className="flex items-center gap-2">
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
              dir="auto"
              aria-label={tString(locale, "composer_label")}
              placeholder={tString(locale, "composer_placeholder")}
              enterKeyHint="send"
              autoComplete="off"
              autoCapitalize="sentences"
              className="composer-input h-10 min-w-0 flex-1 rounded-full border border-line bg-surface-alt px-4 text-fg placeholder:text-fg-faint dark:bg-canvas"
            />
            <button
              type="button"
              onClick={handleSendClick}
              disabled={!loading && !trimmedInput}
              aria-disabled={loading || undefined}
              aria-busy={loading}
              aria-label={tString(locale, "send_message")}
              className="accent-fill accent-press hit-44-send relative inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:bg-surface-alt disabled:text-fg-faint"
            >
              {loading ? (
                <Loader2 size={16} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
              ) : (
                <ArrowUp size={16} aria-hidden="true" />
              )}
            </button>
          </div>
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
