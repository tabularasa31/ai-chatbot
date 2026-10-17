"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { ApiError, api, type GapDraftPayload, type GapItem } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { Button, buttonClasses } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input, Textarea } from "@/components/ui/field";
import { FlashMessage, useFlash } from "@/components/ui/flash";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { DraftContext } from "./DraftContext";

type Busy = "save" | "rewrite" | null;
type Confirm = "publish" | "discard" | "resolve";
type LoadState = { status: "loading" } | { status: "failed" } | { status: "missing" } | { status: "ready"; draft: GapDraftPayload };

const STATUS_LABELS: Partial<Record<GapDraftPayload["status"], string>> = {
  drafting: "Drafting",
  in_review: "In review",
};
const CONFLICT_STATUS = 409;
const NOT_FOUND_STATUS = 404;

function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(undefined, { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

function BackLink() {
  return (
    <Link href="/gap-analyzer" className={buttonClasses({ variant: "ghost" })}>
      <ArrowLeft size={16} aria-hidden="true" />
      Doc gaps
    </Link>
  );
}

function PageShell({ children }: { children: ReactNode }) {
  return (
    <div className="max-w-6xl space-y-6">
      <BackLink />
      {children}
    </div>
  );
}

function FormSkeleton() {
  return (
    <SkeletonRegion>
      <div className="max-w-3xl space-y-6">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    </SkeletonRegion>
  );
}

export default function VisitorQuestionDraftPage() {
  const router = useRouter();
  const { gapId } = useParams<{ gapId: string }>();

  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [cluster, setCluster] = useState<GapItem | null>(null);
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [guidance, setGuidance] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [error, setError] = useState<{ message: string; conflict: boolean } | null>(null);
  const [saved, flashSaved] = useFlash();

  const applyDraft = useCallback((draft: GapDraftPayload) => {
    setState({ status: "ready", draft });
    setTitle(draft.title);
    setQuestion(draft.question);
    setMarkdown(draft.markdown);
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      applyDraft(await api.gapAnalyzer.getModeBDraft(gapId));
    } catch (err) {
      setState({ status: err instanceof ApiError && err.status === NOT_FOUND_STATUS ? "missing" : "failed" });
    }
  }, [applyDraft, gapId]);

  useEffect(() => {
    setState({ status: "loading" });
    void load();
  }, [load]);

  useEffect(() => {
    let current = true;
    api.gapAnalyzer
      .get({ modeBStatus: "all" })
      .then((response) => {
        if (current) setCluster(response.mode_b_items.find((item) => item.id === gapId) ?? null);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [gapId]);

  if (state.status === "loading") {
    return (
      <PageShell>
        <FormSkeleton />
      </PageShell>
    );
  }
  if (state.status === "missing") {
    return (
      <PageShell>
        <p className="text-ui text-fg-muted">This draft no longer exists.</p>
      </PageShell>
    );
  }
  if (state.status === "failed") {
    return (
      <PageShell>
        <LoadError message="Couldn't load this draft." onRetry={load} />
      </PageShell>
    );
  }

  const { draft } = state;
  const locked = busy !== null;

  async function save() {
    setBusy("save");
    setError(null);
    try {
      applyDraft(
        await api.gapAnalyzer.updateModeBDraft(gapId, { title, question, markdown, if_match: draft.draft_updated_at }),
      );
      flashSaved("Draft saved");
    } catch (err) {
      const conflict = err instanceof ApiError && err.status === CONFLICT_STATUS;
      setError({
        message: conflict
          ? "This draft was changed in another tab. Reload to see the latest."
          : "Couldn't save the draft. Try again.",
        conflict,
      });
    } finally {
      setBusy(null);
    }
  }

  async function rewrite() {
    setBusy("rewrite");
    setError(null);
    try {
      applyDraft(await api.gapAnalyzer.refineModeBDraft(gapId, guidance.trim()));
      setGuidance("");
    } catch {
      setError({ message: "Couldn't rewrite the answer. Try again.", conflict: false });
    } finally {
      setBusy(null);
    }
  }

  async function finish(work: () => Promise<unknown>) {
    await work();
    router.push("/gap-analyzer");
  }

  return (
    <PageShell>
      <div className="space-y-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h1 className="text-h1 text-fg">Review FAQ answer</h1>
          <p className="text-caption text-fg-muted">
            {[
              STATUS_LABELS[draft.status],
              `Language: ${languageName(draft.language)}`,
              `Saved ${formatDateTime(draft.draft_updated_at)}`,
            ]
              .filter(Boolean)
              .join(" - ")}
          </p>
        </div>
        {cluster && <p className="break-words text-ui text-fg-muted">{cluster.label}</p>}
      </div>

      <div className={cluster ? "grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]" : ""}>
        <div className="max-w-3xl space-y-6">
          <Input label="Title" value={title} onChange={(event) => setTitle(event.target.value)} />
          <Input
            label="Question visitors ask"
            helper="The bot matches visitor questions to this."
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <Textarea
            label="Answer (Markdown)"
            mono
            rows={16}
            value={markdown}
            onChange={(event) => setMarkdown(event.target.value)}
          />

          <section className="space-y-3">
            <h2 className="text-body font-semibold text-fg">Rewrite the answer</h2>
            <Textarea
              label="How should it change?"
              placeholder="For example: shorter, add an example, friendlier tone."
              helper="Replaces the current draft; unsaved edits are lost."
              rows={3}
              value={guidance}
              onChange={(event) => setGuidance(event.target.value)}
            />
            <Button
              variant="secondary"
              busy={busy === "rewrite"}
              disabled={locked ? busy !== "rewrite" : !guidance.trim()}
              onClick={() => void rewrite()}
            >
              Rewrite answer
            </Button>
          </section>

          {error && (
            <InlineNotice
              tone="danger"
              action={
                error.conflict && (
                  <Button variant="secondary" offset="surface" onClick={() => void load()}>
                    Reload draft
                  </Button>
                )
              }
            >
              {error.message}
            </InlineNotice>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
            <div className="flex flex-wrap gap-3">
              <Button variant="ghost" onClick={() => setConfirm("discard")} disabled={locked}>
                Discard draft
              </Button>
              <Button variant="secondary" onClick={() => setConfirm("resolve")} disabled={locked}>
                Mark as resolved
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <FlashMessage message={saved} />
              <Button variant="secondary" busy={busy === "save"} onClick={() => void save()} disabled={locked && busy !== "save"}>
                Save draft
              </Button>
              <Button variant="primary" onClick={() => setConfirm("publish")} disabled={locked}>
                Publish to bot
              </Button>
            </div>
          </div>
        </div>
        {cluster && <DraftContext cluster={cluster} />}
      </div>

      {confirm === "publish" && (
        <ConfirmDialog
          title="Publish this answer?"
          body="The bot starts answering this question right away."
          confirmLabel="Publish to bot"
          tone="primary"
          failureMessage="Couldn't publish the answer. Try again."
          onConfirm={() => finish(() => api.gapAnalyzer.publishModeBDraft(gapId))}
          onClose={() => setConfirm(null)}
        />
      )}
      {confirm === "discard" && (
        <ConfirmDialog
          title="Discard this draft?"
          body="The draft is deleted and the gap returns to the list."
          confirmLabel="Discard draft"
          tone="destructive"
          failureMessage="Couldn't discard the draft. Try again."
          onConfirm={() => finish(() => api.gapAnalyzer.discardModeBDraft(gapId))}
          onClose={() => setConfirm(null)}
        />
      )}
      {confirm === "resolve" && (
        <ConfirmDialog
          title="Mark as resolved?"
          body="The gap leaves the open list without publishing an answer. Use this if you publish it elsewhere."
          confirmLabel="Mark as resolved"
          tone="primary"
          failureMessage="Couldn't mark the gap as resolved. Try again."
          onConfirm={() => finish(() => api.gapAnalyzer.resolveModeBGap(gapId))}
          onClose={() => setConfirm(null)}
        />
      )}
    </PageShell>
  );
}
