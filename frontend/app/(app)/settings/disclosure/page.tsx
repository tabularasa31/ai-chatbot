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
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-[#0A0A0F] mb-2">Response controls</h1>
      <p className="text-sm text-gray-600 mb-6">
        One setting for your whole tenant: every chat (widget and API) uses this response style. Same rules for
        all end-users.
      </p>

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <>
          {error && (
            <div className="mb-4 rounded-md bg-red-50 text-red-800 text-sm px-3 py-2 border border-red-100">
              {error}
            </div>
          )}
          {savedOk && (
            <div className="mb-4 rounded-md bg-emerald-50 text-emerald-800 text-sm px-3 py-2 border border-emerald-100">
              Saved.
            </div>
          )}

          <fieldset className="space-y-4">
            <legend className="text-sm font-medium text-[#0A0A0F] mb-3">Response detail level</legend>
            {OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className={`flex gap-3 p-4 rounded-lg border cursor-pointer transition-colors ${
                  level === opt.value
                    ? "border-[#E879F9] bg-[#FAF5FF]/50"
                    : "border-gray-200 bg-white hover:border-gray-300"
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
                  <div className="font-medium text-[#0A0A0F]">{opt.label}</div>
                  <div className="text-sm text-gray-600 mt-1">{opt.description}</div>
                </div>
              </label>
            ))}
          </fieldset>

          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="mt-6 px-4 py-2 rounded-md bg-[#0A0A0F] text-[#FAF5FF] text-sm font-medium disabled:opacity-50 hover:bg-[#1a1a24]"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </>
      )}
    </div>
  );
}
