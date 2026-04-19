"use client";

import { useEffect, useState } from "react";
import { api, type DisclosureLevel, type BotResponse } from "@/lib/api";

const DISCLOSURE_OPTIONS: {
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

export default function SettingsPage() {
  const [hasOpenaiKey, setHasOpenaiKey] = useState(false);
  const [openaiKeyInput, setOpenaiKeyInput] = useState("");
  const [supportEmailInput, setSupportEmailInput] = useState("");
  const [escalationLanguageInput, setEscalationLanguageInput] = useState("");
  const [fallbackEmail, setFallbackEmail] = useState<string | null>(null);
  const [level, setLevel] = useState<DisclosureLevel>("standard");
  const [defaultBot, setDefaultBot] = useState<BotResponse | null>(null);
  const [keySaving, setKeySaving] = useState(false);
  const [supportSaving, setSupportSaving] = useState(false);
  const [disclosureSaving, setDisclosureSaving] = useState(false);
  const [error, setError] = useState("");
  const [keySavedOk, setKeySavedOk] = useState(false);
  const [supportSavedOk, setSupportSavedOk] = useState(false);
  const [disclosureSavedOk, setDisclosureSavedOk] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.clients.getMe(), api.support.get(), api.bots.list()])
      .then(async ([client, support, bots]) => {
        setHasOpenaiKey(client.has_openai_key ?? false);
        setSupportEmailInput(support.l2_email ?? "");
        setEscalationLanguageInput(support.escalation_language ?? "");
        setFallbackEmail(support.fallback_email ?? null);
        const bot = bots.find((b) => b.is_active) ?? null;
        setDefaultBot(bot);
        if (bot) {
          const disclosure = await api.bots.getDisclosure(bot.id);
          setLevel(disclosure.level);
        }
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load settings");
      })
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
    setKeySaving(true);
    try {
      await api.clients.update({ openai_api_key: key });
      setHasOpenaiKey(true);
      setOpenaiKeyInput("");
      setKeySavedOk(true);
      setTimeout(() => setKeySavedOk(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setKeySaving(false);
    }
  }

  async function removeOpenaiKey() {
    setError("");
    setKeySaving(true);
    try {
      await api.clients.update({ openai_api_key: null });
      setHasOpenaiKey(false);
      setOpenaiKeyInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove");
    } finally {
      setKeySaving(false);
    }
  }

  async function saveSupportEmail() {
    setError("");
    setSupportSaving(true);
    setSupportSavedOk(false);
    try {
      const response = await api.support.update({
        l2_email: supportEmailInput.trim() || null,
        escalation_language: escalationLanguageInput.trim() || null,
      });
      setSupportEmailInput(response.l2_email ?? "");
      setEscalationLanguageInput(response.escalation_language ?? "");
      setFallbackEmail(response.fallback_email ?? null);
      setSupportSavedOk(true);
      setTimeout(() => setSupportSavedOk(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSupportSaving(false);
    }
  }

  async function clearSupportEmail() {
    setError("");
    setSupportSaving(true);
    setSupportSavedOk(false);
    try {
      const response = await api.support.update({
        l2_email: null,
        escalation_language: null,
      });
      setSupportEmailInput(response.l2_email ?? "");
      setEscalationLanguageInput(response.escalation_language ?? "");
      setFallbackEmail(response.fallback_email ?? null);
      setSupportSavedOk(true);
      setTimeout(() => setSupportSavedOk(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear");
    } finally {
      setSupportSaving(false);
    }
  }

  async function saveDisclosure() {
    if (!defaultBot) return;
    setError("");
    setDisclosureSaving(true);
    setDisclosureSavedOk(false);
    try {
      await api.bots.updateDisclosure(defaultBot.id, { level });
      setDisclosureSavedOk(true);
      setTimeout(() => setDisclosureSavedOk(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setDisclosureSaving(false);
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
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Settings</h1>
        <p className="text-sm text-slate-500 mt-1">
          Tenant-wide bot configuration for support routing, response behavior, and AI providers.
        </p>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 text-red-600 text-sm px-3 py-2 border border-red-100">
          {error}
        </div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Support inbox</h2>
          <p className="text-sm text-slate-500 mt-1">
            New escalation tickets are emailed here. If empty, we fall back to your owner email.
          </p>
        </div>

        {supportSavedOk && (
          <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            Support inbox saved.
          </div>
        )}

        <div className="space-y-2">
          <input
            type="email"
            placeholder="support@company.com"
            value={supportEmailInput}
            onChange={(e) => setSupportEmailInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveSupportEmail()}
            className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 outline-none focus:border-slate-400 placeholder:text-slate-400"
          />
          <p className="text-xs text-slate-500">
            Fallback owner email: <span className="font-medium text-slate-700">{fallbackEmail ?? "Not configured"}</span>
          </p>
          <input
            type="text"
            placeholder="Escalation language (e.g. en, ru, fr, pt-BR)"
            value={escalationLanguageInput}
            onChange={(e) => setEscalationLanguageInput(e.target.value)}
            className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 outline-none focus:border-slate-400 placeholder:text-slate-400"
          />
          <p className="text-xs text-slate-500">
            Used for escalation-only chat copy. Leave empty to fall back to English.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={saveSupportEmail}
              disabled={supportSaving}
              className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
            >
              {supportSaving ? "Saving…" : "Save inbox"}
            </button>
            <button
              type="button"
              onClick={clearSupportEmail}
              disabled={supportSaving || !supportEmailInput.trim()}
              className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
            >
              Clear
            </button>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Response controls</h2>
          <p className="text-sm text-slate-500 mt-1">
            One setting for your whole bot: every chat uses this response style.
          </p>
        </div>

        {disclosureSavedOk && (
          <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            Response controls saved.
          </div>
        )}

        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold text-slate-800 mb-1">Response detail level</legend>
          {DISCLOSURE_OPTIONS.map((opt) => (
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
          onClick={saveDisclosure}
          disabled={disclosureSaving}
          className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50 hover:bg-violet-700"
        >
          {disclosureSaving ? "Saving…" : "Save response controls"}
        </button>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">AI / Providers</h2>
          <p className="text-sm text-slate-500 mt-1">
            Configure provider credentials used for embeddings and chat completions.{" "}
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

        {keySavedOk && (
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
              disabled={keySaving || !openaiKeyInput.trim()}
              className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
            >
              {keySaving ? "Saving…" : hasOpenaiKey ? "Update key" : "Save key"}
            </button>
            {hasOpenaiKey && (
              <button
                type="button"
                onClick={removeOpenaiKey}
                disabled={keySaving}
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
