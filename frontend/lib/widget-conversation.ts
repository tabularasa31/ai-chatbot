export type ChatWidgetMessage =
  | {
      id: string;
      type: "assistant" | "user" | "error";
      text: string;
    }
  | {
      id: string;
      type: "system";
      subtype: "conversation_ended" | "new_conversation";
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
): ChatWidgetMessage {
  return {
    id: createMessageId(),
    type,
    text,
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
