"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type PrivacyConfigResponse } from "@/lib/api";

const OPTIONAL_TYPES: Array<{
  value: PrivacyConfigResponse["optional_entity_types"][number];
  label: string;
  description: string;
}> = [
  {
    value: "ID_DOC",
    label: "Identity documents",
    description: "Passport, INN and similar identifiers are redacted before external delivery.",
  },
  {
    value: "IP",
    label: "IP addresses",
    description: "IPv4 addresses are masked in outbound text and safe views.",
  },
  {
    value: "URL_TOKEN",
    label: "URLs with tokens",
    description: "Links containing token-like query params are replaced with placeholders.",
  },
];

export default function PrivacySettingsPage() {
  const [config, setConfig] = useState<PrivacyConfigResponse>({ optional_entity_types: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedOk, setSavedOk] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const data = await api.privacy.get();
      setConfig(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load privacy settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave() {
    setSaving(true);
    setError("");
    setSavedOk(false);
    try {
      const updated = await api.privacy.update(config);
      setConfig(updated);
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save privacy settings");
    } finally {
      setSaving(false);
    }
  }

  function toggleType(value: PrivacyConfigResponse["optional_entity_types"][number]) {
    setConfig((current) => {
      const enabled = current.optional_entity_types.includes(value);
      return {
        optional_entity_types: enabled
          ? current.optional_entity_types.filter((item) => item !== value)
          : [...current.optional_entity_types, value].sort(),
      };
    });
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Privacy</h1>
        <p className="text-sm text-slate-500 mt-1">
          Control regex-based outbound redaction for optional entity types. Core protections stay on for every tenant.
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

          <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-4">
            <div>
              <h2 className="text-base font-semibold text-slate-800">Always on</h2>
              <p className="text-sm text-slate-500 mt-1">
                Email, phone, API key, password, and payment card redaction cannot be disabled.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {["EMAIL", "PHONE", "API_KEY", "PASSWORD", "CARD"].map((item) => (
                <span
                  key={item}
                  className="inline-flex items-center rounded-full bg-emerald-50 border border-emerald-200 px-3 py-1 text-xs font-medium text-emerald-800"
                >
                  {item}
                </span>
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-4">
            <div>
              <h2 className="text-base font-semibold text-slate-800">Optional regex redaction</h2>
              <p className="text-sm text-slate-500 mt-1">
                These controls affect safe storage and external delivery for new messages and tickets.
              </p>
            </div>

            <div className="space-y-3">
              {OPTIONAL_TYPES.map((opt) => {
                const checked = config.optional_entity_types.includes(opt.value);
                return (
                  <label
                    key={opt.value}
                    className={`flex gap-3 p-4 rounded-xl border cursor-pointer transition-colors ${
                      checked
                        ? "border-violet-400 bg-violet-50/50"
                        : "border-slate-200 bg-white hover:border-slate-300"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleType(opt.value)}
                      className="mt-1"
                    />
                    <div>
                      <div className="font-medium text-slate-800">{opt.label}</div>
                      <div className="text-sm text-slate-500 mt-1">{opt.description}</div>
                    </div>
                  </label>
                );
              })}
            </div>

            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50 hover:bg-violet-700"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </section>
        </>
      )}
    </div>
  );
}
