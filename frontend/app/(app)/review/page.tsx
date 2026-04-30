"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, type BadAnswerItem } from "@/lib/api";

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
  const [items, setItems] = useState<BadAnswerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const list = await api.chat.listBadAnswers(50, 0);
      setItems(list);
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
            <BadAnswerCard key={item.message_id} item={item} onUpdate={load} />
          ))}
        </div>
      )}
    </div>
  );
}

function BadAnswerCard({
  item,
  onUpdate,
}: {
  item: BadAnswerItem;
  onUpdate: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [idealText, setIdealText] = useState(item.ideal_answer ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

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
      </div>
    </div>
  );
}
