"use client";

import { useEffect, useState } from "react";
import { api, type ChatSessionSummary, type ChatSessionLogs } from "@/lib/api";

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

export default function LogsPage() {
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [logs, setLogs] = useState<ChatSessionLogs | null>(null);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [error, setError] = useState("");

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
        const data = await api.chat.getSessionLogs(sessionId);
        setLogs(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load logs");
      } finally {
        setLoadingLogs(false);
      }
    }
    load();
  }, [selectedSessionId]);

  const selectedSession = sessions.find((s) => s.session_id === selectedSessionId);
  const lastActivity = selectedSession?.last_activity ?? logs?.messages?.[logs.messages.length - 1]?.created_at;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-slate-800">Chat logs</h1>

      <div className="flex flex-col md:flex-row gap-4">
        {/* Left: Sessions list */}
        <div className="w-full md:w-1/3 bg-white rounded-lg shadow-md overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-200">
            <h2 className="text-lg font-medium text-slate-800">Sessions</h2>
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
                        selectedSessionId === s.session_id ? "bg-blue-50 border-l-4 border-blue-600" : ""
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
        <div className="w-full md:w-2/3 bg-white rounded-lg shadow-md overflow-hidden">
          {!selectedSessionId ? (
            <div className="p-8 text-center text-slate-500">
              Select a session to view conversation.
            </div>
          ) : (
            <>
              <div className="px-4 py-3 border-b border-slate-200">
                <p className="text-xs font-mono text-slate-500 break-all">
                  Session: {selectedSessionId}
                </p>
                <p className="text-sm text-slate-600 mt-1">
                  Messages: {selectedSession?.message_count ?? logs?.messages.length ?? 0}
                  {lastActivity && (
                    <> · Last activity: {formatDateTime(lastActivity)}</>
                  )}
                </p>
              </div>
              <div className="p-4 max-h-[400px] overflow-y-auto">
                {error && (
                  <div className="mb-4 text-red-600 text-sm bg-red-50 px-3 py-2 rounded-md">
                    {error}
                  </div>
                )}
                {loadingLogs ? (
                  <div className="text-slate-500 text-sm">Loading conversation…</div>
                ) : logs && logs.messages.length === 0 ? (
                  <p className="text-slate-500 text-sm">No messages in this session.</p>
                ) : (
                  <div className="space-y-4">
                    {logs?.messages.map((msg, i) => (
                      <div
                        key={i}
                        className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                      >
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
                          <p className="whitespace-pre-wrap text-sm mt-0.5">{msg.content}</p>
                          <p className="text-xs opacity-75 mt-1">
                            {formatDateTime(msg.created_at)}
                          </p>
                        </div>
                      </div>
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
