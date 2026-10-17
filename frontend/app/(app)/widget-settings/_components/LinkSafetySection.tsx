"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { useActiveBot } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/choice";
import { Textarea } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import {
  SaveRow,
  SectionFallback,
  SettingsSection,
  serverRejection,
  useSavedMessage,
} from "../../settings/_components/SettingsSection";

function parseDomains(input: string): string[] {
  return input
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function LinkSafetySection() {
  const { bots, activeBot, isValidating, error, mutate } = useActiveBot();
  const [enabled, setEnabled] = useState(false);
  const [domains, setDomains] = useState("");
  const [seededBotId, setSeededBotId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [domainsError, setDomainsError] = useState<string>();
  const [saveFailed, setSaveFailed] = useState(false);
  const [saved, setSaved] = useSavedMessage();

  useEffect(() => {
    if (!activeBot || isValidating || seededBotId === activeBot.id) return;
    setSeededBotId(activeBot.id);
    setEnabled(activeBot.link_safety_enabled ?? false);
    setDomains((activeBot.allowed_domains ?? []).join("\n"));
  }, [activeBot, isValidating, seededBotId]);

  if (!activeBot || seededBotId !== activeBot.id) {
    return (
      <SettingsSection title="Link safety">
        <SectionFallback failed={!bots && Boolean(error)} what="link safety" fields={2} onRetry={() => mutate()} />
      </SettingsSection>
    );
  }

  const dirty =
    enabled !== (activeBot.link_safety_enabled ?? false) ||
    parseDomains(domains).join("\n") !== (activeBot.allowed_domains ?? []).join("\n");

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!activeBot || !dirty) return;
    setSaving(true);
    setSaveFailed(false);
    setDomainsError(undefined);
    setSaved(null);
    try {
      const updated = await api.bots.update(activeBot.id, {
        link_safety_enabled: enabled,
        allowed_domains: parseDomains(domains),
      });
      await mutate((current) => current?.map((bot) => (bot.id === updated.id ? updated : bot)), { revalidate: false });
      setDomains((updated.allowed_domains ?? []).join("\n"));
      setSaved("Link safety saved.");
    } catch (err) {
      const rejection = serverRejection(err);
      if (rejection) setDomainsError(rejection);
      else setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsSection title="Link safety">
      <form onSubmit={(event) => void save(event)} className="space-y-4">
        <Checkbox
          label="Ask visitors to confirm before opening links outside the allowed domains"
          helper="Applies to links in answers and sources."
          checked={enabled}
          onChange={(event) => setEnabled(event.target.checked)}
        />
        <Textarea
          label="Allowed domains"
          rows={4}
          placeholder={"example.com\nhelp.example.com"}
          helper="One per line or comma-separated. Subdomains are included."
          error={domainsError}
          value={domains}
          onChange={(event) => {
            setDomains(event.target.value);
            setDomainsError(undefined);
          }}
        />
        {saveFailed && <InlineNotice tone="danger">Couldn&apos;t save link safety. Try again.</InlineNotice>}
        <SaveRow saved={saved}>
          <Button type="submit" variant="primary" busy={saving} disabled={!dirty}>
            Save link safety
          </Button>
        </SaveRow>
      </form>
    </SettingsSection>
  );
}
