"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, type BadAnswerItem, type ChatDebugResponse } from "@/lib/api";

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function truncateSessionId(id: string): string {
  if (id.length <= 12) return id;
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

export default function ReviewPage() {
  const [botId, setBotId] = useState("");
  const [items, setItems] = useState<BadAnswerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const [list, currentClient] = await Promise.all([
        api.chat.listBadAnswers(50, 0),
        api.clients.getMe(),
      ]);
      setItems(list);
      setBotId(currentClient.public_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load bad answers");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-slate-800">Review bad answers</h1>
      <p className="text-slate-500 text-sm">
        Assistant answers marked as 👎. Add ideal answers for future training.
      </p>

      {error && (
        <div className="text-red-600 text-sm bg-red-50 border border-red-100 px-3 py-2 rounded-lg">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-slate-500 text-sm">Loading…</div>
      ) : items.length === 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-slate-500">
          No bad answers yet. Mark answers with 👎 in{" "}
          <Link href="/logs" className="text-violet-600 hover:underline">
            Logs
          </Link>{" "}
          to see them here.
        </div>
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <BadAnswerCard key={item.message_id} item={item} botId={botId} onUpdate={load} />
          ))}
        </div>
      )}
    </div>
  );
}

type DebugState =
  | null
  | { loading: true }
  | { loading: false; data: ChatDebugResponse }
  | { loading: false; error: string };

function BadAnswerCard({
  item,
  botId,
  onUpdate,
}: {
  item: BadAnswerItem;
  botId: string;
  onUpdate: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [idealText, setIdealText] = useState(item.ideal_answer ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [debugState, setDebugState] = useState<DebugState>(null);
  const [showDebug, setShowDebug] = useState(false);

  const loadDebug = useCallback(async () => {
    if (!item.question) return;
    if (!botId) {
      setDebugState({
        loading: false,
        error: "Failed to load bot context for retrieval debug.",
      });
      return;
    }
    setDebugState({ loading: true });
    try {
      const data = await api.chat.debug(item.question, botId);
      setDebugState({ loading: false, data });
    } catch (err) {
      setDebugState({
        loading: false,
        error: err instanceof Error ? err.message : "Failed to load debug",
      });
    }
  }, [botId, item.question]);

  const handleToggleDebug = useCallback(() => {
    if (showDebug) {
      setShowDebug(false);
    } else {
      if (debugState === null) {
        loadDebug();
      }
      setShowDebug(true);
    }
  }, [showDebug, debugState, loadDebug]);

  const handleRetryDebug = useCallback(() => {
    loadDebug();
  }, [loadDebug]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await api.chat.setFeedback(item.message_id, "down", idealText || null);
      setEditing(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      onUpdate();
    } finally {
      setSaving(false);
    }
  }, [item.message_id, idealText, onUpdate]);

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 mb-2">
        <span>{formatDateTime(item.created_at)}</span>
        <span>·</span>
        <Link
          href={`/logs?session=${item.session_id}`}
          className="text-violet-600 hover:underline font-mono"
          title={item.session_id}
        >
          Session {truncateSessionId(item.session_id)}
        </Link>
      </div>
      <div className="space-y-2">
        <div>
          <p className="text-xs font-medium text-slate-500 uppercase">Question</p>
          <p className="text-sm text-slate-800">{item.question ?? "(no question)"}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-slate-500 uppercase">Answer</p>
          <p className="text-sm text-slate-800 whitespace-pre-wrap">{item.answer}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-slate-500 uppercase">Ideal answer</p>
          {editing ? (
            <div className="mt-1">
              <textarea
                value={idealText}
                onChange={(e) => setIdealText(e.target.value)}
                placeholder="Enter ideal answer for training..."
                aria-label="Ideal answer for training"
                className="w-full text-sm border border-slate-200 rounded-lg px-2 py-1.5 text-slate-800 outline-none focus:border-slate-400"
                rows={4}
              />
              <div className="flex gap-2 mt-2">
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saving}
                  className="text-sm bg-violet-600 text-white px-3 py-1.5 rounded-lg hover:bg-violet-700"
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                <button
                  type="button"
                  onClick={() => setEditing(false)}
                  className="text-sm text-slate-600 hover:text-slate-800"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-2 mt-1">
              <p className="text-sm text-slate-800 whitespace-pre-wrap flex-1">
                {item.ideal_answer ?? (
                  <span className="text-slate-400 italic">Not set</span>
                )}
              </p>
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="text-xs text-violet-600 hover:underline shrink-0"
              >
                {item.ideal_answer ? "Edit" : "Add"}
              </button>
            </div>
          )}
          {saved && <span className="text-xs text-green-600 ml-2">Saved</span>}
        </div>
        <div>
          <p className="text-xs font-medium text-slate-500 uppercase mb-1">Retrieval debug</p>
          {item.question == null || item.question === "" ? (
            <span className="text-xs text-slate-400 italic">No question for debug</span>
          ) : (
            <>
              <button
                type="button"
                onClick={handleToggleDebug}
                className="text-xs text-violet-600 hover:underline"
              >
                {showDebug ? "Hide debug" : "Show debug"}
              </button>
              {debugState?.loading && (
                <span className="text-xs text-slate-500 ml-2">Loading…</span>
              )}
              {showDebug && debugState && !debugState.loading && "error" in debugState && (
                <div className="mt-2 text-xs text-red-600">
                  {debugState.error}
                  <button
                    type="button"
                    onClick={handleRetryDebug}
                    className="ml-2 text-violet-600 hover:underline"
                  >
                    Retry
                  </button>
                </div>
              )}
              {showDebug && debugState && !debugState.loading && "data" in debugState && (
                <div className="mt-2 rounded border border-slate-200 bg-slate-50 p-2 text-sm">
                  {debugState.data.debug.chunks.length === 0 ? (
                    <div className="text-slate-500 text-xs">No chunks retrieved.</div>
                  ) : (
                    <ul className="space-y-1">
                      {debugState.data.debug.chunks.map((chunk, i) => (
                        <li
                          key={`${chunk.document_id}-${i}`}
                          className="border-t border-slate-200 pt-1 first:border-t-0 first:pt-0"
                        >
                          <div className="text-xs text-slate-500">
                            Doc: {chunk.document_id} | score: {chunk.score?.toFixed(4) ?? "–"}
                          </div>
                          <div className="text-xs text-slate-800 whitespace-pre-wrap">
                            {chunk.preview}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
