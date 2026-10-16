"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Trash2, X } from "lucide-react";
import { api, type KnowledgeProfile } from "@/lib/api";
import { formatDateTimeLocale } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { FlashMessage, useFlash } from "./flash";
import { confidenceLevel } from "./shared";

const EXTRACTION_POLL_MS = 5000;
const LINK_CLASS =
  "rounded-tag underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";

type EditableProfile = Pick<KnowledgeProfile, "product_name" | "topics" | "support_email" | "support_urls">;
type LoadState = "loading" | "ready" | "error";

const GLOSSARY_SOURCES: Record<string, string> = {
  docs: "Documents",
  logs: "Chat history",
  gap_analyzer: "Doc gaps",
};

function glossarySourceLabel(source?: string | null): string {
  if (!source) return "";
  if (GLOSSARY_SOURCES[source]) return GLOSSARY_SOURCES[source];
  const words = source.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function editable(profile: KnowledgeProfile): EditableProfile {
  const { product_name, topics, support_email, support_urls } = profile;
  return { product_name, topics, support_email, support_urls };
}

function AddRow({
  label,
  placeholder,
  type,
  onAdd,
}: {
  label: string;
  placeholder: string;
  type?: "url";
  onAdd: (value: string) => void;
}) {
  const [value, setValue] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    onAdd(trimmed);
    setValue("");
  }

  return (
    <form onSubmit={submit} className="mt-2 flex items-start gap-2">
      <Input
        label={label}
        hideLabel
        type={type}
        placeholder={placeholder}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="min-w-0 flex-1"
      />
      <Button type="submit" variant="secondary" disabled={!value.trim()}>
        Add
      </Button>
    </form>
  );
}

function StatusLine({
  profile,
  hasSources,
  onGoToDocuments,
  onCheckAgain,
}: {
  profile: KnowledgeProfile;
  hasSources: boolean;
  onGoToDocuments: () => void;
  onCheckAgain: () => Promise<unknown>;
}) {
  const [checking, setChecking] = useState(false);

  async function checkAgain() {
    setChecking(true);
    try {
      await onCheckAgain();
    } finally {
      setChecking(false);
    }
  }

  const documentsLink = (
    <button type="button" onClick={onGoToDocuments} className={LINK_CLASS}>
      Documents
    </button>
  );

  if (profile.extraction_status === "failed") {
    return (
      <InlineNotice tone="danger">
        Extraction failed. Refresh a source in {documentsLink} to try again.
      </InlineNotice>
    );
  }
  if (profile.extraction_status === "pending") {
    if (!hasSources) {
      return (
        <p className="text-caption text-fg-muted">
          The profile is built from your documents. Add one in {documentsLink} first.
        </p>
      );
    }
    return (
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-caption text-fg-muted">Reading your documents. This can take a few minutes.</p>
        <Button variant="secondary" busy={checking} onClick={() => void checkAgain()}>
          Check again
        </Button>
      </div>
    );
  }
  return (
    <p className="text-caption text-fg-muted">
      {`Extracted from your documents. Updated ${formatDateTimeLocale(profile.updated_at)}`}
    </p>
  );
}

