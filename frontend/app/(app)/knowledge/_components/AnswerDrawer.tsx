"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  DRAFT_ANSWER_MAX_LENGTH,
  DRAFT_INSTRUCTIONS_MAX_LENGTH,
  DRAFT_QUESTION_MAX_LENGTH,
  draftErrorCode,
  type DraftErrorCode,
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
import { QuestionList } from "./QuestionList";
import { conversationsLabel, withLimit } from "./shared";
import { DraftNotice } from "./DraftNotice";
import { DraftProgress } from "./DraftProgress";
import { useAnswerDraft } from "./useAnswerDraft";

const ANSWER_ROWS = 12;
const DRAFT_READY_ANNOUNCEMENT = "Draft ready. Review the question and answer.";

const MISSING_CONTENT_REASON = "Add a question and an answer to save or publish.";
const OPERATOR_REASON = "Only the tenant owner can publish. Your draft is kept for the owner.";

const PUBLISH_FAILED = "Couldn't publish the answer. Try again.";

const PUBLISH_FAILURES: Partial<Record<DraftErrorCode, string>> = {
  draft_rejected: "This text looks like an instruction to the bot. Edit it before publishing.",
  group_answered: "This question was already answered. Close this panel and refresh the list.",
  faq_embedding_failed: "Couldn't publish the answer, and nothing was published. Try again.",
};

const ARIA_DISABLED_CLASS =
  "aria-disabled:cursor-not-allowed aria-disabled:bg-surface-alt aria-disabled:text-fg-faint aria-disabled:hover:bg-surface-alt";

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
  const draft = useAnswerDraft(group.id, group.question, onDraftStored);
  const [flashMessage, flash] = useFlash();
  const [confirming, setConfirming] = useState<"publish" | "discard" | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [publishFailure, setPublishFailure] = useState(PUBLISH_FAILED);
  const questionInput = useRef<HTMLInputElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const wasWaiting = useRef(true);
  const reasonId = useId();

  const { phase, notice, pending, dirty, question, answer, instructions } = draft;
  const waiting = phase.status === "loading" || phase.status === "generating";
  const hasDraft = draft.canAskAgent;
  const noDraftYet = !hasDraft;
  const hasContent = question.trim() !== "" && answer.trim() !== "";
  const ready = phase.status === "ready";
  const missingContent = ready && !hasContent;
  const operatorBlocked = me !== undefined && !isOwner;
  const footerReason = missingContent ? MISSING_CONTENT_REASON : operatorBlocked ? OPERATOR_REASON : "";

  useEffect(() => {
    if (wasWaiting.current && phase.status === "ready" && draft.draftApplied) {
      setAnnouncement(DRAFT_READY_ANNOUNCEMENT);
      const active = document.activeElement;
      if (!active || active === document.body || body.current?.closest("dialog")?.contains(active)) {
        questionInput.current?.focus();
      }
    }
    wasWaiting.current = waiting;
  }, [phase.status, waiting, draft.draftApplied]);

  function requestClose() {
    if (dirty) setConfirming("discard");
    else onClose();
  }

  function requestPublish() {
    if (isOwner && hasContent) setConfirming("publish");
  }

  async function saveDraft() {
    if (await draft.saveDraft()) flash("Draft saved");
  }

  async function publish() {
    try {
      const result = await draft.publish();
      if (result) onPublished(result.group);
    } catch (err) {
      const code = draftErrorCode(err);
      setPublishFailure((code && PUBLISH_FAILURES[code]) || PUBLISH_FAILED);
      throw err;
    }
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
            <p className="text-caption tabular-nums text-fg-muted">
              {conversationsLabel(group.conversations_30d)} in the last 30 days
            </p>
            <QuestionList questions={draft.examples.length > 0 ? draft.examples : [group.question]} />
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
              <DraftProgress questionCount={draft.examples.length} />
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
                helper={withLimit("The bot matches visitor questions to this.", question.length, DRAFT_QUESTION_MAX_LENGTH)}
                maxLength={DRAFT_QUESTION_MAX_LENGTH}
                value={question}
                onChange={(e) => draft.setQuestion(e.target.value)}
              />
              <Textarea
                mono
                resizable
                rows={ANSWER_ROWS}
                label="Answer (Markdown)"
                helper={withLimit(undefined, answer.length, DRAFT_ANSWER_MAX_LENGTH)}
                maxLength={DRAFT_ANSWER_MAX_LENGTH}
                value={answer}
                onChange={(e) => draft.setAnswer(e.target.value)}
              />
              <section className="space-y-3">
                <h3 className="text-body font-semibold text-fg">Ask agent</h3>
                <Textarea
                  label="Instructions"
                  placeholder="For example: shorter, add an example, friendlier tone."
                  helper={withLimit(
                    "Replaces the current answer. Unsaved edits are lost.",
                    instructions.length,
                    DRAFT_INSTRUCTIONS_MAX_LENGTH,
                  )}
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
              </section>
            </>
          )}

          {draft.keptText !== null && (
            <section className="space-y-2">
              <h3 className="text-body font-semibold text-fg">Your previous text</h3>
              <pre className="whitespace-pre-wrap break-anywhere rounded-control bg-surface-alt p-3 font-mono text-caption text-fg">
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
            {footerReason}
          </p>
          <div className="flex shrink-0 items-center gap-3">
            <FlashMessage message={flashMessage} />
            <Button
              variant="secondary"
              offset="surface"
              busy={pending === "save"}
              disabled={!ready || !hasContent || !dirty || pending !== null}
              aria-describedby={missingContent ? reasonId : undefined}
              onClick={() => void saveDraft()}
            >
              Save draft
            </Button>
            <Button
              variant="primary"
              offset="surface"
              className={ARIA_DISABLED_CLASS}
              disabled={me === undefined || !ready || pending !== null}
              aria-disabled={operatorBlocked || missingContent ? true : undefined}
              aria-describedby={operatorBlocked || missingContent ? reasonId : undefined}
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
          failureMessage={publishFailure}
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
          onConfirm={async () => onClose()}
          onClose={() => setConfirming(null)}
        />
      )}
    </>
  );
}
