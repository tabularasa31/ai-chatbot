"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type BotResponse } from "@/lib/api";

export default function WidgetSettingsPage() {
  const [defaultBot, setDefaultBot] = useState<BotResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [copiedBotId, setCopiedBotId] = useState(false);
  const [linkSafetyEnabled, setLinkSafetyEnabled] = useState(false);
  const [allowedDomainsInput, setAllowedDomainsInput] = useState("");
  const [settingsSavedOk, setSettingsSavedOk] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const bots = await api.bots.list();
      const activeBot = bots.find((b) => b.is_active) ?? null;
      setDefaultBot(activeBot);
      setLinkSafetyEnabled(activeBot?.link_safety_enabled ?? false);
      setAllowedDomainsInput((activeBot?.allowed_domains ?? []).join("\n"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function copyBotId() {
    if (!defaultBot?.public_id) return;
    await navigator.clipboard.writeText(defaultBot.public_id);
    setCopiedBotId(true);
    setTimeout(() => setCopiedBotId(false), 2000);
  }

  function parseAllowedDomains(): string[] {
    return allowedDomainsInput
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  async function saveLinkSafetySettings() {
    if (!defaultBot) return;
    setSettingsBusy(true);
    setSettingsSavedOk(false);
    setError("");
    try {
      const updated = await api.bots.update(defaultBot.id, {
        link_safety_enabled: linkSafetyEnabled,
        allowed_domains: parseAllowedDomains(),
      });
      setDefaultBot(updated);
      setAllowedDomainsInput((updated.allowed_domains ?? []).join("\n"));
      setSettingsSavedOk(true);
      setTimeout(() => setSettingsSavedOk(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save widget settings");
    } finally {
      setSettingsBusy(false);
    }
  }

  if (loading) {
    return <p className="text-slate-600">Loading…</p>;
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Widget</h1>
        <p className="mt-1 text-slate-500 text-sm">
          Bot ID and link-safety configuration for the embedded widget.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 text-red-800 px-4 py-3 text-sm">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Bot ID</h2>
          <p className="text-slate-500 text-sm mt-1">
            Use this public bot ID in the widget snippet&apos;s{" "}
            <code className="text-slate-800">data-bot-id</code> attribute.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <code className="text-xs break-all bg-slate-50 border border-slate-200 text-slate-900 font-mono rounded px-3 py-2 flex-1 min-w-0">
            {defaultBot?.public_id ?? "—"}
          </code>
          <button
            type="button"
            onClick={copyBotId}
            className="rounded-lg bg-slate-900 text-white px-3 py-2 text-sm shrink-0"
          >
            {copiedBotId ? "Copied" : "Copy"}
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Content</p>
          <h2 className="mt-1 text-base font-semibold text-slate-800">Link Safety</h2>
          <p className="mt-1 text-sm text-slate-500">
            Ask visitors to confirm before opening links outside your allowed domains.
          </p>
        </div>

        <div className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
          <span>
            <span id="link-safety-enabled-label" className="block text-sm font-medium text-slate-800">
              Enable Link Safety modal
            </span>
            <span className="block text-xs text-slate-500">
              Markdown links and source links are checked before opening.
            </span>
          </span>
          <input
            id="link-safety-enabled"
            type="checkbox"
            aria-labelledby="link-safety-enabled-label"
            checked={linkSafetyEnabled}
            onChange={(e) => setLinkSafetyEnabled(e.target.checked)}
            className="h-5 w-5 rounded border-slate-300 text-violet-600 focus:ring-violet-500"
          />
        </div>

        <div className="space-y-2">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Deploy</p>
            <label htmlFor="allowed-domains" className="mt-1 block text-sm font-medium text-slate-800">
              Allowed domains
            </label>
          </div>
          <textarea
            id="allowed-domains"
            value={allowedDomainsInput}
            onChange={(e) => setAllowedDomainsInput(e.target.value)}
            rows={4}
            placeholder={"example.com\nhelp.example.com"}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-100"
          />
          <p className="text-xs text-slate-500">
            One domain per line or comma separated. Subdomains are allowed automatically.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={!defaultBot || settingsBusy}
            onClick={saveLinkSafetySettings}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {settingsBusy ? "Saving…" : "Save settings"}
          </button>
          {settingsSavedOk && <span className="text-sm text-emerald-600">Saved</span>}
        </div>
      </section>
    </div>
  );
}
