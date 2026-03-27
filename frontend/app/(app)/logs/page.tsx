"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, type ChatSessionSummary, type ChatSessionLogs, type MessageFeedbackValue } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

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
        {msg.content_original_available && (
          <p className="text-[11px] uppercase tracking-wide mt-1 opacity-70">
            {msg.content_original ? "Original view" : "Safe view only"}
          </p>
        )}
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
  const [adminLoaded, setAdminLoaded] = useState(false);
  const [includeOriginal, setIncludeOriginal] = useState(false);
  const [deletingOriginal, setDeletingOriginal] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [error, setError] = useState("");
  const skipNextLogsReload = useRef(false);

  useEffect(() => {
    if (sessionFromUrl) setSelectedSessionId(sessionFromUrl);
  }, [sessionFromUrl]);

  useEffect(() => {
    async function load() {
      try {
        const client = await api.clients.getMe().catch(() => null);
        setIsAdmin(Boolean(client?.is_admin));
      } finally {
        setAdminLoaded(true);
      }
    }
    load();
  }, []);

  useEffect(() => {
    async function load() {
      setError("");
      setLoadingSessions(true);
      try {
        const list = await api.chat.listSessions();
        setSessions(list);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load sessions");
      } finally {
        setLoadingSessions(false);
      }
    }
    load();
  }, []);

  const loadSessionLogs = useCallback(
    async (sessionId: string, includeOriginalValue: boolean) => {
      setError("");
      setLoadingLogs(true);
      setLogs(null);
      try {
        const data = await api.chat.getSessionLogs(sessionId, {
          includeOriginal: isAdmin && includeOriginalValue,
        });
        setLogs(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load logs");
      } finally {
        setLoadingLogs(false);
      }
    },
    [isAdmin]
  );

  useEffect(() => {
    if (!adminLoaded) return;
    if (skipNextLogsReload.current) {
      skipNextLogsReload.current = false;
      return;
    }
    const sid = selectedSessionId;
    if (!sid) {
      setLogs(null);
      return;
    }
    void loadSessionLogs(sid, includeOriginal);
  }, [selectedSessionId, includeOriginal, adminLoaded, loadSessionLogs]);

  const selectedSession = sessions.find((s) => s.session_id === selectedSessionId);
  const lastActivity = selectedSession?.last_activity ?? logs?.messages?.[logs.messages.length - 1]?.created_at;
  const hasOriginalContent = Boolean(logs?.messages.some((msg) => msg.content_original_available));

  async function handleDeleteOriginal() {
    if (!selectedSessionId) return;
    if (!window.confirm("Delete the stored original content for this session? This cannot be undone.")) {
      return;
    }
    setDeletingOriginal(true);
    setError("");
    try {
      await api.chat.deleteSessionOriginal(selectedSessionId);
      skipNextLogsReload.current = includeOriginal;
      if (includeOriginal) {
        setIncludeOriginal(false);
      }
      await loadSessionLogs(selectedSessionId, false);
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
                      onClick={handleDeleteOriginal}
                      disabled={deletingOriginal || !hasOriginalContent}
                      className="text-sm px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-700 disabled:opacity-40 hover:bg-slate-50"
                    >
                      {deletingOriginal ? "Deleting…" : "Delete original content"}
                    </button>
                  </div>
                )}
              </div>
              <div className="p-4 max-h-[400px] overflow-y-auto">
                {error && (
                  <div className="mb-4 text-red-600 text-sm bg-red-50 border border-red-100 px-3 py-2 rounded-lg">
                    {error}
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
