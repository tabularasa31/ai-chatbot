"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import { useActiveBot } from "@/hooks/useApi";
import { Button, FOCUS_RING } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/choice";
import { Textarea } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import { cn } from "@/components/ui/utils";
import { SaveRow, SectionFallback, SettingsSection, saveErrorText, useSavedMessage } from "./SettingsSection";

const MAX_INSTRUCTIONS_LENGTH = 3000;
const DEFAULT_PRESET = "support_agent";

export function InstructionsSection() {
  const { bots, activeBot, error, mutate } = useActiveBot();
  const [additions, setAdditions] = useState("");
  const [ownPromptOnly, setOwnPromptOnly] = useState(false);
  const [presetText, setPresetText] = useState<string | null>(null);
  const [seededBotId, setSeededBotId] = useState<string | null>(null);
  const [showPreset, setShowPreset] = useState(false);
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useSavedMessage();

  useEffect(() => {
    if (!activeBot || seededBotId === activeBot.id) return;
    setAdditions(activeBot.custom_instructions ?? "");
    setOwnPromptOnly(activeBot.preset === null);
    setPresetText(activeBot.preset_text);
    setSeededBotId(activeBot.id);
  }, [seededBotId, activeBot]);

  if (!activeBot || seededBotId !== activeBot.id) {
    return (
      <SettingsSection title="Instructions">
        <SectionFallback
          failed={!bots && Boolean(error)}
          what="the instructions"
          fields={1}
          onRetry={() => mutate()}
        />
      </SettingsSection>
    );
  }

  const length = additions.trim().length;
  const overBy = length - MAX_INSTRUCTIONS_LENGTH;
  const promptMissing = ownPromptOnly && length === 0;
  const dirty = additions.trim() !== (activeBot.custom_instructions ?? "") || ownPromptOnly !== (activeBot.preset === null);
  const canSave = dirty && overBy <= 0 && !promptMissing;
  const additionsError =
    overBy > 0 ? `Shorten by ${overBy} characters.` : promptMissing && touched ? "Enter your prompt." : undefined;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!activeBot || !canSave) return;
    setSaving(true);
    setSaveError(null);
    setSaved(null);
    try {
      const updated = await api.bots.update(activeBot.id, {
        custom_instructions: additions.trim() || null,
        preset: ownPromptOnly ? null : (activeBot.preset ?? DEFAULT_PRESET),
      });
      setAdditions(updated.custom_instructions ?? "");
      setOwnPromptOnly(updated.preset === null);
      setPresetText(updated.preset_text);
      await mutate(bots?.map((bot) => (bot.id === updated.id ? updated : bot)), false);
      setSaved("Instructions saved.");
    } catch (err) {
      setSaveError(saveErrorText(err, "Couldn't save the instructions. Try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsSection title="Instructions">
      <form onSubmit={(event) => void save(event)} className="space-y-4">
        {!ownPromptOnly &&
          (presetText ? (
            <div>
              <button
                type="button"
                aria-expanded={showPreset}
                onClick={() => setShowPreset((open) => !open)}
                className={cn(
                  "flex min-h-11 items-center gap-2 rounded-control text-ui font-medium text-fg",
                  FOCUS_RING.canvas,
                )}
              >
                Standard instructions
                <ChevronDown
                  size={16}
                  aria-hidden="true"
                  className={cn("text-fg-muted transition-transform", showPreset && "rotate-180")}
                />
              </button>
              {showPreset && (
                <>
                  <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-control border border-line bg-surface-alt p-3 font-mono text-caption text-fg">
                    {presetText}
                  </pre>
                  <p className="mt-1 text-caption text-fg-muted">Updated by Chat9. Product rules always apply.</p>
                </>
              )}
            </div>
          ) : (
            <p className="text-caption text-fg-muted">Standard instructions appear here after you save.</p>
          ))}

        <div>
          <Textarea
            mono
            rows={10}
            label="Your additions"
            helper="Added after the standard instructions. Use {product_name} for your product name."
            value={additions}
            error={additionsError}
            onChange={(event) => setAdditions(event.target.value)}
            onBlur={() => setTouched(true)}
          />
          <p className={cn("mt-1 text-right text-caption tabular-nums", overBy > 0 ? "text-danger" : "text-fg-muted")}>
            {length} / {MAX_INSTRUCTIONS_LENGTH}
          </p>
        </div>

        <Checkbox
          label="Use only my own prompt"
          checked={ownPromptOnly}
          onChange={(event) => {
            setOwnPromptOnly(event.target.checked);
            setTouched(true);
          }}
        />
        {ownPromptOnly && (
          <InlineNotice tone="warning">
            The standard instructions are not applied. Product rules still apply. You are responsible for the whole
            prompt.
          </InlineNotice>
        )}

        {saveError && <InlineNotice tone="danger">{saveError}</InlineNotice>}
        <SaveRow saved={saved}>
          <Button type="submit" variant="primary" busy={saving} disabled={!canSave}>
            Save instructions
          </Button>
        </SaveRow>
      </form>
    </SettingsSection>
  );
}
