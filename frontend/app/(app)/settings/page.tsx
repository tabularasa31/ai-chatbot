"use client";

import { useEffect, useRef, useState } from "react";
import { api, type DisclosureLevel } from "@/lib/api";
import { useClientMe, useBots, useSupportSettings, useBotDisclosure } from "@/hooks/useApi";

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

const PRESETS: { id: string; label: string; content: string }[] = [
  {
    id: "support_agent",
    label: "Support Agent",
    content: `You are a support assistant for {product_name}. Your job is to help users get answers from the provided documentation — clearly, honestly, and in the user's language.

Ground rules:
- Base every answer strictly on the retrieved context. If something isn't there, say so directly rather than guessing.
- When the context covers the question, be specific: name the exact setting, page, or section it describes.
- If a single missing detail would make your answer wrong or incomplete, ask one focused clarifying question instead of speculating.
- Stay on topic — politely decline anything unrelated to {product_name} and its docs.
- Match the user's language in every reply. Never switch languages mid-response.
- Keep it concise. Expand only when the user asks for more depth.

Formatting:
- Use Markdown when it adds clarity (lists, code blocks, headings).
- Only link to URLs that appear verbatim in the provided context.
- When you can't answer: "I don't have that information in the documentation. Feel free to reach out to the support team directly."`,
  },
];

export default function SettingsPage() {
  const [openaiKeyInput, setOpenaiKeyInput] = useState("");
  const [supportEmailInput, setSupportEmailInput] = useState("");
  const [escalationLanguageInput, setEscalationLanguageInput] = useState("");
  const [level, setLevel] = useState<DisclosureLevel>("standard");
  const [agentInstructions, setAgentInstructions] = useState("");
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);
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
  const { data: bots, isLoading: botsLoading } = useBots();
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
    const instructions = defaultBot.agent_instructions ?? "";
    setAgentInstructions(instructions);
    const matched = PRESETS.find((p) => instructions.trim() === p.content.trim());
    setSelectedPreset(matched?.id ?? null);
    setLevel(disclosure.level);
  }, [client, support, defaultBot, disclosure]);

  const loading = clientLoading || botsLoading || supportLoading || disclosureLoading;

  function applyPreset(presetId: string) {
    const preset = PRESETS.find((p) => p.id === presetId);
    if (!preset) return;
    setSelectedPreset(presetId);
    setAgentInstructions(preset.content);
  }

  async function saveAgentInstructions() {
    if (!defaultBot) return;
    setError("");
    setInstructionsSaving(true);
    setInstructionsSavedOk(false);
    try {
      const updated = await api.bots.update(defaultBot.id, {
        agent_instructions: agentInstructions.trim() || null,
      });
      const instructions = updated.agent_instructions ?? "";
      setAgentInstructions(instructions);
      const matched = PRESETS.find((p) => instructions.trim() === p.content.trim());
      setSelectedPreset(matched?.id ?? null);
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

  const activePreset = PRESETS.find((p) => p.id === selectedPreset) ?? null;
  const isCustom = agentInstructions.trim() !== "" && !activePreset;

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
            The system prompt your bot follows on every turn. Start from a template or write your own.
          </p>
        </div>

        {instructionsSavedOk && (
          <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            Saved.
          </div>
        )}

        {/* Template pills */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-500 mr-1">Templates:</span>
          {PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              onClick={() => applyPreset(preset.id)}
              className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                selectedPreset === preset.id
                  ? "bg-violet-600 border-violet-600 text-white"
                  : "bg-white border-slate-300 text-slate-600 hover:border-violet-400 hover:text-violet-700"
              }`}
            >
              {preset.label}
            </button>
          ))}
          {isCustom && (
            <span className="px-3 py-1 rounded-full text-xs font-medium bg-slate-100 border border-slate-200 text-slate-500">
              Custom
            </span>
          )}
        </div>

        {/* Textarea */}
        <div className="relative">
          <textarea
            rows={14}
            placeholder={"You are a support assistant for {product_name}.\n\nYour rules here…"}
            value={agentInstructions}
            onChange={(e) => {
              setAgentInstructions(e.target.value);
              const matched = PRESETS.find((p) => e.target.value.trim() === p.content.trim());
              setSelectedPreset(matched?.id ?? null);
            }}
            className={`w-full px-3 py-2.5 border rounded-lg text-sm text-slate-800 outline-none placeholder:text-slate-400 font-mono resize-y leading-relaxed ${
              agentInstructions.trim().length > MAX_INSTRUCTIONS_LENGTH
                ? "border-red-300 focus:border-red-400"
                : "border-slate-200 focus:border-slate-400"
            }`}
          />
          {selectedPreset && agentInstructions.trim() !== (PRESETS.find(p => p.id === selectedPreset)?.content ?? "").trim() && (
            <button
              type="button"
              onClick={() => applyPreset(selectedPreset)}
              className="absolute bottom-3 right-3 text-xs text-slate-400 hover:text-violet-600 transition-colors"
            >
              Reset to template
            </button>
          )}
        </div>

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
    </div>
  );
}
