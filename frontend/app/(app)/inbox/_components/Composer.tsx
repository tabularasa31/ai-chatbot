"use client";

import { useCallback, useState, type ButtonHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";
import { ApiError, api, HTTP_CONFLICT, type Thread } from "@/lib/api";
import { Button, FOCUS_RING } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";

type Action = "send" | "take" | "release" | "resolve";

const FAILURE_MESSAGE: Record<Action, string> = {
  send: "Couldn't send the reply. Try again.",
  take: "Couldn't take the conversation. Try again.",
  release: "Couldn't return the conversation to the bot. Try again.",
  resolve: "Couldn't resolve the ticket. Try again.",
};

const CONFLICT_MESSAGE = "Another operator took this conversation.";

const FIELD_CLASS = "w-full rounded-control border bg-surface px-3 text-ui text-fg placeholder:text-fg-faint";
const HELPER_ID = "composer-helper";
const ERROR_ID = "composer-error";

function ActionButton({
  loading = false,
  variant = "secondary",
  className,
  children,
  ...props
}: {
  loading?: boolean;
  variant?: "primary" | "secondary";
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type">) {
  return (
    <Button
      variant={variant}
      offset="surface"
      aria-busy={loading}
      className={cn("relative", className)}
      {...props}
    >
      {loading && <Loader2 size={16} aria-hidden="true" className="absolute animate-spin" />}
      <span className={cn(loading && "invisible")}>{children}</span>
    </Button>
  );
}

export function Composer({ thread, onChanged }: { thread: Thread; onChanged: () => Promise<unknown> }) {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<Action | null>(null);
  const [fieldError, setFieldError] = useState("");
  const [notice, setNotice] = useState("");
  const chatId = thread.chat.chat_id;
  const { ticket } = thread;
  const isLive = thread.handoff_state === "live";
  const ticketActive = ticket?.status === "open" || ticket?.status === "in_progress";

  const run = useCallback(
    async (name: Action, action: () => Promise<unknown>) => {
      setBusy(name);
      setFieldError("");
      setNotice("");
      try {
        await action();
      } catch (e) {
        if (e instanceof ApiError && e.status === HTTP_CONFLICT) setNotice(CONFLICT_MESSAGE);
        else if (name === "send") setFieldError(FAILURE_MESSAGE.send);
        else setNotice(FAILURE_MESSAGE[name]);
      }
      // A failed action can still have changed the chat (another operator took it): refresh either way.
      await onChanged();
      setBusy(null);
    },
    [onChanged],
  );

  const send = () => {
    const body = text.trim();
    if (!body || busy !== null) return;
    void run("send", async () => {
      await api.operator.reply(chatId, body);
      setText("");
    });
  };

  return (
    <div className="shrink-0 space-y-2 border-t border-line p-4">
      {notice && (
        <p role="alert" className="text-ui text-danger">
          {notice}
        </p>
      )}
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return;
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
          }
        }}
        rows={3}
        aria-label="Reply to the visitor"
        aria-describedby={fieldError ? `${ERROR_ID} ${HELPER_ID}` : HELPER_ID}
        aria-invalid={fieldError ? true : undefined}
        placeholder="Write a reply…"
        dir="auto"
        readOnly={busy !== null}
        className={cn(FIELD_CLASS, "py-2", fieldError ? "border-danger" : "border-line-strong", FOCUS_RING.surface)}
      />
      {fieldError && (
        <p id={ERROR_ID} role="alert" className="text-ui text-danger">
          {fieldError}
        </p>
      )}
      <p id={HELPER_ID} className="text-caption text-fg-muted">
        Enter to send, Shift+Enter for a new line
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {isLive ? (
          <ActionButton
            loading={busy === "release"}
            disabled={busy !== null}
            onClick={() => void run("release", () => api.operator.release(chatId))}
          >
            Return to bot
          </ActionButton>
        ) : (
          <ActionButton
            loading={busy === "take"}
            disabled={busy !== null}
            onClick={() => void run("take", () => api.operator.take(chatId))}
          >
            Take
          </ActionButton>
        )}
        {ticketActive && (
          <ActionButton disabled={busy !== null} onClick={() => setConfirming(true)}>
            Mark resolved
          </ActionButton>
        )}
        <ActionButton
          variant="primary"
          loading={busy === "send"}
          disabled={busy !== null || !text.trim()}
          className="ml-auto"
          onClick={send}
        >
          Send
        </ActionButton>
      </div>
      {confirming && ticket && ticketActive && (
        <div className="space-y-3 border-t border-line pt-3">
          <p className="text-ui text-fg">
            Closes ticket <span className="font-mono">{ticket.ticket_number}</span> and returns the conversation to the bot.
          </p>
          <div className="space-y-1">
            <label htmlFor="resolution-note" className="block text-ui font-medium text-fg">
              Resolution note (optional)
            </label>
            <input
              id="resolution-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              dir="auto"
              className={cn(FIELD_CLASS, "h-11 border-line-strong", FOCUS_RING.surface)}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <ActionButton disabled={busy !== null} onClick={() => setConfirming(false)}>
              Cancel
            </ActionButton>
            <ActionButton
              loading={busy === "resolve"}
              disabled={busy !== null}
              onClick={() =>
                void run("resolve", async () => {
                  await api.operator.resolve(chatId, note.trim() || null);
                  setNote("");
                  setConfirming(false);
                })
              }
            >
              Resolve ticket
            </ActionButton>
          </div>
        </div>
      )}
    </div>
  );
}
