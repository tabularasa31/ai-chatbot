export type WidgetSource = { title: string; url: string };

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

export type ChatWidgetMessage =
  | {
      id: string;
      type: "assistant" | "user" | "error";
      text: string;
      sources?: WidgetSource[];
    }
  | {
      id: string;
      type: "system";
      subtype: "conversation_ended" | "new_conversation";
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

export function createTextMessage(
  type: "assistant" | "user" | "error",
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

export function createSystemMessage(
  subtype: "conversation_ended" | "new_conversation",
): ChatWidgetMessage {
  return {
    id: createMessageId(),
    type: "system",
    subtype,
  };
}

export function appendSystemMarker(
  messages: ChatWidgetMessage[],
  subtype: "conversation_ended" | "new_conversation",
): ChatWidgetMessage[] {
  if (subtype === "conversation_ended") {
    const last = messages[messages.length - 1];
    if (last?.type === "system" && last.subtype === "conversation_ended") {
      return messages;
    }
  }
  return [...messages, createSystemMessage(subtype)];
}

export function getLastEndedMarkerIndex(messages: ChatWidgetMessage[]): number {
  return messages.reduce((lastIndex, item, index) => {
    if (item.type === "system" && item.subtype === "conversation_ended") {
      return index;
    }
    return lastIndex;
  }, -1);
}
