"use client";

import { useEffect, useRef, useState } from "react";
import { api, type DisclosureLevel } from "@/lib/api";
import { useClientMe, useBots, useSupportSettings, useBotDisclosure } from "@/hooks/useApi";
import DeleteWorkspaceCard from "./DeleteWorkspaceCard";

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

export default function SettingsPage() {
  const [openaiKeyInput, setOpenaiKeyInput] = useState("");
  const [supportEmailInput, setSupportEmailInput] = useState("");
  const [escalationLanguageInput, setEscalationLanguageInput] = useState("");
  const [level, setLevel] = useState<DisclosureLevel>("standard");
  const [agentInstructions, setAgentInstructions] = useState("");
  const [customInstructionsInput, setCustomInstructionsInput] = useState("");
  const [ownPromptOnly, setOwnPromptOnly] = useState(false);
  const [presetText, setPresetText] = useState<string | null>(null);
  const [keySaving, setKeySaving] = useState(false);
  const [supportSaving, setSupportSaving] = useState(false);
  const [disclosureSaving, setDisclosureSaving] = useState(false);
  const [instructionsSaving, setInstructionsSaving] = useState(false);
  const [instructionsSavedOk, setInstructionsSavedOk] = useState(false);
  const [error, setError] = useState("");
  const [keySavedOk, setKeySavedOk] = useState(false);
  const [supportSavedOk, setSupportSavedOk] = useState(false);
  const [disclosureSavedOk, setDisclosureSavedOk] = useState(false);

  const { data: client, error: clientError, isLoading: clientLoading, mutate: mutateClient } = useClientMe();
  const { data: bots, isLoading: botsLoading, mutate: mutateBots } = useBots();
  const { data: support, isLoading: supportLoading, mutate: mutateSupport } = useSupportSettings();

  const defaultBot = bots?.find((b) => b.is_active) ?? null;
  const { data: disclosure, isLoading: disclosureLoading, mutate: mutateDisclosure } = useBotDisclosure(defaultBot?.id);

  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    if (!client || !support || !defaultBot || !disclosure) return;
    initialized.current = true;
    setSupportEmailInput(support.l2_email ?? "");
    setEscalationLanguageInput(support.escalation_language ?? "");
    setAgentInstructions(defaultBot.agent_instructions ?? "");
    setCustomInstructionsInput(defaultBot.custom_instructions ?? "");
    setOwnPromptOnly(defaultBot.preset === null);
    setPresetText(defaultBot.preset_text);
    setLevel(disclosure.level);
  }, [client, support, defaultBot, disclosure]);

  const loading = clientLoading || botsLoading || supportLoading || disclosureLoading;
  const isLegacyInstructions = defaultBot?.instructions_source === "legacy";

  async function saveAgentInstructions() {
    if (!defaultBot) return;
    setError("");
    setInstructionsSaving(true);
    setInstructionsSavedOk(false);
    try {
      const updated = await api.bots.update(defaultBot.id, {
        agent_instructions: agentInstructions.trim() || null,
      });
      setAgentInstructions(updated.agent_instructions ?? "");
      await mutateBots(bots?.map((b) => (b.id === updated.id ? updated : b)), false);
      setInstructionsSavedOk(true);
      setTimeout(() => setInstructionsSavedOk(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setInstructionsSaving(false);
    }
  }

  async function saveOwnInstructions() {
    if (!defaultBot) return;
    setError("");
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
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setInstructionsSaving(false);
    }
  }

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
      await mutateClient();
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
      await mutateClient();
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
      await mutateSupport(response, false);
      setSupportEmailInput(response.l2_email ?? "");
      setEscalationLanguageInput(response.escalation_language ?? "");
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
      await mutateSupport(response, false);
      setSupportEmailInput(response.l2_email ?? "");
      setEscalationLanguageInput(response.escalation_language ?? "");
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
      const updated = await api.bots.updateDisclosure(defaultBot.id, { level });
      await mutateDisclosure(updated, false);
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

      {(error || clientError) && (
        <div className="rounded-lg bg-red-50 text-red-600 text-sm px-3 py-2 border border-red-100">
          {error || (clientError instanceof Error ? clientError.message : "Failed to load settings")}
        </div>
      )}

      {/* Support inbox */}
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

      {/* Agent instructions */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Agent instructions</h2>
          <p className="text-sm text-slate-500 mt-1">
            The system prompt your bot follows on every turn.
          </p>
        </div>

        {instructionsSavedOk && (
          <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            Saved.
          </div>
        )}

        {isLegacyInstructions ? (
          <>
            <p className="text-xs text-slate-500 italic">
              This bot uses the previous instructions format; it will be moved to the new one automatically.
            </p>

            <textarea
              rows={14}
              placeholder={"You are a support assistant for {product_name}.\n\nYour rules here…"}
              aria-label="Agent instructions"
              value={agentInstructions}
              onChange={(e) => setAgentInstructions(e.target.value)}
              className={`w-full px-3 py-2.5 border rounded-lg text-sm text-slate-800 outline-none placeholder:text-slate-400 font-mono resize-y leading-relaxed ${
                agentInstructions.trim().length > MAX_INSTRUCTIONS_LENGTH
                  ? "border-red-300 focus:border-red-400"
                  : "border-slate-200 focus:border-slate-400"
              }`}
            />

            <div className="flex items-start justify-between gap-4">
              <p className="text-xs text-slate-500">
                Use{" "}
                <code className="font-mono bg-slate-100 px-1 py-0.5 rounded">{"{product_name}"}</code>{" "}
                to insert your product name. These instructions are prepended to every chat turn.
              </p>
              <span className={`text-xs shrink-0 tabular-nums ${agentInstructions.trim().length > MAX_INSTRUCTIONS_LENGTH ? "text-red-500 font-medium" : "text-slate-400"}`}>
                {agentInstructions.trim().length} / {MAX_INSTRUCTIONS_LENGTH}
              </span>
            </div>

            <button
              type="button"
              onClick={saveAgentInstructions}
              disabled={instructionsSaving || agentInstructions.trim().length > MAX_INSTRUCTIONS_LENGTH}
              className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50 hover:bg-violet-700 transition-colors"
            >
              {instructionsSaving ? "Saving…" : "Save instructions"}
            </button>
          </>
        ) : (
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

        <button
          type="button"
          onClick={saveDisclosure}
          disabled={disclosureSaving}
          className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50 hover:bg-violet-700"
        >
          {disclosureSaving ? "Saving…" : "Save response controls"}
        </button>
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
          {error && <p className="text-red-600 text-sm">{error}</p>}
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
      </div>

      {/* Danger zone. Owner-gated here as well as in the sidebar, because a
          direct URL does not go through the sidebar. */}
      {client?.role === "owner" && client.name && (
        <DeleteWorkspaceCard workspaceName={client.name} workspaceId={client.id} />
      )}
    </div>
  );
}
