"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type KnowledgeFaqItem } from "@/lib/api";
import { pluralize } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input, Select, Textarea } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { FlashMessage, useFlash } from "@/components/ui/flash";
import { LINK_CLASS } from "./link-class";
import { confidenceLevel } from "./shared";

type FaqFilter = "all" | "pending" | "approved" | "docs";
type FaqState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; items: KnowledgeFaqItem[]; pendingCount: number; refreshFailed: boolean };

const FILTER_OPTIONS: ReadonlyArray<{ value: FaqFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "pending", label: "Waiting for review" },
  { value: "approved", label: "Approved" },
  { value: "docs", label: "From documents" },
];

const QUERY_BY_FILTER = {
  all: { approved: "all", source: "all" },
  pending: { approved: "false", source: "all" },
  approved: { approved: "true", source: "all" },
  docs: { approved: "all", source: "docs" },
} as const;

const SOURCE_LABELS: Record<string, string> = {
  docs: "From documents",
  logs: "From chat history",
  unanswered_queue: "From Unanswered",
};
const SKELETON_ROWS = 4;

function answersCount(count: number): string {
  return `${count} ${pluralize(count, "answer", "answers")}`;
}

function EmptyMessage({ filter, onGoToDocuments }: { filter: FaqFilter; onGoToDocuments: () => void }) {
  if (filter === "pending") return <p className="text-ui text-fg-muted">No answers are waiting for review.</p>;
  if (filter === "approved") return <p className="text-ui text-fg-muted">No approved answers yet.</p>;
  return (
    <p className="text-ui text-fg-muted">
      No FAQ answers yet. Suggestions appear after your documents are indexed.{" "}
      <button type="button" onClick={onGoToDocuments} className={LINK_CLASS}>
        Go to Documents
      </button>
    </p>
  );
}

function ItemMeta({ item, showReviewState }: { item: KnowledgeFaqItem; showReviewState: boolean }) {
  const parts = [SOURCE_LABELS[item.source ?? ""], `${confidenceLevel(item.confidence)} confidence`].filter(Boolean);
  return (
    <p className="text-caption text-fg-muted">
      {parts.join(" · ")}
      {showReviewState && !item.approved && (
        <>
          {" · "}
          <span className="text-warning">Needs review</span>
        </>
      )}
    </p>
  );
}

