"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { useSupportSettings } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import { saveErrorText } from "./errors";
import { SaveRow, SectionFallback, SettingsSection, useSavedMessage } from "./SettingsSection";

export function EscalationSection() {
  const { data: support, error, mutate } = useSupportSettings();
  const [email, setEmail] = useState("");
  const [language, setLanguage] = useState("");
  const [seeded, setSeeded] = useState(false);
  const [emailError, setEmailError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useSavedMessage();
  const emailField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (seeded || !support) return;
    setEmail(support.l2_email ?? "");
    setLanguage(support.escalation_language ?? "");
    setSeeded(true);
  }, [seeded, support]);

  if (!seeded || !support) {
    return (
      <SettingsSection title="Escalation">
        <SectionFallback failed={Boolean(error)} what="the escalation settings" fields={2} onRetry={() => mutate()} />
      </SettingsSection>
    );
  }

  const dirty =
    email.trim() !== (support.l2_email ?? "") || language.trim() !== (support.escalation_language ?? "");

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!dirty) return;
    if (emailField.current?.validity.typeMismatch) {
      setEmailError("Enter a valid email address.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    setSaved(null);
    try {
      const response = await api.support.update({
        l2_email: email.trim() || null,
        escalation_language: language.trim() || null,
      });
      await mutate(response, false);
      setEmail(response.l2_email ?? "");
      setLanguage(response.escalation_language ?? "");
      setSaved("Escalation settings saved.");
    } catch (err) {
      setSaveError(saveErrorText(err, "Couldn't save the escalation settings. Try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsSection title="Escalation">
      <form onSubmit={(event) => void save(event)} noValidate className="space-y-4">
        <Input
          ref={emailField}
          type="email"
          label="Support inbox email"
          helper={`Escalation tickets are emailed here. Empty: the owner's email (${support.fallback_email ?? "none"}).`}
          error={emailError}
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
            setEmailError(undefined);
          }}
        />
        <Input
          label="Escalation language"
          helper="Language code, e.g. en, pt-BR. Empty: English."
          value={language}
          onChange={(event) => setLanguage(event.target.value)}
        />
        {saveError && <InlineNotice tone="danger">{saveError}</InlineNotice>}
        <SaveRow saved={saved}>
          <Button type="submit" variant="primary" busy={saving} disabled={!dirty}>
            Save escalation settings
          </Button>
        </SaveRow>
      </form>
    </SettingsSection>
  );
}
