"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  DRAFT_ANSWER_MAX_LENGTH,
  DRAFT_INSTRUCTIONS_MAX_LENGTH,
  DRAFT_QUESTION_MAX_LENGTH,
  type UnansweredGroup,
} from "@/lib/api";
import { useClientMe } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CopyButton } from "@/components/ui/copy-button";
import { Drawer } from "@/components/ui/drawer";
import { Input, Textarea } from "@/components/ui/field";
import { FlashMessage, useFlash } from "@/components/ui/flash";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { QuestionList } from "../../gap-analyzer/_components/QuestionList";
import { DraftNotice } from "./DraftNotice";
import { DraftProgress } from "./DraftProgress";
import { useAnswerDraft } from "./useAnswerDraft";

const ANSWER_ROWS = 12;
const DRAFT_READY_ANNOUNCEMENT = "Draft ready. Review the question and answer.";

const ARIA_DISABLED_CLASS =
  "aria-disabled:cursor-not-allowed aria-disabled:bg-surface-alt aria-disabled:text-fg-faint aria-disabled:hover:bg-surface-alt";

function conversationsLabel(count: number): string {
  return count === 1 ? "1 conversation" : `${count} conversations`;
}

function FieldsSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-11 w-full" />
      </div>
      <div className="space-y-2">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-72 w-full" />
      </div>
    </div>
  );
}