function Glossary({ glossary }: { glossary: KnowledgeProfile["glossary"] }) {
  if (glossary.length === 0) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-h2 text-fg">Glossary</h2>
      <details>
        <summary className="flex min-h-11 cursor-pointer items-center text-ui font-medium text-fg">
          {glossary.length === 1 ? "1 term" : `${glossary.length} terms`}
        </summary>
        <table className="w-full table-fixed border-collapse text-left text-ui">
          <thead>
            <tr className="border-b border-line text-caption font-medium text-fg-muted">
              <th scope="col" className="w-1/4 py-2 pr-4 font-medium">Term</th>
              <th scope="col" className="py-2 pr-4 font-medium">Definition</th>
              <th scope="col" className="w-24 py-2 pr-4 font-medium">Confidence</th>
              <th scope="col" className="w-28 py-2 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {glossary.map((entry, index) => (
              <tr key={`${entry.term ?? "term"}-${index}`} className="border-b border-line align-top">
                <td className="break-words py-2 pr-4 font-medium text-fg">{entry.term}</td>
                <td className="break-words py-2 pr-4 text-fg">{entry.definition}</td>
                <td className="py-2 pr-4 text-fg">{confidenceLevel(entry.confidence)}</td>
                <td className="break-words py-2 text-fg-muted">{glossarySourceLabel(entry.source)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

export function ProfileSection({ onGoToDocuments }: { onGoToDocuments: () => void }) {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [profile, setProfile] = useState<KnowledgeProfile | null>(null);
  const [draft, setDraft] = useState<EditableProfile | null>(null);
  const [hasSources, setHasSources] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [savedMessage, flashSaved] = useFlash();

  const loadProfile = useCallback(async () => {
    try {
      const next = await api.knowledge.getProfile();
      setProfile(next);
      setDraft(editable(next));
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, []);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  const pending = profile?.extraction_status === "pending";
  const dirty = profile !== null && draft !== null && JSON.stringify(editable(profile)) !== JSON.stringify(draft);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    if (!pending) return;
    api.documents
      .listSources()
      .then((data) => setHasSources(data.documents.length + data.url_sources.length > 0))
      .catch(() => setHasSources(true));
    const timer = window.setInterval(async () => {
      try {
        const next = await api.knowledge.getProfile();
        setProfile(next);
        if (!dirtyRef.current) setDraft(editable(next));
      } catch {
        // The next tick retries; the visible status stays as it was.
      }
    }, EXTRACTION_POLL_MS);
    return () => window.clearInterval(timer);
  }, [pending]);

  if (loadState === "loading") {
    return (
      <SkeletonRegion>
        <div className="max-w-2xl space-y-4">
          {[0, 1, 2, 3].map((key) => (
            <Skeleton key={key} className="h-11 w-full" />
          ))}
        </div>
      </SkeletonRegion>
    );
  }
  if (loadState === "error" || !profile || !draft) {
    return (
      <div className="max-w-2xl">
        <LoadError message="Couldn't load the profile." onRetry={loadProfile} />
      </div>
    );
  }

  function update(patch: Partial<EditableProfile>) {
    setDraft((prev) => (prev ? { ...prev, ...patch } : prev));
  }

  async function save() {
    if (!draft) return;
    setSaving(true);
    setSaveFailed(false);
    try {
      const updated = await api.knowledge.patchProfile(draft);
      setProfile(updated);
      setDraft(editable(updated));
      flashSaved("Profile saved");
    } catch {
      setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-8">
      <StatusLine
        profile={profile}
        hasSources={hasSources}
        onGoToDocuments={onGoToDocuments}
        onCheckAgain={loadProfile}
      />

      <div className="space-y-6">
        <Input
          label="Product name"
          value={draft.product_name ?? ""}
          onChange={(e) => update({ product_name: e.target.value || null })}
        />

        <fieldset>
          <legend className="mb-1 text-ui font-medium text-fg">Topics</legend>
          <ul className="flex flex-wrap gap-2">
            {draft.topics.map((topic) => (
              <li key={topic}>
                <button
                  type="button"
                  aria-label={`Remove topic ${topic}`}
                  onClick={() => update({ topics: draft.topics.filter((item) => item !== topic) })}
                  className="inline-flex h-11 items-center gap-2 break-all rounded-control bg-surface-alt px-3 text-ui text-fg hover:bg-line focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-canvas"
                >
                  {topic}
                  <X size={16} aria-hidden="true" className="shrink-0 text-fg-muted" />
                </button>
              </li>
            ))}
          </ul>
          <AddRow
            label="New topic"
            placeholder="Add topic"
            onAdd={(value) => !draft.topics.includes(value) && update({ topics: [...draft.topics, value] })}
          />
        </fieldset>

        <Input
          label="Support email"
          type="email"
          value={draft.support_email ?? ""}
          onChange={(e) => update({ support_email: e.target.value || null })}
        />

        <fieldset>
          <legend className="mb-1 text-ui font-medium text-fg">Support URLs</legend>
          <ul className="divide-y divide-line">
            {draft.support_urls.map((url) => (
              <li key={url} className="flex items-center justify-between gap-3">
                <span className="min-w-0 break-all text-ui text-fg">{url}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove ${url}`}
                  title="Remove URL"
                  onClick={() => update({ support_urls: draft.support_urls.filter((item) => item !== url) })}
                >
                  <Trash2 size={20} aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
          <AddRow
            label="New support URL"
            placeholder="https://…"
            type="url"
            onAdd={(value) =>
              !draft.support_urls.includes(value) && update({ support_urls: [...draft.support_urls, value] })
            }
          />
        </fieldset>
      </div>

      <div className="space-y-3">
        {saveFailed && <InlineNotice tone="danger">Couldn&apos;t save the profile. Your changes are still here, try again.</InlineNotice>}
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="primary" busy={saving} disabled={!dirty} onClick={() => void save()}>
            Save changes
          </Button>
          <Button variant="ghost" disabled={!dirty || saving} onClick={() => setDraft(editable(profile))}>
            Discard changes
          </Button>
          <FlashMessage message={savedMessage} />
        </div>
      </div>

      <Glossary glossary={profile.glossary} />
    </div>
  );
}
