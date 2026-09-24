import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import type { ChatWidgetMessage, HandoffState } from "@chat9/widget-shared";

// Grouped state setters shared by the extracted history/polling hooks, so
// each hook only lists the slice it actually writes via Pick<>.
export type WidgetSetters = {
  setSessionId: Dispatch<SetStateAction<string | null>>;
  setHandoffState: Dispatch<SetStateAction<HandoffState>>;
  setOperatorLabel: Dispatch<SetStateAction<string>>;
  setActiveTicket: Dispatch<SetStateAction<string | null>>;
  setMessages: Dispatch<SetStateAction<ChatWidgetMessage[]>>;
  setPendingReadId: Dispatch<SetStateAction<string | null>>;
  setHistoryLoaded: Dispatch<SetStateAction<boolean>>;
  setLoading: Dispatch<SetStateAction<boolean>>;
};

export type WidgetRefs = {
  cursorRef: MutableRefObject<string | null>;
  activeTicketRef: MutableRefObject<string | null>;
  pollInFlightRef: MutableRefObject<boolean>;
  userIdRef: MutableRefObject<string | null>;
};
