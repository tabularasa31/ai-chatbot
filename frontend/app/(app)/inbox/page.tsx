"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, type HandoffState, type InboxRow, type Thread, type ThreadMessage } from "@/lib/api";
import { useClientMe, useInbox, useThread } from "@/hooks/useApi";
import { INBOX_CHANGED_EVENT } from "@/components/Sidebar";

type Scope = "attention" | "all";

const INBOX_REFRESH_MS = 15_000;
const THREAD_REFRESH_MS: Record<HandoffState, number> = {
  live: 4_000,
  waiting: 8_000,
  bot: 15_000,
};

// The API stamps naive UTC without a zone designator; read it as UTC rather
// than as the browser's local time.
function parseUtc(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

function formatDateTime(iso: string): string {
  return parseUtc(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

function formatTime(iso: string): string {
  return parseUtc(iso).toLocaleTimeString(undefined, { timeStyle: "short" });
}

function waitingFor(since: string, now: number): string {
  const minutes = Math.max(0, Math.floor((now - parseUtc(since).getTime()) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h`;
  return `${Math.floor(hours / 24)} d`;
}

function visitorLabel(row: { visitor_name: string | null; visitor_email: string | null }): string {
  return row.visitor_name || row.visitor_email || "Anonymous visitor";
}

function stateClass(state: HandoffState): string {
  return {
    waiting: "bg-amber-100 text-amber-900",
    live: "bg-emerald-100 text-emerald-900",
    bot: "bg-slate-100 text-slate-600",
  }[state];
}

function stateLabel(state: HandoffState): string {
  return { waiting: "Waiting", live: "Live", bot: "Bot" }[state];
}

function ticketStatusLabel(status: string): string {
  return status.replace("_", " ");
}

function InboxRowItem({
  row,
  selected,
  now,
  onSelect,
}: {
  row: InboxRow;
  selected: boolean;
  now: number;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className={`w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors ${
          selected ? "bg-violet-50 border-l-2 border-violet-500" : ""
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-medium text-slate-800 truncate" title={row.visitor_email ?? ""}>
            {visitorLabel(row)}
          </p>
          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${stateClass(row.handoff_state)}`}>
            {stateLabel(row.handoff_state)}
          </span>
        </div>
        <p className="text-sm text-slate-600 truncate mt-0.5" title={row.last_message_preview ?? ""}>
          {row.last_message_role === "operator" ? "You: " : row.last_message_role === "assistant" ? "Bot: " : ""}
          {row.last_message_preview ?? "(no messages)"}
        </p>
        <p className="text-xs text-slate-400 mt-1 truncate">
          {row.handoff_state === "waiting" && row.waiting_since
            ? `Waiting ${waitingFor(row.waiting_since, now)} · unassigned`
            : row.assigned_operator_email
              ? `Held by ${row.assigned_operator_email}`
              : formatDateTime(row.last_activity)}
          {row.ticket && ` · ${row.ticket.ticket_number}`}
        </p>
      </button>
    </li>
  );
}

function MessageBubble({ msg }: { msg: ThreadMessage }) {
  const isUser = msg.role === "user";
  const isOperator = msg.role === "operator";
  const bubble = isUser
    ? "bg-blue-600 text-white"
    : isOperator
      ? "bg-violet-100 text-violet-950 border border-violet-200"
      : "bg-slate-200 text-slate-800";
  const label = isUser ? "Visitor" : isOperator ? msg.author_label ?? "Operator" : "Bot";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[80%] rounded-lg px-4 py-2 ${bubble}`}>
        <p className="text-xs font-medium opacity-80 truncate" title={label}>
          {label}
        </p>
        <p className="whitespace-pre-wrap text-sm mt-0.5">{msg.content}</p>
        <p className="text-[11px] opacity-70 mt-1">{formatTime(msg.created_at)}</p>
      </div>
    </div>
  );
}

function ThreadHeader({ thread }: { thread: Thread }) {
  const ticket = thread.ticket;
  return (
    <div className="px-4 py-3 border-b border-slate-100 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-slate-800">{visitorLabel(thread)}</p>
        {thread.visitor_name && thread.visitor_email && (
          <span className="text-xs text-slate-500">{thread.visitor_email}</span>
        )}
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${stateClass(thread.handoff_state)}`}>
          {stateLabel(thread.handoff_state)}
        </span>
        {thread.chat.assigned_operator_email && (
          <span className="text-xs text-slate-500">held by {thread.chat.assigned_operator_email}</span>
        )}
        {thread.chat_ended && <span className="text-xs text-slate-400">visitor closed the chat</span>}
      </div>
      {ticket ? (
        <p className="text-xs text-slate-500">
          <span className="font-mono text-slate-700">{ticket.ticket_number}</span>
          {" · "}
          {ticketStatusLabel(ticket.status)} · {ticket.priority} priority · {ticket.trigger.replace("_", " ")}
          {ticket.user_note && (
            <>
              {" · "}
              <span className="italic" title={ticket.user_note}>
                note: {ticket.user_note}
              </span>
            </>
          )}
        </p>
      ) : (
        <p className="text-xs text-slate-400">No escalation ticket in this conversation.</p>
      )}
    </div>
  );
}

function Composer({
  thread,
  onChanged,
}: {
  thread: Thread;
  onChanged: () => Promise<unknown>;
}) {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const chatId = thread.chat.chat_id;
  const isLive = thread.handoff_state === "live";
  const ticketActive = thread.ticket?.status === "open" || thread.ticket?.status === "in_progress";

  const run = useCallback(
    async (name: string, action: () => Promise<unknown>) => {
      setBusy(name);
      setError("");
      try {
        await action();
        await onChanged();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Something went wrong");
      } finally {
        setBusy(null);
      }
    },
    [onChanged]
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
    <div className="border-t border-slate-100 px-4 py-3 space-y-2">
      {error && (
        <div className="text-red-600 text-sm bg-red-50 border border-red-100 px-3 py-2 rounded-lg">{error}</div>
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
        placeholder="Reply to the visitor… (Enter to send, Shift+Enter for a new line)"
        className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-300"
        readOnly={busy !== null}
      />
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={send}
          disabled={busy !== null || !text.trim()}
          className="rounded-lg bg-violet-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
        >
          {busy === "send" ? "Sending…" : "Send"}
        </button>
        {isLive ? (
          <button
            type="button"
            onClick={() => void run("release", () => api.operator.release(chatId))}
            disabled={busy !== null}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {busy === "release" ? "Returning…" : "Return to bot"}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void run("take", () => api.operator.take(chatId))}
            disabled={busy !== null}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {busy === "take" ? "Taking…" : "Take"}
          </button>
        )}
        {(isLive || ticketActive) && (
          <button
            type="button"
            onClick={() => setShowNote((v) => !v)}
            disabled={busy !== null}
            className="rounded-lg border border-emerald-200 px-3 py-1.5 text-sm text-emerald-800 hover:bg-emerald-50 disabled:opacity-50"
          >
            Mark resolved
          </button>
        )}
      </div>
      {showNote && (
        <div className="rounded-lg border border-emerald-100 bg-emerald-50/50 p-3 space-y-2">
          <p className="text-xs text-slate-600">
            Closes the ticket and hands the conversation back to the bot. A note is optional.
          </p>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Resolution note (optional)"
            className="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() =>
                void run("resolve", async () => {
                  await api.operator.resolve(chatId, note.trim() || null);
                  setNote("");
                  setShowNote(false);
                })
              }
              disabled={busy !== null}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {busy === "resolve" ? "Resolving…" : "Confirm resolved"}
            </button>
            <button
              type="button"
              onClick={() => setShowNote(false)}
              className="rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ThreadView({
  sessionId,
  canOperate,
  onChanged,
}: {
  sessionId: string;
  canOperate: boolean | undefined;
  onChanged: () => Promise<unknown>;
}) {
  const [refreshMs, setRefreshMs] = useState(THREAD_REFRESH_MS.bot);
  const { data: thread, error, isLoading, mutate } = useThread(sessionId, refreshMs);
  const scrollRef = useRef<HTMLDivElement>(null);
  const messageCount = thread?.messages.length ?? 0;
  const handoffState = thread?.handoff_state;

  useEffect(() => {
    if (handoffState) setRefreshMs(THREAD_REFRESH_MS[handoffState]);
  }, [handoffState]);

  // Follow new messages only when the operator is already at the bottom;
  // someone reading history must not be yanked down by a poll.
  const stickToBottom = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messageCount]);

  const changed = useCallback(async () => {
    await Promise.all([mutate(), onChanged()]);
  }, [mutate, onChanged]);

  if (isLoading && !thread) {
    return <div className="p-8 text-slate-500 text-sm">Loading conversation…</div>;
  }
  if (!thread) {
    return (
      <div className="p-8 text-red-600 text-sm">
        {error instanceof Error ? error.message : "Failed to load the conversation"}
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-200px)] min-h-[420px]">
      <ThreadHeader thread={thread} />
      {error && (
        <div className="mx-4 mt-3 text-red-600 text-sm bg-red-50 border border-red-100 px-3 py-2 rounded-lg">
          {error instanceof Error ? error.message : "Failed to refresh the conversation"}
        </div>
      )}
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="flex-1 overflow-y-auto p-4"
      >
        {thread.messages.length === 0 ? (
          <p className="text-slate-500 text-sm">No messages yet.</p>
        ) : (
          <div className="space-y-4">
            {thread.messages.map((msg, index) => {
              const prev = index > 0 ? thread.messages[index - 1] : null;
              const newConversation = prev != null && prev.chat_id !== msg.chat_id;
              return (
                <div key={msg.id} className="space-y-4">
                  {newConversation && (
                    <div className="flex items-center gap-3 py-1">
                      <div className="h-px flex-1 bg-slate-200" />
                      <span className="text-xs uppercase tracking-wide text-slate-400">New conversation</span>
                      <div className="h-px flex-1 bg-slate-200" />
                    </div>
                  )}
                  <MessageBubble msg={msg} />
                </div>
              );
            })}
          </div>
        )}
      </div>
      {canOperate === true && <Composer thread={thread} onChanged={changed} />}
      {canOperate === false && (
        <div className="border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
          Read-only. Answering, taking and resolving conversations needs an operator seat.
        </div>
      )}
    </div>
  );
}

function InboxPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionFromUrl = searchParams.get("session");
  const [scope, setScope] = useState<Scope>("attention");
  const [selected, setSelected] = useState<string | null>(sessionFromUrl);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    setSelected(sessionFromUrl);
  }, [sessionFromUrl]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const { data: me } = useClientMe();
  const { data: inbox, error, isLoading, mutate } = useInbox(scope, INBOX_REFRESH_MS);
  const canOperate = me ? Boolean(me.has_seat) : undefined;

  const select = useCallback(
    (sessionId: string) => {
      setSelected(sessionId);
      router.replace(`/inbox?session=${sessionId}`);
    },
    [router]
  );

  const rows = useMemo(() => inbox?.items ?? [], [inbox]);
  const refresh = useCallback(async () => {
    await mutate();
    window.dispatchEvent(new Event(INBOX_CHANGED_EVENT));
  }, [mutate]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-800">Inbox</h1>
          <p className="text-slate-500 text-sm mt-1">
            Conversations that need a person, and everything else your bot is handling.
          </p>
        </div>
        <div className="flex rounded-lg border border-slate-200 bg-white p-0.5 text-sm">
          {(["attention", "all"] as Scope[]).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setScope(s)}
              className={`rounded-md px-3 py-1 ${
                scope === s ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              {s === "attention" ? `Needs attention${inbox ? ` (${inbox.attention_count})` : ""}` : "All"}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="text-red-600 text-sm bg-red-50 border border-red-100 px-3 py-2 rounded-lg">
          {error instanceof Error ? error.message : "Failed to load the inbox"}
        </div>
      )}

      <div className="flex flex-col md:flex-row gap-4">
        <div className="w-full md:w-1/3 bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="max-h-[calc(100vh-200px)] min-h-[420px] overflow-y-auto">
            {isLoading && !inbox ? (
              <div className="p-4 text-slate-500 text-sm">Loading…</div>
            ) : rows.length === 0 ? (
              <div className="p-4 text-slate-500 text-sm">
                {scope === "attention" ? "Nobody is waiting. All conversations are with the bot." : "No conversations yet."}
              </div>
            ) : (
              <ul className="divide-y divide-slate-100">
                {rows.map((row) => (
                  <InboxRowItem
                    key={row.session_id}
                    row={row}
                    now={now}
                    selected={selected === row.session_id}
                    onSelect={() => select(row.session_id)}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="w-full md:w-2/3 bg-white rounded-xl border border-slate-200 overflow-hidden">
          {!selected ? (
            <div className="p-8 text-center text-slate-500">Select a conversation.</div>
          ) : (
            <ThreadView key={selected} sessionId={selected} canOperate={canOperate} onChanged={refresh} />
          )}
        </div>
      </div>
    </div>
  );
}

export default function InboxPage() {
  return (
    <Suspense fallback={<div className="text-slate-500">Loading…</div>}>
      <InboxPageContent />
    </Suspense>
  );
}
