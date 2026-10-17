"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, type DisclosureLevel } from "@/lib/api";
import { useActiveBot, useBotDisclosure } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { RadioGroup, type RadioOption } from "@/components/ui/choice";
import { InlineNotice } from "@/components/ui/inline-notice";
import { SaveRow, SectionFallback, SettingsSection, useSavedMessage } from "./SettingsSection";

const DISCLOSURE_OPTIONS: readonly RadioOption<DisclosureLevel>[] = [
  {
    value: "detailed",
    label: "Detailed",
    description: "Full technical detail from your docs: paths, diagnostics, vendor and tool names.",
  },
  {
    value: "standard",
    label: "Standard",
    description: "Plain language. No internal paths, stack traces, vendor names, affected-user counts or team names.",
  },
  {
    value: "corporate",
    label: "Corporate",
    description:
      "Polished, non-technical. No ETAs or status-page detail; points to support when issues are ongoing.",
  },
];

export function ResponseDetailSection() {
  const { bots, activeBot, error: botsError, mutate: mutateBots } = useActiveBot();
  const { data: disclosure, error: disclosureError, mutate: mutateDisclosure } = useBotDisclosure(activeBot?.id);
  const [level, setLevel] = useState<DisclosureLevel>("standard");
  const [seededBotId, setSeededBotId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [saved, setSaved] = useSavedMessage();

  useEffect(() => {
    if (!activeBot || !disclosure || seededBotId === activeBot.id) return;
    setLevel(disclosure.level);
    setSeededBotId(activeBot.id);
  }, [seededBotId, activeBot, disclosure]);

  if (!activeBot || !disclosure || seededBotId !== activeBot.id) {
    return (
      <SettingsSection title="Response detail">
        <SectionFallback
          failed={(!bots && Boolean(botsError)) || Boolean(disclosureError)}
          what="the response detail"
          fields={1}
          onRetry={() => Promise.all([mutateBots(), mutateDisclosure()])}
        />
      </SettingsSection>
    );
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!activeBot) return;
    setSaving(true);
    setSaveFailed(false);
    setSaved(null);
    try {
      const updated = await api.bots.updateDisclosure(activeBot.id, { level });
      await mutateDisclosure(updated, false);
      setSaved("Response detail saved.");
    } catch {
      setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsSection title="Response detail">
      <form onSubmit={(event) => void save(event)} className="space-y-4">
        <p className="text-caption text-fg-muted">Applies to every chat.</p>
        <RadioGroup
          label="Response detail"
          name="disclosure-level"
          options={DISCLOSURE_OPTIONS}
          value={level}
          onChange={setLevel}
        />
        {saveFailed && <InlineNotice tone="danger">Couldn&apos;t save the response detail. Try again.</InlineNotice>}
        <SaveRow saved={saved}>
          <Button type="submit" variant="primary" busy={saving} disabled={level === disclosure.level}>
            Save response detail
          </Button>
        </SaveRow>
      </form>
    </SettingsSection>
  );
}
