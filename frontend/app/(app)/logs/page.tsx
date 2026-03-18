"use client";

import { useState } from "react";
import { api } from "@/lib/api";

type Message = {
  id: number;
  role: string;
  content: string;
  created_at: string;
};

export default function LogsPage() {
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!sessionId.trim()) return;
    setError("");
    setLoading(true);
    setMessages(null);
    try {
      const data = await api.chat.getHistory(sessionId.trim());
      setMessages(data.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load history");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-slate-800">Chat logs</h1>

      <div className="bg-white rounded-lg shadow-md p-6">
        <p className="text-slate-600 text-sm mb-4">
          Enter a session ID to view chat history. Session IDs are returned when you use the chat API.
        </p>
        <form onSubmit={handleSubmit} className="flex gap-2 flex-wrap">
          <input
            type="text"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            placeholder="Session ID (e.g. uuid)"
            className="flex-1 min-w-[200px] px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-600 focus:border-transparent text-slate-800"
          />
          <button
            type="submit"
            disabled={loading}
            className="px-4 py-2 bg-blue-600 text-white font-medium rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Loading..." : "Load"}
          </button>
        </form>
        {error && (
          <div className="mt-4 text-red-600 text-sm bg-red-50 px-3 py-2 rounded-md">
            {error}
          </div>
        )}
      </div>

      {messages !== null && (
        <div className="bg-white rounded-lg shadow-md overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200">
            <h2 className="text-lg font-medium text-slate-800">Messages</h2>
          </div>
          <div className="p-6 space-y-4">
            {messages.length === 0 ? (
              <p className="text-slate-600 text-sm">No messages in this session.</p>
            ) : (
              messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[80%] rounded-lg px-4 py-2 ${
                      msg.role === "user"
                        ? "bg-blue-600 text-white"
                        : "bg-slate-200 text-slate-800"
                    }`}
                  >
                    <p className="text-sm text-slate-600 mb-1">
                      {msg.role === "user" ? "You" : "Assistant"}
                    </p>
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                    <p className="text-xs opacity-75 mt-1">
                      {new Date(msg.created_at).toLocaleString()}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
