"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const [hasOpenaiKey, setHasOpenaiKey] = useState(false);
  const [openaiKeyInput, setOpenaiKeyInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedOk, setSavedOk] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.clients.getMe()
      .then((c) => setHasOpenaiKey(c.has_openai_key ?? false))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  async function saveOpenaiKey() {
    setError("");
    const key = openaiKeyInput.trim();
    if (!key) return;
    if (!key.startsWith("sk-")) {
      setError("OpenAI API key must start with 'sk-'");
      return;
    }
    setSaving(true);
    try {
      await api.clients.update({ openai_api_key: key });
      setHasOpenaiKey(true);
      setOpenaiKeyInput("");
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function removeOpenaiKey() {
    setError("");
    setSaving(true);
    try {
      await api.clients.update({ openai_api_key: null });
      setHasOpenaiKey(false);
      setOpenaiKeyInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Agents</h1>
        <p className="text-sm text-slate-500 mt-1">Configure AI models and provider credentials</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">OpenAI API Key</h2>
          <p className="text-sm text-slate-500 mt-1">
            Used for embeddings and chat completions.{" "}
            <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener noreferrer" className="text-violet-600 hover:underline">
              Get yours at platform.openai.com
            </a>
          </p>
        </div>

        {hasOpenaiKey && (
          <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
            API key configured
          </div>
        )}

        {!hasOpenaiKey && (
          <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-100 px-3 py-2 rounded-lg">
            <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" />
            No API key — chat and embeddings are disabled
          </div>
        )}

        {savedOk && (
          <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            Saved.
          </div>
        )}

        <div className="space-y-2">
          <input
            type="password"
            placeholder="sk-..."
            value={openaiKeyInput}
            onChange={(e) => setOpenaiKeyInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveOpenaiKey()}
            className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 outline-none focus:border-slate-400 placeholder:text-slate-400"
          />
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={saveOpenaiKey}
              disabled={saving || !openaiKeyInput.trim()}
              className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
            >
              {saving ? "Saving…" : hasOpenaiKey ? "Update key" : "Save key"}
            </button>
            {hasOpenaiKey && (
              <button
                type="button"
                onClick={removeOpenaiKey}
                disabled={saving}
                className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
              >
                Remove key
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