export function FaqSection({ onGoToDocuments }: { onGoToDocuments: () => void }) {
  const [filter, setFilter] = useState<FaqFilter>("all");
  const [state, setState] = useState<FaqState>({ status: "loading" });
  const latestRequest = useRef(0);
  const [actionError, setActionError] = useState("");
  const [flashMessage, flash] = useFlash();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; question: string; answer: string } | null>(null);
  const [confirmAcceptAll, setConfirmAcceptAll] = useState(false);
  const [rejecting, setRejecting] = useState<KnowledgeFaqItem | null>(null);

  const load = useCallback(async () => {
    const request = ++latestRequest.current;
    try {
      const data = await api.knowledge.listFaq(QUERY_BY_FILTER[filter]);
      if (request !== latestRequest.current) return;
      setState({ status: "ready", items: data.items, pendingCount: data.pending_count, refreshFailed: false });
    } catch {
      if (request !== latestRequest.current) return;
      setState((prev) => (prev.status === "ready" ? { ...prev, refreshFailed: true } : { status: "error" }));
    }
  }, [filter]);

  useEffect(() => {
    setState({ status: "loading" });
    void load();
  }, [load]);

  const items = state.status === "ready" ? state.items : [];
  const pendingCount = state.status === "ready" ? state.pendingCount : 0;

  function changeFilter(value: string) {
    const next = FILTER_OPTIONS.find((option) => option.value === value);
    if (!next) return;
    setFilter(next.value);
    setEditing(null);
  }

  async function run(id: string, action: () => Promise<unknown>, fallback: string) {
    setBusyId(id);
    setActionError("");
    try {
      await action();
      await load();
    } catch {
      setActionError(fallback);
      return false;
    } finally {
      setBusyId(null);
    }
    return true;
  }

  async function saveEdit(item: KnowledgeFaqItem) {
    if (!editing) return;
    const saved = await run(
      item.id,
      () => api.knowledge.updateFaq(item.id, { question: editing.question, answer: editing.answer }),
      "Couldn't save this answer. Try again.",
    );
    if (saved) {
      setEditing(null);
      flash(item.approved ? "Saved. This answer needs review again." : "Saved");
    }
  }

  async function acceptAll() {
    const count = pendingCount;
    await api.knowledge.approveAll();
    await load();
    flash(`${answersCount(count)} accepted`);
  }

  async function reject(item: KnowledgeFaqItem) {
    await api.knowledge.rejectFaq(item.id);
    await load();
  }

  const showReviewState = filter === "all" || filter === "docs";

  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <Select
          label="Show"
          value={filter}
          onChange={(e) => changeFilter(e.target.value)}
          className="w-56 max-w-full"
        >
          {FILTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <div className="flex flex-wrap items-center gap-3 pt-6">
          <FlashMessage message={flashMessage} />
          {pendingCount > 0 && (
            <>
              <span className="text-ui tabular-nums text-fg-muted">{`${pendingCount} waiting for review`}</span>
              <Button variant="primary" onClick={() => setConfirmAcceptAll(true)}>
                Accept all
              </Button>
            </>
          )}
        </div>
      </div>

      {(state.status === "error" || (state.status === "ready" && state.refreshFailed)) && <LoadError message="Couldn't load the FAQ." onRetry={load} />}
      {actionError && (
        <InlineNotice tone="danger" onDismiss={() => setActionError("")}>
          {actionError}
        </InlineNotice>
      )}

      {state.status === "loading" && (
        <SkeletonRegion>
          <div className="space-y-4">
            {Array.from({ length: SKELETON_ROWS }, (_, index) => (
              <Skeleton key={index} className="h-16 w-full" />
            ))}
          </div>
        </SkeletonRegion>
      )}

      {state.status === "ready" && items.length === 0 && <EmptyMessage filter={filter} onGoToDocuments={onGoToDocuments} />}

      {items.length > 0 && (
        <ul className="divide-y divide-line border-y border-line">
          {items.map((item) => {
            const busy = busyId === item.id;
            if (editing?.id === item.id) {
              return (
                <li key={item.id} className="max-w-prose space-y-4 py-4">
                  <Input
                    label="Question"
                    value={editing.question}
                    onChange={(e) => setEditing({ ...editing, question: e.target.value })}
                  />
                  <Textarea
                    label="Answer"
                    rows={5}
                    value={editing.answer}
                    onChange={(e) => setEditing({ ...editing, answer: e.target.value })}
                  />
                  {item.approved && <p className="text-caption text-fg-muted">Saving sends this answer back to review.</p>}
                  <div className="flex gap-3">
                    <Button variant="secondary" onClick={() => setEditing(null)} disabled={busy}>
                      Cancel
                    </Button>
                    <Button
                      variant="primary"
                      busy={busy}
                      disabled={editing.question === item.question && editing.answer === item.answer}
                      onClick={() => void saveEdit(item)}
                    >
                      Save changes
                    </Button>
                  </div>
                </li>
              );
            }
            return (
              <li key={item.id} className="py-4">
                <div className="max-w-prose space-y-1">
                  <p className="break-anywhere text-body font-semibold text-fg">{item.question}</p>
                  <p className="break-anywhere text-ui text-fg">{item.answer}</p>
                  <ItemMeta item={item} showReviewState={showReviewState} />
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <Button
                    variant="ghost"
                    onClick={() => setEditing({ id: item.id, question: item.question, answer: item.answer })}
                  >
                    Edit
                  </Button>
                  <span className={item.approved ? "invisible flex gap-2" : "flex gap-2"} aria-hidden={item.approved}>
                    <Button variant="ghost" disabled={item.approved || busy} tabIndex={item.approved ? -1 : 0} onClick={() => setRejecting(item)}>
                      Reject
                    </Button>
                    <Button
                      variant="secondary"
                      busy={busy}
                      disabled={item.approved}
                      tabIndex={item.approved ? -1 : 0}
                      onClick={() => void run(item.id, () => api.knowledge.approveFaq(item.id), "Couldn't accept this answer. Try again.")}
                    >
                      Accept
                    </Button>
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {confirmAcceptAll && (
        <ConfirmDialog
          title={`Accept ${answersCount(pendingCount)}?`}
          body="The bot starts using them right away."
          confirmLabel="Accept answers"
          tone="primary"
          failureMessage="Couldn't accept the answers. Try again."
          onConfirm={acceptAll}
          onClose={() => setConfirmAcceptAll(false)}
        />
      )}
      {rejecting && (
        <ConfirmDialog
          title="Reject this answer?"
          body="The answer is deleted and can't be restored."
          confirmLabel="Reject and delete"
          tone="destructive"
          failureMessage="Couldn't reject this answer. Try again."
          onConfirm={() => reject(rejecting)}
          onClose={() => setRejecting(null)}
        />
      )}
    </div>
  );
}