export function AnswerDrawer({
  group,
  onClose,
  onDraftStored,
  onPublished,
}: {
  group: UnansweredGroup;
  onClose: () => void;
  onDraftStored: () => void;
  onPublished: (group: UnansweredGroup) => void;
}) {
  const { data: me } = useClientMe();
  const isOwner = me?.role === "owner";
  const draft = useAnswerDraft(group.id, onDraftStored);
  const [flashMessage, flash] = useFlash();
  const [confirming, setConfirming] = useState<"publish" | "discard" | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const questionInput = useRef<HTMLInputElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const wasWaiting = useRef(true);
  const reasonId = useId();

  const { phase, notice, pending, dirty, question, answer, instructions } = draft;
  const waiting = phase.status === "loading" || phase.status === "generating";
  const noDraftYet = !draft.canAskAgent;
  const hasContent = question.trim() !== "" && answer.trim() !== "";

  useEffect(() => {
    if (wasWaiting.current && phase.status === "ready") {
      setAnnouncement(DRAFT_READY_ANNOUNCEMENT);
      const active = document.activeElement;
      if (!active || active === document.body || body.current?.closest("dialog")?.contains(active)) {
        questionInput.current?.focus();
      }
    }
    wasWaiting.current = waiting;
  }, [phase.status, waiting]);

  function requestClose() {
    if (dirty) setConfirming("discard");
    else onClose();
  }

  function requestPublish() {
    if (isOwner) setConfirming("publish");
  }

  async function saveDraft() {
    if (await draft.saveDraft()) flash("Draft saved");
  }

  async function publish() {
    const result = await draft.publish();
    if (result) onPublished(result.group);
  }

  return (
    <>
      <Drawer
        title="Answer this question"
        onClose={requestClose}
        canClose={!dirty}
        onCloseBlocked={() => setConfirming("discard")}
      >
        <div ref={body} className="min-h-0 flex-1 space-y-6 overflow-y-auto p-6">
          <section className="space-y-2">
            <h3 className="text-body font-semibold text-fg">Visitor questions</h3>
            <QuestionList questions={[group.question]} />
            <p className="text-caption tabular-nums text-fg-muted">
              {conversationsLabel(group.conversations_30d)} in the last 30 days
            </p>
          </section>

          {notice && (
            <DraftNotice
              notice={notice}
              isOwner={isOwner}
              canRetry={noDraftYet}
              onRetry={draft.retryLoad}
              onReload={draft.reloadDraft}
            />
          )}

          {phase.status === "loading" && (
            <SkeletonRegion>
              <FieldsSkeleton />
            </SkeletonRegion>
          )}

          {phase.status === "generating" && (
            <>
              <DraftProgress />
              <FieldsSkeleton />
            </>
          )}

          {phase.status === "loadError" && (
            <LoadError message="Couldn't load this draft." onRetry={async () => draft.retryLoad()} />
          )}

          {phase.status === "ready" && (
            <>
              <Input
                ref={questionInput}
                label="Question visitors ask"
                helper="The bot matches visitor questions to this."
                maxLength={DRAFT_QUESTION_MAX_LENGTH}
                value={question}
                onChange={(e) => draft.setQuestion(e.target.value)}
              />
              <Textarea
                mono
                rows={ANSWER_ROWS}
                label="Answer (Markdown)"
                maxLength={DRAFT_ANSWER_MAX_LENGTH}
                value={answer}
                onChange={(e) => draft.setAnswer(e.target.value)}
              />
              <div className="space-y-3">
                <Textarea
                  label="Instructions"
                  placeholder="For example: shorter, add an example, friendlier tone."
                  helper="Replaces the current answer. Unsaved edits are lost."
                  maxLength={DRAFT_INSTRUCTIONS_MAX_LENGTH}
                  rows={3}
                  value={instructions}
                  onChange={(e) => draft.setInstructions(e.target.value)}
                />
                <Button
                  variant="secondary"
                  busy={pending === "refine"}
                  disabled={noDraftYet || notice?.kind === "no_key" || instructions.trim() === "" || pending === "save"}
                  onClick={() => void draft.askAgent()}
                >
                  Ask agent
                </Button>
              </div>
            </>
          )}

          {draft.keptText !== null && (
            <section className="space-y-2">
              <h3 className="text-body font-semibold text-fg">Your previous text</h3>
              <pre className="whitespace-pre-wrap break-words rounded-control bg-surface-alt p-3 font-mono text-caption text-fg">
                {draft.keptText}
              </pre>
              <CopyButton text={draft.keptText} label="Copy" variant="secondary" />
            </section>
          )}

          <p role="status" className="sr-only">
            {announcement}
          </p>
        </div>

        <footer className="flex items-center justify-between gap-4 border-t border-line px-6 py-4">
          <p id={reasonId} className="min-w-0 text-caption text-fg-muted">
            {me && !isOwner && "Only the tenant owner can publish. Your draft is kept for the owner."}
          </p>
          <div className="flex shrink-0 items-center gap-3">
            <FlashMessage message={flashMessage} />
            <Button
              variant="secondary"
              offset="surface"
              busy={pending === "save"}
              disabled={!hasContent || !dirty || pending === "refine"}
              onClick={() => void saveDraft()}
            >
              Save draft
            </Button>
            <Button
              variant="primary"
              offset="surface"
              className={ARIA_DISABLED_CLASS}
              disabled={isOwner && (!hasContent || waiting || pending !== null)}
              aria-disabled={me && !isOwner ? true : undefined}
              aria-describedby={me && !isOwner ? reasonId : undefined}
              onClick={requestPublish}
            >
              Publish to bot
            </Button>
          </div>
        </footer>
      </Drawer>

      {confirming === "publish" && (
        <ConfirmDialog
          title="Publish this answer?"
          body="The bot starts answering this question right away."
          confirmLabel="Publish to bot"
          tone="primary"
          failureMessage="Couldn't publish the answer. Try again."
          onConfirm={publish}
          onClose={() => setConfirming(null)}
        />
      )}
      {confirming === "discard" && (
        <ConfirmDialog
          title="Discard unsaved changes?"
          body="Your edits to this draft will be lost."
          confirmLabel="Discard changes"
          cancelLabel="Keep editing"
          tone="destructive"
          failureMessage="Couldn't close the panel. Try again."
          onConfirm={async () => onClose()}
          onClose={() => setConfirming(null)}
        />
      )}
    </>
  );
}
