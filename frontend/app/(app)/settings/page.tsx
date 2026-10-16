"use client";

import { useEffect, useState } from "react";
import { api, type DisclosureLevel } from "@/lib/api";
import { useClientMe, useActiveBot, useSupportSettings, useBotDisclosure } from "@/hooks/useApi";
import DeleteWorkspaceCard from "./DeleteWorkspaceCard";
import { LoadError } from "@/components/ui/load-error";

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

const MAX_INSTRUCTIONS_LENGTH = 3000;

function CardFallback({
  failed,
  what,
  onRetry,
}: {
  failed: boolean;
  what: string;
  onRetry: () => Promise<unknown>;
}) {
  if (failed) return <LoadError message={`Couldn't load ${what}. Try again.`} onRetry={onRetry} />;
  return <p className="text-sm text-slate-500 animate-pulse">Loading…</p>;
}

export default function SettingsPage() {
  const [openaiKeyInput, setOpenaiKeyInput] = useState("");
  const [supportEmailInput, setSupportEmailInput] = useState("");
  const [escalationLanguageInput, setEscalationLanguageInput] = useState("");
  const [level, setLevel] = useState<DisclosureLevel>("standard");
  const [customInstructionsInput, setCustomInstructionsInput] = useState("");
  const [ownPromptOnly, setOwnPromptOnly] = useState(false);
  const [presetText, setPresetText] = useState<string | null>(null);
  const [keySaving, setKeySaving] = useState(false);
  const [supportSaving, setSupportSaving] = useState(false);
  const [disclosureSaving, setDisclosureSaving] = useState(false);
  const [instructionsSaving, setInstructionsSaving] = useState(false);
  const [instructionsSavedOk, setInstructionsSavedOk] = useState(false);
  const [supportError, setSupportError] = useState("");
  const [instructionsError, setInstructionsError] = useState("");
  const [disclosureError, setDisclosureError] = useState("");
  const [keyError, setKeyError] = useState("");
  const [supportReady, setSupportReady] = useState(false);
  const [instructionsReady, setInstructionsReady] = useState(false);
  const [disclosureReady, setDisclosureReady] = useState(false);
  const [keySavedOk, setKeySavedOk] = useState(false);
  const [supportSavedOk, setSupportSavedOk] = useState(false);
  const [disclosureSavedOk, setDisclosureSavedOk] = useState(false);

  const { data: client, error: clientError, mutate: mutateClient } = useClientMe();
  const { bots, activeBot: defaultBot, error: botsError, mutate: mutateBots } = useActiveBot();
  const { data: support, error: supportLoadError, mutate: mutateSupport } = useSupportSettings();
  const { data: disclosure, error: disclosureLoadError, mutate: mutateDisclosure } = useBotDisclosure(defaultBot?.id);

  useEffect(() => {
    if (supportReady || !support) return;
    setSupportEmailInput(support.l2_email ?? "");
    setEscalationLanguageInput(support.escalation_language ?? "");
    setSupportReady(true);
  }, [supportReady, support]);

  useEffect(() => {
    if (instructionsReady || !defaultBot) return;
    setCustomInstructionsInput(defaultBot.custom_instructions ?? "");
    setOwnPromptOnly(defaultBot.preset === null);
    setPresetText(defaultBot.preset_text);
    setInstructionsReady(true);
  }, [instructionsReady, defaultBot]);

  useEffect(() => {
    if (disclosureReady || !disclosure) return;
    setLevel(disclosure.level);
    setDisclosureReady(true);
  }, [disclosureReady, disclosure]);

  const botLoadFailed = !defaultBot && (Boolean(botsError) || bots !== undefined);
  const retryBots = () => mutateBots();

  async function saveOwnInstructions() {
    if (!defaultBot) return;
    setInstructionsError("");
    setInstructionsSaving(true);
    setInstructionsSavedOk(false);
    try {
      const updated = await api.bots.update(defaultBot.id, {
        custom_instructions: customInstructionsInput.trim() || null,
        preset: ownPromptOnly ? null : defaultBot.preset ?? "support_agent",
      });
      setCustomInstructionsInput(updated.custom_instructions ?? "");
      setOwnPromptOnly(updated.preset === null);
      setPresetText(updated.preset_text);
      await mutateBots(bots?.map((b) => (b.id === updated.id ? updated : b)), false);
      setInstructionsSavedOk(true);
      setTimeout(() => setInstructionsSavedOk(false), 2500);
    } catch (err) {
      setInstructionsError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setInstructionsSaving(false);
    }
  }

  async function saveOpenaiKey() {
    setKeyError("");
    const key = openaiKeyInput.trim();
    if (!key) return;
    if (!key.startsWith("sk-")) {
      setKeyError("OpenAI API key must start with 'sk-'");
      return;
    }
    setKeySaving(true);
    try {
      await api.clients.update({ openai_api_key: key });
      await mutateClient();
      setOpenaiKeyInput("");
      setKeySavedOk(true);
      setTimeout(() => setKeySavedOk(false), 2500);
    } catch (err) {
      setKeyError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setKeySaving(false);
    }
  }

  async function removeOpenaiKey() {
    setKeyError("");
    setKeySaving(true);
    try {
      await api.clients.update({ openai_api_key: null });
      await mutateClient();
      setOpenaiKeyInput("");
    } catch (err) {
      setKeyError(err instanceof Error ? err.message : "Failed to remove");
    } finally {
      setKeySaving(false);
    }
  }

  async function saveSupportEmail() {
    setSupportError("");
    setSupportSaving(true);
    setSupportSavedOk(false);
    try {
      const response = await api.support.update({
        l2_email: supportEmailInput.trim() || null,
        escalation_language: escalationLanguageInput.trim() || null,
      });
      await mutateSupport(response, false);
      setSupportEmailInput(response.l2_email ?? "");
      setEscalationLanguageInput(response.escalation_language ?? "");
      setSupportSavedOk(true);
      setTimeout(() => setSupportSavedOk(false), 2500);
    } catch (err) {
      setSupportError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSupportSaving(false);
    }
  }

  async function clearSupportEmail() {
    setSupportError("");
    setSupportSaving(true);
    setSupportSavedOk(false);
    try {
      const response = await api.support.update({
        l2_email: null,
        escalation_language: null,
      });
      await mutateSupport(response, false);
      setSupportEmailInput(response.l2_email ?? "");
      setEscalationLanguageInput(response.escalation_language ?? "");
      setSupportSavedOk(true);
      setTimeout(() => setSupportSavedOk(false), 2500);
    } catch (err) {
      setSupportError(err instanceof Error ? err.message : "Failed to clear");
    } finally {
      setSupportSaving(false);
    }
  }

  async function saveDisclosure() {
    if (!defaultBot) return;
    setDisclosureError("");
    setDisclosureSaving(true);
    setDisclosureSavedOk(false);
    try {
      const updated = await api.bots.updateDisclosure(defaultBot.id, { level });
      await mutateDisclosure(updated, false);
      setDisclosureSavedOk(true);
      setTimeout(() => setDisclosureSavedOk(false), 2500);
    } catch (err) {
      setDisclosureError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setDisclosureSaving(false);
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Settings</h1>
        <p className="text-sm text-slate-500 mt-1">
          Tenant-wide bot configuration for support routing, response behavior, and AI providers.
        </p>
      </div>

      {/* Support inbox */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Support inbox</h2>
          <p className="text-sm text-slate-500 mt-1">
            New escalation tickets are emailed here. If empty, we fall back to your owner email.
          </p>
        </div>

        {supportReady ? (
        <>
        {supportSavedOk && (
          <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            Support inbox saved.
          </div>
        )}

        <div className="space-y-2">
          <input
            type="email"
            placeholder="support@company.com"
            aria-label="Support inbox email"
            value={supportEmailInput}
            onChange={(e) => setSupportEmailInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveSupportEmail()}
            className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 outline-none focus:border-slate-400 placeholder:text-slate-400"
          />
          <p className="text-xs text-slate-500">
            Fallback owner email:{" "}
            <span className="font-medium text-slate-700">{support?.fallback_email ?? "Not configured"}</span>
          </p>
          <input
            type="text"
            placeholder="Escalation language (e.g. en, ru, fr, pt-BR)"
            aria-label="Escalation language"
            value={escalationLanguageInput}
            onChange={(e) => setEscalationLanguageInput(e.target.value)}
            className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 outline-none focus:border-slate-400 placeholder:text-slate-400"
          />
          <p className="text-xs text-slate-500">
            Used for escalation-only chat copy. Leave empty to fall back to English.
          </p>
          <p role="alert" className="text-red-600 text-sm">{supportError}</p>
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
        </>
        ) : (
          <CardFallback failed={Boolean(supportLoadError)} what="support inbox settings" onRetry={() => mutateSupport()} />
        )}
      </div>

      {/* Agent instructions */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Agent instructions</h2>
          <p className="text-sm text-slate-500 mt-1">
            The system prompt your bot follows on every turn.
          </p>
        </div>

        {instructionsReady ? (
        <>
        {instructionsSavedOk && (
          <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            Saved.
          </div>
        )}

        <>
            {!ownPromptOnly && presetText && (
              <details className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium text-slate-700">
                  Standard instructions
                </summary>
                <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-white p-3 font-mono text-xs text-slate-600">
                  {presetText}
                </pre>
                <p className="mt-2 text-xs text-slate-500">
                  Updated centrally by Chat9. Product rules always apply on top of these instructions.
                </p>
              </details>
            )}

            {!ownPromptOnly && !presetText && (
              <p className="text-sm text-slate-500">
                <span className="font-medium text-slate-700">Standard instructions.</span>{" "}
                The standard instructions will appear here after you save.
              </p>
            )}

            <div>
              <label htmlFor="custom-instructions" className="block text-sm font-semibold text-slate-800 mb-1">
                Your additions
              </label>
              <textarea
                id="custom-instructions"
                rows={10}
                placeholder="Appended after the standard instructions above."
                aria-label="Your additions"
                value={customInstructionsInput}
                onChange={(e) => setCustomInstructionsInput(e.target.value)}
                className={`w-full px-3 py-2.5 border rounded-lg text-sm text-slate-800 outline-none placeholder:text-slate-400 font-mono resize-y leading-relaxed ${
                  customInstructionsInput.trim().length > MAX_INSTRUCTIONS_LENGTH
                    ? "border-red-300 focus:border-red-400"
                    : "border-slate-200 focus:border-slate-400"
                }`}
              />
              <div className="flex items-start justify-between gap-4 mt-1">
                <p className="text-xs text-slate-500">
                  Use{" "}
                  <code className="font-mono bg-slate-100 px-1 py-0.5 rounded">{"{product_name}"}</code>{" "}
                  to insert your product name. These instructions are prepended to every chat turn.
                </p>
                <span className={`text-xs shrink-0 tabular-nums ${customInstructionsInput.trim().length > MAX_INSTRUCTIONS_LENGTH ? "text-red-500 font-medium" : "text-slate-400"}`}>
                  {customInstructionsInput.trim().length} / {MAX_INSTRUCTIONS_LENGTH}
                </span>
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={ownPromptOnly}
                onChange={(e) => setOwnPromptOnly(e.target.checked)}
                className="h-4 w-4"
              />
              Use only my own prompt
            </label>

            {ownPromptOnly && (
              <div className="text-sm text-amber-700 bg-amber-50 border border-amber-100 px-3 py-2 rounded-lg">
                The standard instructions are not applied to this bot. Product rules still apply. You are
                responsible for the full prompt.
                {customInstructionsInput.trim() === "" && " Enter your prompt before saving."}
              </div>
            )}

          <p role="alert" className="text-red-600 text-sm">{instructionsError}</p>
            <button
              type="button"
              onClick={saveOwnInstructions}
              disabled={
                instructionsSaving ||
                customInstructionsInput.trim().length > MAX_INSTRUCTIONS_LENGTH ||
                (ownPromptOnly && customInstructionsInput.trim() === "")
              }
              className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50 hover:bg-violet-700 transition-colors"
            >
              {instructionsSaving ? "Saving…" : "Save instructions"}
            </button>
          </>
        </>
        ) : (
          <CardFallback failed={botLoadFailed} what="agent instructions" onRetry={retryBots} />
        )}
      </div>

      {/* Response controls */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Response controls</h2>
          <p className="text-sm text-slate-500 mt-1">
            One setting for your whole bot: every chat uses this response style.
          </p>
        </div>

        {disclosureReady ? (
        <>
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
              aria-label={opt.label}
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

          <p role="alert" className="text-red-600 text-sm">{disclosureError}</p>
        <button
          type="button"
          onClick={saveDisclosure}
          disabled={disclosureSaving}
          className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50 hover:bg-violet-700"
        >
          {disclosureSaving ? "Saving…" : "Save response controls"}
        </button>
        </>
        ) : (
          <CardFallback failed={botLoadFailed || Boolean(disclosureLoadError)} what="response controls" onRetry={() => Promise.all([mutateBots(), mutateDisclosure()])} />
        )}
      </div>

      {/* AI / Providers */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">AI / Providers</h2>
          <p className="text-sm text-slate-500 mt-1">
            Configure provider credentials used for embeddings and chat completions.{" "}
            <a
              href="https://platform.openai.com/api-keys"
              target="_blank"
              rel="noopener noreferrer"
              className="text-violet-600 hover:underline"
            >
              Get yours at platform.openai.com
            </a>
          </p>
        </div>

        {client ? (
        <>
        {client?.has_openai_key && (
          <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
            API key configured
          </div>
        )}

        {!client?.has_openai_key && (
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
            aria-label="OpenAI API key"
            value={openaiKeyInput}
            onChange={(e) => setOpenaiKeyInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveOpenaiKey()}
            className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 outline-none focus:border-slate-400 placeholder:text-slate-400"
          />
          {keyError && <p role="alert" className="text-red-600 text-sm">{keyError}</p>}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={saveOpenaiKey}
              disabled={keySaving || !openaiKeyInput.trim()}
              className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
            >
              {keySaving ? "Saving…" : client?.has_openai_key ? "Update key" : "Save key"}
            </button>
            {client?.has_openai_key && (
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
        </>
        ) : (
          <CardFallback failed={Boolean(clientError)} what="provider settings" onRetry={() => mutateClient()} />
        )}
      </div>

      {/* Danger zone. Owner-gated here as well as in the sidebar, because a
          direct URL does not go through the sidebar. */}
      {client?.role === "owner" && client.name && (
        <DeleteWorkspaceCard workspaceName={client.name} workspaceId={client.id} />
      )}
    </div>
  );
}
