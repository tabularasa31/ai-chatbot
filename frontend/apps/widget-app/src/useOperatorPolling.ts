import { useCallback, useEffect } from "react";
import { createTextMessage, type HandoffState } from "@chat9/widget-shared";
import type { WidgetRefs, WidgetSetters } from "./widget-state";

// How often to ask whether a human has written. The ladder is the whole
// point: a conversation the bot is handling is not polled at all, one
// waiting in a queue is polled lazily, and one a human is actively typing
// into is polled briskly enough that their reply lands while the visitor is
// still looking at the window.
const POLL_INTERVAL_LIVE_MS = 2500;
const POLL_INTERVAL_WAITING_MS = 20000;

type Props = {
  apiBase: string;
  botId: string;
  sessionId: string | null;
  sessionHydrated: boolean;
  historyLoaded: boolean;
  handoffState: HandoffState;
  activeTicket: string | null;
  isOpen: boolean;
  pendingReadId: string | null;
  refs: Pick<WidgetRefs, "cursorRef" | "activeTicketRef" | "pollInFlightRef">;
  setters: Pick<
    WidgetSetters,
    "setHandoffState" | "setOperatorLabel" | "setActiveTicket" | "setMessages" | "setPendingReadId" | "setHistoryLoaded"
  >;
};

/** Polls /widget/messages for operator replies while a ticket is open, and
 *  reports the newest operator reply as read while the panel is visible. */
export function useOperatorPolling({
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
}: Props) {
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

    const period = handoffState === "live" ? POLL_INTERVAL_LIVE_MS : POLL_INTERVAL_WAITING_MS;
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
  }, [activeTicket, handoffState, historyLoaded, pollForOperatorMessages, sessionHydrated, sessionId]);

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
}
