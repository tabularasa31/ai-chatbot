"use client";

import { useState, type FormEvent } from "react";
import { ExternalLink } from "lucide-react";
import { api } from "@/lib/api";
import { useClientMe } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import {
  linkClasses,
  SaveRow,
  SectionFallback,
  SettingsSection,
  StatusLine,
  useSavedMessage,
} from "./SettingsSection";

const KEY_PREFIX = "sk-";

export function OpenAiKeySection() {
  const { data: client, error, mutate } = useClientMe();
  const [key, setKey] = useState("");
  const [keyError, setKeyError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [saved, setSaved] = useSavedMessage();

  async function save(event: FormEvent) {
    event.preventDefault();
    const value = key.trim();
    if (!value) return;
    if (!value.startsWith(KEY_PREFIX)) {
      setKeyError("Keys start with sk-.");
      return;
    }
    setSaving(true);
    setSaveFailed(false);
    setSaved(null);
    try {
      await api.clients.update({ openai_api_key: value });
      await mutate();
      setKey("");
      setSaved("Key saved.");
    } catch {
      setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  }

  async function removeKey() {
    await api.clients.update({ openai_api_key: null });
    await mutate();
    setKey("");
    setSaved("Key removed.");
  }

  return (
    <SettingsSection id="openai-key" title="OpenAI API key">
      {client ? (
        <form onSubmit={(event) => void save(event)} className="space-y-4">
          {client.has_openai_key ? (
            <StatusLine>Key set.</StatusLine>
          ) : (
            <InlineNotice tone="warning">
              No key: the bot can&apos;t answer and documents can&apos;t be indexed.
            </InlineNotice>
          )}
          <div>
            <Input
              type="password"
              label={client.has_openai_key ? "New key" : "OpenAI API key"}
              autoComplete="off"
              placeholder="sk-…"
              value={key}
              error={keyError}
              onChange={(event) => {
                setKey(event.target.value);
                setKeyError(undefined);
              }}
            />
            <a
              href="https://platform.openai.com/api-keys"
              target="_blank"
              rel="noopener noreferrer"
              className={`${linkClasses} mt-1 inline-flex min-h-11 items-center gap-1 text-caption`}
            >
              Get a key at platform.openai.com
              <ExternalLink size={16} aria-hidden="true" />
            </a>
          </div>
          {saveFailed && <InlineNotice tone="danger">Couldn&apos;t save the key. Check it and try again.</InlineNotice>}
          <SaveRow saved={saved}>
            {client.has_openai_key && (
              <Button variant="secondary" onClick={() => setRemoving(true)}>
                Remove key
              </Button>
            )}
            <Button type="submit" variant="primary" busy={saving} disabled={!key.trim()}>
              {client.has_openai_key ? "Replace key" : "Save key"}
            </Button>
          </SaveRow>
        </form>
      ) : (
        <SectionFallback failed={Boolean(error)} what="the key status" fields={1} onRetry={() => mutate()} />
      )}
      {removing && (
        <ConfirmDialog
          title="Remove the OpenAI key?"
          body="The bot stops answering and new documents can't be indexed until you add a key."
          confirmLabel="Remove key"
          tone="destructive"
          failureMessage="Couldn't remove the key. Try again."
          onConfirm={removeKey}
          onClose={() => setRemoving(false)}
        />
      )}
    </SettingsSection>
  );
}
