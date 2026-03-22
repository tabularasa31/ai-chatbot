"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type DisclosureLevel } from "@/lib/api";

const OPTIONS: {
  value: DisclosureLevel;
  label: string;
  description: string;
}[] = [
  {
    value: "detailed",
    label: "Detailed",
    description:
      "Full technical detail from documentation — paths, diagnostics, vendor/tool names where relevant.",
  },
  {
    value: "standard",
    label: "Standard",
    description:
      "Plain language; avoids internal paths, stack traces, error vendor names, affected-user counts, internal team names.",
  },
  {
    value: "corporate",
    label: "Corporate",
    description:
      "Polished, non-technical tone; no ETAs, no deep technical or status-page detail; offer support contact when issues are ongoing.",
  },
];

export default function DisclosureSettingsPage() {
  const [level, setLevel] = useState<DisclosureLevel>("standard");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedOk, setSavedOk] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const d = await api.disclosure.get();
      setLevel(d.level);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave() {
    setSaving(true);
    setSavedOk(false);
    setError("");
    try {
      await api.disclosure.update({ level });
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Response controls</h1>
        <p className="text-sm text-slate-500 mt-1">
          One setting for your whole tenant: every chat (widget and API) uses this response style.
        </p>
      </div>

      {loading ? (
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      ) : (
        <>
          {error && (
            <div className="rounded-lg bg-red-50 text-red-600 text-sm px-3 py-2 border border-red-100">
              {error}
            </div>
          )}
          {savedOk && (
            <div className="rounded-lg bg-emerald-50 text-emerald-700 text-sm px-3 py-2 border border-emerald-100">
              Saved.
            </div>
          )}

          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold text-slate-800 mb-1">Response detail level</legend>
            {OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className={`flex gap-3 p-4 rounded-xl border cursor-pointer transition-colors ${
                  level === opt.value
                    ? "border-violet-400 bg-violet-50/50"
                    : "border-slate-200 bg-white hover:border-slate-300"
                }`}
              >
                <input
                  type="radio"
                  name="disclosure-level"
                  value={opt.value}
                  checked={level === opt.value}
                  onChange={() => setLevel(opt.value)}
                  className="mt-1"
                />
                <div>
                  <div className="font-medium text-slate-800">{opt.label}</div>
                  <div className="text-sm text-slate-500 mt-1">{opt.description}</div>
                </div>
              </label>
            ))}
          </fieldset>

          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="mt-6 px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50 hover:bg-violet-700"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </>
      )}
    </div>
  );
}
