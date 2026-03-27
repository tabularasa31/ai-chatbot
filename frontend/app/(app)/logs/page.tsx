"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, type ChatSessionSummary, type ChatSessionLogs, type MessageFeedbackValue } from "@/lib/api";

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function truncateSessionId(id: string): string {
  if (id.length <= 11) return id;
  return id.slice(0, 8) + "…";
}

type LogMessage = ChatSessionLogs["messages"][number];

function MessageBubble({
  msg,
  onFeedbackUpdate,
}: {
  msg: LogMessage;
  onFeedbackUpdate: (msg: LogMessage, feedback: MessageFeedbackValue, idealAnswer?: string | null) => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showIdeal, setShowIdeal] = useState(false);
  const [idealText, setIdealText] = useState(msg.ideal_answer ?? "");

  const handleFeedback = useCallback(
    async (fb: MessageFeedbackValue) => {
      if (msg.role !== "assistant") return;
      setSaving(true);
      try {
        await onFeedbackUpdate(msg, fb, msg.ideal_answer);
        setSaved(true);
        setTimeout(() => setSaved(false), 2000);
      } finally {
        setSaving(false);
      }
    },
    [msg, onFeedbackUpdate]
  );

  const handleSaveIdeal = useCallback(async () => {
    if (msg.role !== "assistant") return;
    setSaving(true);
    try {
      await onFeedbackUpdate(msg, "down", idealText || null);
      setShowIdeal(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }, [msg, idealText, onFeedbackUpdate]);

  const isAssistant = msg.role === "assistant";
  const originalState = msg.content_original
    ? {
        label: "Original shown",
        className: "text-emerald-700",
      }
    : msg.content_original_available
      ? {
          label: "Original available",
          className: "text-amber-700",
        }
      : {
          label: "Original removed",
          className: "text-slate-500",
        };

  return (
    <div className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2 ${
          msg.role === "user"
            ? "bg-blue-600 text-white"
            : "bg-slate-200 text-slate-800"
        }`}
      >
        <p className="text-sm font-medium opacity-90">
          {msg.role === "user" ? "User" : "Assistant"}
        </p>
        <p className={`text-[11px] uppercase tracking-wide mt-1 ${originalState.className}`}>
          {originalState.label}
        </p>
        <p className="whitespace-pre-wrap text-sm mt-0.5">{msg.content}</p>
        {msg.content_original && (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-slate-800">
            <p className="text-[11px] uppercase tracking-wide text-amber-800">Original content</p>
            <p className="whitespace-pre-wrap text-sm mt-1">{msg.content_original}</p>
          </div>
        )}
        <p className="text-xs opacity-75 mt-1">
          {formatDateTime(msg.created_at)}
        </p>
        {isAssistant && (
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => handleFeedback("up")}
                disabled={saving}
                className={`p-1 rounded text-sm ${
                  msg.feedback === "up"
                    ? "bg-green-600 text-white"
                    : "bg-slate-300 hover:bg-slate-400 text-slate-700"
                }`}
                title="Good answer"
              >
                👍
              </button>
              <button
                type="button"
                onClick={() => handleFeedback("down")}
                disabled={saving}
                className={`p-1 rounded text-sm ${
                  msg.feedback === "down"
                    ? "bg-red-600 text-white"
                    : "bg-slate-300 hover:bg-slate-400 text-slate-700"
                }`}
                title="Bad answer"
              >
                👎
              </button>
            </div>
            <button
              type="button"
              onClick={() => setShowIdeal(!showIdeal)}
              className="text-xs text-violet-600 hover:underline"
            >
              {showIdeal ? "Cancel" : "Edit ideal answer"}
            </button>
            {saved && <span className="text-xs text-green-600">Saved</span>}
            {showIdeal && (
              <div className="w-full mt-2">
                <textarea
                  value={idealText}
                  onChange={(e) => setIdealText(e.target.value)}
                  placeholder="Ideal answer for training..."
                  className="w-full text-sm border border-slate-200 rounded-lg px-2 py-1.5 text-slate-800 outline-none focus:border-slate-400"
                  rows={3}
                />
                <button
                  type="button"
                  onClick={handleSaveIdeal}
                  disabled={saving}
                  className="mt-1 text-xs bg-violet-600 text-white px-2 py-1 rounded-md hover:bg-violet-700"
                >
                  Save ideal answer
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function LogsPageContent() {
  const searchParams = useSearchParams();
  const sessionFromUrl = searchParams.get("session");
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(sessionFromUrl);
  const [logs, setLogs] = useState<ChatSessionLogs | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [includeOriginal, setIncludeOriginal] = useState(false);
  const [deletingOriginal, setDeletingOriginal] = useState(false);
  const [confirmDeleteOriginal, setConfirmDeleteOriginal] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");

  useEffect(() => {
    if (sessionFromUrl) setSelectedSessionId(sessionFromUrl);
  }, [sessionFromUrl]);

  useEffect(() => {
    setConfirmDeleteOriginal(false);
    setActionMessage("");
  }, [selectedSessionId]);

  useEffect(() => {
    async function load() {
      setError("");
      setLoadingSessions(true);
      try {
        const [client, list] = await Promise.all([
          api.clients.getMe().catch(() => null),
          api.chat.listSessions(),
        ]);
        setIsAdmin(Boolean(client?.is_admin));
        setSessions(list);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load sessions");
      } finally {
        setLoadingSessions(false);
      }
    }
    load();
  }, []);

  useEffect(() => {
    const sid = selectedSessionId;
    if (!sid) {
      setLogs(null);
      return;
    }
    const sessionId: string = sid;
    async function load() {
      setError("");
      setLoadingLogs(true);
      setLogs(null);
      try {
        const data = await api.chat.getSessionLogs(sessionId, {
          includeOriginal: isAdmin && includeOriginal,
        });
        setLogs(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load logs");
      } finally {
        setLoadingLogs(false);
      }
    }
    load();
  }, [selectedSessionId, includeOriginal, isAdmin]);

  const selectedSession = sessions.find((s) => s.session_id === selectedSessionId);
  const lastActivity = selectedSession?.last_activity ?? logs?.messages?.[logs.messages.length - 1]?.created_at;
  const hasOriginalContent = Boolean(logs?.messages.some((msg) => msg.content_original_available));
  const hasVisibleOriginalContent = Boolean(logs?.messages.some((msg) => msg.content_original));
  const originalLifecycle = hasVisibleOriginalContent
    ? {
        label: "Original content visible",
        className: "bg-emerald-50 text-emerald-700 border-emerald-200",
      }
    : hasOriginalContent
      ? {
          label: "Original content available",
          className: "bg-amber-50 text-amber-800 border-amber-200",
        }
      : {
          label: "Original content removed",
          className: "bg-slate-100 text-slate-600 border-slate-200",
        };

  async function handleDeleteOriginal() {
    if (!selectedSessionId) return;
    setDeletingOriginal(true);
    setError("");
    setActionMessage("");
    try {
      const result = await api.chat.deleteSessionOriginal(selectedSessionId);
      const data = await api.chat.getSessionLogs(selectedSessionId, { includeOriginal: false });
      setIncludeOriginal(false);
      setLogs(data);
      setConfirmDeleteOriginal(false);
      setActionMessage(
        result.deleted_count > 0
          ? `Original content deleted from ${result.deleted_count} message(s).`
          : "Original content was already removed."
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete original content");
    } finally {
      setDeletingOriginal(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-slate-800">Chat logs</h1>

      <div className="flex flex-col md:flex-row gap-4">
        {/* Left: Sessions list */}
        <div className="w-full md:w-1/3 bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100">
            <h2 className="text-base font-semibold text-slate-800">Sessions</h2>
          </div>
          <div className="max-h-[400px] overflow-y-auto">
            {loadingSessions ? (
              <div className="p-4 text-slate-500 text-sm">Loading sessions…</div>
            ) : sessions.length === 0 ? (
              <div className="p-4 text-slate-500 text-sm">No sessions yet.</div>
            ) : (
              <ul className="divide-y divide-slate-100">
                {sessions.map((s) => (
                  <li key={s.session_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedSessionId(s.session_id)}
                      className={`w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors ${
                        selectedSessionId === s.session_id ? "bg-violet-50 border-l-2 border-violet-500" : ""
                      }`}
                    >
                      <p className="text-xs font-mono text-slate-500 truncate" title={s.session_id}>
                        {truncateSessionId(s.session_id)}
                      </p>
                      <p className="text-sm text-slate-800 truncate mt-0.5" title={s.last_question ?? ""}>
                        {s.last_question ?? "(no question)"}
                      </p>
                      <p className="text-xs text-slate-400 mt-1">
                        {formatDateTime(s.last_activity)}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Right: Messages view */}
        <div className="w-full md:w-2/3 bg-white rounded-xl border border-slate-200 overflow-hidden">
          {!selectedSessionId ? (
            <div className="p-8 text-center text-slate-500">
              Select a session to view conversation.
            </div>
          ) : (
            <>
              <div className="px-4 py-3 border-b border-slate-100">
                <p className="text-xs font-mono text-slate-500 break-all">
                  Session: {selectedSessionId}
                </p>
                <p className="text-sm text-slate-600 mt-1">
                  Messages: {selectedSession?.message_count ?? logs?.messages.length ?? 0}
                  {lastActivity && (
                    <> · Last activity: {formatDateTime(lastActivity)}</>
                  )}
                </p>
                {logs && logs.messages.length > 0 && (
                  <div className="mt-3">
                    <span
                      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${originalLifecycle.className}`}
                    >
                      {originalLifecycle.label}
                    </span>
                  </div>
                )}
                {isAdmin && (
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <label className="inline-flex items-center gap-2 text-sm text-slate-600">
                      <input
                        type="checkbox"
                        checked={includeOriginal}
                        onChange={(e) => setIncludeOriginal(e.target.checked)}
                      />
                      Show original content
                    </label>
                    <button
                      type="button"
                      onClick={() => setConfirmDeleteOriginal(true)}
                      disabled={deletingOriginal || !hasOriginalContent}
                      className="text-sm px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-700 disabled:opacity-40 hover:bg-slate-50"
                    >
                      {hasOriginalContent ? "Delete original content" : "Original already removed"}
                    </button>
                  </div>
                )}
                {isAdmin && confirmDeleteOriginal && hasOriginalContent && (
                  <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
                    <p className="text-sm font-medium text-amber-900">
                      Delete remaining original content for this session?
                    </p>
                    <p className="mt-1 text-sm text-amber-800">
                      This keeps the redacted chat history visible but removes the stored original text.
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={handleDeleteOriginal}
                        disabled={deletingOriginal}
                        className="px-3 py-1.5 rounded-lg bg-amber-600 text-white text-sm hover:bg-amber-700 disabled:opacity-50"
                      >
                        {deletingOriginal ? "Deleting…" : "Confirm delete"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDeleteOriginal(false)}
                        disabled={deletingOriginal}
                        className="px-3 py-1.5 rounded-lg border border-amber-200 bg-white text-amber-900 text-sm hover:bg-amber-100 disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
              <div className="p-4 max-h-[400px] overflow-y-auto">
                {error && (
                  <div className="mb-4 text-red-600 text-sm bg-red-50 border border-red-100 px-3 py-2 rounded-lg">
                    {error}
                  </div>
                )}
                {actionMessage && (
                  <div className="mb-4 text-emerald-700 text-sm bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
                    {actionMessage}
                  </div>
                )}
                {logs && logs.messages.length > 0 && !hasOriginalContent && (
                  <div className="mb-4 text-slate-600 text-sm bg-slate-50 border border-slate-200 px-3 py-2 rounded-lg">
                    Original content is no longer available for this session. The redacted transcript remains available.
                  </div>
                )}
                {loadingLogs ? (
                  <div className="text-slate-500 text-sm">Loading conversation…</div>
                ) : logs && logs.messages.length === 0 ? (
                  <p className="text-slate-500 text-sm">No messages in this session.</p>
                ) : (
                  <div className="space-y-4">
                    {logs?.messages.map((msg) => (
                      <MessageBubble
                        key={msg.id}
                        msg={msg}
                        onFeedbackUpdate={async (m, feedback, idealAnswer) => {
                          await api.chat.setFeedback(m.id, feedback, idealAnswer);
                          const data = await api.chat.getSessionLogs(selectedSessionId!, {
                            includeOriginal: isAdmin && includeOriginal,
                          });
                          setLogs(data);
                        }}
                      />
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function LogsPage() {
  return (
    <Suspense fallback={<div className="text-slate-500">Loading…</div>}>
      <LogsPageContent />
    </Suspense>
  );
}
