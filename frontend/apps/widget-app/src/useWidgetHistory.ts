import { useEffect } from "react";
import {
  appendSystemMarker,
  createSystemMessage,
  createTextMessage,
  type ChatWidgetMessage,
  type HandoffState,
} from "@chat9/widget-shared";
import { clearStoredSession } from "./session-storage";
import type { WidgetRefs, WidgetSetters } from "./widget-state";

type Props = {
  sessionHydrated: boolean;
  sessionId: string | null;
  historyLoaded: boolean;
  botId: string;
  apiBase: string;
  refs: Pick<WidgetRefs, "userIdRef" | "cursorRef">;
  fetchGreeting: (attemptSessionId?: string | null) => Promise<void>;
  setters: Pick<
    WidgetSetters,
    | "setSessionId"
    | "setHandoffState"
    | "setOperatorLabel"
    | "setMessages"
    | "setActiveTicket"
    | "setPendingReadId"
    | "setHistoryLoaded"
    | "setLoading"
  >;
};

/** Fetches /widget/history once a session is hydrated, hydrates the message
 *  list (with new-conversation boundary markers), and re-greets when the
 *  server reports the conversation rotated underneath the stored session. */
export function useWidgetHistory({
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
}: Props) {
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
        if (!r.ok) {
          // Transient error (5xx, network) — keep session, silently skip history
          return null;
        }
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
            hydrated.push(createTextMessage(m.role as "user" | "assistant" | "operator", m.content));
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
          void fetchGreeting(sessionId).catch(() => {
            // Best-effort: without a greeting the visitor still gets a fresh
            // conversation on their first real message.
          });
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
    return () => {
      cancelled = true;
    };
  }, [sessionHydrated, sessionId, historyLoaded, botId, apiBase, fetchGreeting]);
}
