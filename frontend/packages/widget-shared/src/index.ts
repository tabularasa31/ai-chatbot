export type WidgetSource = { title: string; url: string };

export type UserHints = {
  user_id?: string;
  user_hash?: string;
  email?: string;
  name?: string;
  locale?: string;
  plan_tier?: string;
  audience_tag?: string;
};

export type WidgetTheme = "light" | "dark";

export type Chat9StartConfig = {
  userHints?: UserHints;
  mode?: "bubble" | "inline";
  theme?: WidgetTheme;
  color?: string;
  position?: "right" | "left";
  target?: string;
  topClearance?: number;
  apiBase?: string;
  widgetBase?: string;
};

// Who is answering, as the server sees it: "bot" (nobody has escalated),
// "waiting" (an open request nobody has picked up) or "live" (a human is in
// the conversation). The third state is derived server-side, never stored.
export type HandoffState = "bot" | "waiting" | "live";

// Shared shape of a completed widget turn: the accumulated payload of a
// streamed reply, or the JSON error body of a failed one.
export type WidgetTurnPayload = {
  detail?: unknown;
  text?: string;
  session_id?: string;
  ticket_number?: string | null;
  sources?: WidgetSource[];
  outcome?: string | null;
  failure_state?: LlmFailureState | null;
  delivered_to_operator?: boolean;
};

// Public Chat9Widget API surface (window.Chat9Widget). Mirrors the loader's
// implementation in apps/widget-loader/src/index.ts.
export type Chat9WidgetApi = {
  start: (config?: Chat9StartConfig) => void;
  stop: () => void;
  setHints: (hints: UserHints | null) => void;
  isStarted: () => boolean;
  destroy: () => void;
};

export type LlmFailureType =
  | "provider_unavailable"
  | "provider_timeout"
  | "rate_limited"
  | "quota_exhausted"
  | "invalid_api_key"
  | "unknown_llm_error";

export type LlmFailureState = {
  type: LlmFailureType;
  retryable: boolean;
  can_escalate: boolean;
};

// What "Try again" on an error bubble repeats. A failed send keeps the
// visitor's text so it is never retyped.
export type ErrorRetry =
  | { kind: "send"; message: string }
  | { kind: "history" }
  | { kind: "escalate"; messageId: string };

export type ChatWidgetMessage =
  | {
      // "operator" is a human answering the visitor — through the dashboard
      // console or by replying to the escalation notification. Deliberately
      // its own type rather than an "assistant" with a flag: it renders
      // differently, carries a byline, and must never be counted as a bot
      // reply by anything reading this list.
      id: string;
      type: "assistant" | "user" | "operator";
      text: string;
      sources?: WidgetSource[];
    }
  | {
      id: string;
      type: "error";
      text: string;
      retry: ErrorRetry;
    }
  | {
      id: string;
      type: "system";
      subtype: "new_conversation";
    }
  | {
      id: string;
      type: "llm_unavailable";
      text: string;
      originalMessage: string;
      failureState: LlmFailureState;
      escalationStatus: "idle" | "in_progress" | "done";
      retryInProgress: boolean;
    };

function createMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `msg_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

export function createErrorMessage(text: string, retry: ErrorRetry): ChatWidgetMessage {
  return { id: createMessageId(), type: "error", text, retry };
}

export function createTextMessage(
  type: "assistant" | "user" | "operator",
  text: string,
  sources?: WidgetSource[],
): ChatWidgetMessage {
  return {
    id: createMessageId(),
    type,
    text,
    sources,
  };
}

export function createLlmUnavailableMessage(args: {
  text: string;
  originalMessage: string;
  failureState: LlmFailureState;
}): ChatWidgetMessage {
  return {
    id: createMessageId(),
    type: "llm_unavailable",
    text: args.text,
    originalMessage: args.originalMessage,
    failureState: args.failureState,
    escalationStatus: "idle",
    retryInProgress: false,
  };
}

export function createSystemMessage(subtype: "new_conversation"): ChatWidgetMessage {
  return {
    id: createMessageId(),
    type: "system",
    subtype,
  };
}

export { sharedChromeString, type SharedChromeKey } from "./chromeStrings";
export { ACCENT_ON_LIGHT, accentOnRgb, parseHexColor, rgbToHex, type Rgb } from "./accent";
