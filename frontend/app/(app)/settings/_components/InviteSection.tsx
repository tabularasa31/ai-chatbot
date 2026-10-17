"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import { api, ApiError, HTTP_CONFLICT, HTTP_TOO_MANY_REQUESTS } from "@/lib/api";
import { useMembers } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import { linkClasses, SettingsSection } from "./SettingsSection";

function inviteErrorText(err: unknown): string {
  if (err instanceof ApiError && err.status === HTTP_CONFLICT) return "This address already has an account.";
  if (err instanceof ApiError && err.status === HTTP_TOO_MANY_REQUESTS) return "Too many invites. Try again later.";
  return "Couldn't send the invite. Try again.";
}

export function InviteSection() {
  const { mutate } = useMembers();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string>();
  const [sending, setSending] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const field = useRef<HTMLInputElement>(null);

  async function invite(event: FormEvent) {
    event.preventDefault();
    const address = email.trim();
    if (!address) return;
    if (field.current?.validity.typeMismatch) {
      setError("Enter a valid email address.");
      return;
    }
    setSending(true);
    setError(undefined);
    setSentTo(null);
    try {
      const result = await api.members.invite(address);
      setEmail("");
      setSentTo(result.member.email);
      await mutate();
    } catch (err) {
      setError(inviteErrorText(err));
    } finally {
      setSending(false);
    }
  }

  return (
    <SettingsSection title="Invite a colleague">
      <form onSubmit={(event) => void invite(event)} noValidate className="space-y-4">
        <div className="flex flex-wrap items-start gap-3">
          <Input
            ref={field}
            type="email"
            label="Email"
            autoComplete="off"
            className="min-w-56 flex-1"
            value={email}
            error={error}
            onChange={(event) => {
              setEmail(event.target.value);
              setError(undefined);
            }}
          />
          <Button type="submit" variant="primary" busy={sending} disabled={!email.trim()} className="sm:mt-6">
            Send invite
          </Button>
        </div>
        <p className="text-caption text-fg-muted">
          They join as an operator: they answer conversations in the Inbox and can read the knowledge base. Settings
          stay with you. Each accepted invite takes a seat (see{" "}
          <Link href="/settings/seats" className={linkClasses}>
            Seats
          </Link>
          ).
        </p>
        {sentTo && (
          <InlineNotice tone="info" onDismiss={() => setSentTo(null)}>
            Invite sent to {sentTo}. The link expires in 7 days.
          </InlineNotice>
        )}
      </form>
    </SettingsSection>
  );
}
