"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useClientMe } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CopyButton } from "@/components/ui/copy-button";
import { InlineNotice } from "@/components/ui/inline-notice";
import {
  linkClasses,
  SaveRow,
  SectionFallback,
  SettingsSection,
  StatusLine,
  useSavedMessage,
} from "../../settings/_components/SettingsSection";

const HTTP_CONFLICT = 409;
const HTTP_TOO_MANY_REQUESTS = 429;
const ROTATION_LIMIT_TEXT = "Too many rotations. Try again in an hour.";

export function IdentityKeySection() {
  const { data: client, error, mutate } = useClientMe();
  const [secret, setSecret] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);
  const [status, setStatus] = useSavedMessage();

  async function reveal() {
    if (secret) {
      setRevealed(true);
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      setSecret((await api.widgetIdentity.get()).secret);
      setRevealed(true);
    } catch (err) {
      const unreadable = err instanceof ApiError && err.status === HTTP_CONFLICT;
      setFailure(unreadable ? "This key can't be read. Rotate it to issue a new one." : "Couldn't load the key. Try again.");
    } finally {
      setBusy(false);
    }
  }

  function hide() {
    setRevealed(false);
    setSecret(null);
  }

  async function issueKey() {
    const data = await api.widgetIdentity.rotate();
    setSecret(data.secret);
    setRevealed(true);
    setStatus("New key generated.");
    await mutate();
  }

  async function generate() {
    setBusy(true);
    setFailure(null);
    try {
      await issueKey();
    } catch (err) {
      setFailure(
        err instanceof ApiError && err.status === HTTP_TOO_MANY_REQUESTS
          ? ROTATION_LIMIT_TEXT
          : "Couldn't generate the key. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function rotate() {
    try {
      await issueKey();
    } catch (err) {
      if (!(err instanceof ApiError && err.status === HTTP_TOO_MANY_REQUESTS)) throw err;
      setFailure(ROTATION_LIMIT_TEXT);
    }
  }

  return (
    <SettingsSection id="widget-identity-key" title="Identity verification">
      {client ? (
        <>
          <p className="text-caption text-fg-muted">
            Optional. Your server signs each visitor&apos;s user_id with this key. Only signed IDs restore
            conversations across devices and group visitors in the Inbox.{" "}
            <a
              href="/docs/embedding-the-widget#identity-verification"
              target="_blank"
              rel="noopener noreferrer"
              className={linkClasses}
            >
              How to sign user_id
            </a>
          </p>
          {client.has_widget_identity_secret ? (
            <StatusLine>Key set.</StatusLine>
          ) : (
            <InlineNotice tone="warning">
              No key: user IDs from your page are trusted as-is, so anyone who knows a visitor&apos;s ID can open their
              conversation.
            </InlineNotice>
          )}
          {revealed && secret && (
            <div className="flex flex-wrap items-center gap-3">
              <code className="flex min-h-11 min-w-0 flex-1 items-center break-all rounded-control border border-line bg-surface-alt px-3 font-mono text-caption text-fg">
                {secret}
              </code>
              <CopyButton text={secret} label="Copy" variant="secondary" />
              <Button variant="secondary" onClick={hide}>
                Hide
              </Button>
            </div>
          )}
          {failure && <InlineNotice tone="danger">{failure}</InlineNotice>}
          <SaveRow saved={status}>
            {client.has_widget_identity_secret ? (
              <>
                {!revealed && (
                  <Button variant="secondary" busy={busy} onClick={() => void reveal()}>
                    Reveal key
                  </Button>
                )}
                <Button variant="secondary" disabled={busy} onClick={() => setRotating(true)}>
                  Rotate key
                </Button>
              </>
            ) : (
              <Button variant="secondary" busy={busy} onClick={() => void generate()}>
                Generate key
              </Button>
            )}
          </SaveRow>
        </>
      ) : (
        <SectionFallback failed={Boolean(error)} what="the identity key" fields={1} onRetry={() => mutate()} />
      )}
      {rotating && (
        <ConfirmDialog
          title="Rotate the identity key?"
          body="The old key stops working immediately. Visitors signed with it are treated as unverified, and unverified IDs don't restore conversations across devices, until your server signs with the new key."
          confirmLabel="Rotate key"
          tone="destructive"
          failureMessage="Couldn't rotate the key. Try again."
          onConfirm={rotate}
          onClose={() => setRotating(false)}
        />
      )}
    </SettingsSection>
  );
}
