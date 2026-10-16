"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import { SCHEDULE_OPTIONS, errorMessage, type SourceFormValues } from "./shared";

type SourceFormProps = {
  initial: SourceFormValues;
  withUrl: boolean;
  submitLabel: string;
  failureFallback: string;
  onSubmit: (values: SourceFormValues & { url: string }) => Promise<void>;
  onCancel: () => void;
};

export function SourceForm({
  initial,
  withUrl,
  submitLabel,
  failureFallback,
  onSubmit,
  onCancel,
}: SourceFormProps) {
  const [url, setUrl] = useState("");
  const [values, setValues] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSubmit({ ...values, url });
    } catch (err) {
      setError(errorMessage(err, failureFallback));
      setBusy(false);
    }
  }

  function set(patch: Partial<SourceFormValues>) {
    setValues((prev) => ({ ...prev, ...patch }));
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="grid max-w-3xl gap-4 sm:grid-cols-2">
      {withUrl && (
        <Input
          label="URL"
          type="url"
          required
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://docs.yourproduct.com"
        />
      )}
      <Input
        label="Name"
        value={values.name}
        onChange={(e) => set({ name: e.target.value })}
        placeholder="Optional display name"
      />
      <Select label="Schedule" value={values.schedule} onChange={(e) => set({ schedule: e.target.value })}>
        {SCHEDULE_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>
      <Textarea
        className="sm:col-span-2"
        label="Skipped paths"
        helper="One path pattern per line, for example /blog/*."
        rows={4}
        value={values.exclusions}
        onChange={(e) => set({ exclusions: e.target.value })}
      />
      {error && (
        <div className="sm:col-span-2">
          <InlineNotice tone="danger">{error}</InlineNotice>
        </div>
      )}
      <div className="flex justify-between gap-3 sm:col-span-2">
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" busy={busy} disabled={withUrl && !url.trim()}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
