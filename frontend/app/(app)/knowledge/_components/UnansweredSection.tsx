"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { mutate as mutateGlobal } from "swr";
import { api, type UnansweredDismissReason, type UnansweredGroup, type UnansweredStatus } from "@/lib/api";
import { UNANSWERED_BADGE_KEY } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { FlashMessage, useFlash } from "@/components/ui/flash";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { RowListSkeleton } from "@/components/ui/skeleton";
import { TEXT_LINK_CLASS } from "./link-class";
import { conversationsLabel } from "./shared";
import { useUnansweredList } from "./useUnansweredList";
import { AnswerDrawer } from "./AnswerDrawer";
import { UnansweredRow } from "./UnansweredRow";

export type UnansweredShow = "open" | "answered" | "not-needed";

const SHOW_OPTIONS: readonly { value: UnansweredShow; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "answered", label: "Answered" },
  { value: "not-needed", label: "Not needed" },
];

const STATUS_BY_SHOW: Record<UnansweredShow, UnansweredStatus> = {
  open: "open",
  answered: "answered",
  "not-needed": "dismissed",
};

const SKELETON_ROWS = 5;

export function parseShow(value: string | null): UnansweredShow {
  return SHOW_OPTIONS.find((option) => option.value === value)?.value ?? "open";
}

function EmptyState({
  show,
  conversations30d,
  hasConversations,
}: {
  show: UnansweredShow;
  conversations30d: number;
  hasConversations: boolean;
}) {
  if (show === "answered") return <p className="text-ui text-fg-muted">No answered questions yet.</p>;
  if (show === "not-needed") return <p className="text-ui text-fg-muted">No questions marked as not needed.</p>;
  return (
    <div className="space-y-1">
      <p className="text-ui font-medium text-fg">No unanswered questions yet</p>
      <p className="text-ui text-fg-muted">
        {`Visitors had ${conversationsLabel(conversations30d)} in the last 30 days.`}
        {!hasConversations && " Install the widget on your site to start getting questions."}
      </p>
      {!hasConversations && (
        <Link href="/dashboard" className={TEXT_LINK_CLASS}>
          Install widget
        </Link>
      )}
    </div>
  );
}

export function UnansweredSection({
  show,
  onShowChange,
}: {
  show: UnansweredShow;
  onShowChange: (show: UnansweredShow) => void;
}) {
  const status = STATUS_BY_SHOW[show];
  const { state, loadingMore, load, loadMore, indexOf, remove, markDraftStored } = useUnansweredList(status);
  const [actionError, setActionError] = useState("");
  const [flashMessage, flash] = useFlash();
  const [choosingId, setChoosingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [answering, setAnswering] = useState<UnansweredGroup | null>(null);
  const filter = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const pendingFocusIndex = useRef<number | null>(null);

  useEffect(() => {
    void mutateGlobal(UNANSWERED_BADGE_KEY, { new_count: 0 }, { revalidate: false });
    api.unanswered.markSeen().catch(() => mutateGlobal(UNANSWERED_BADGE_KEY));
  }, []);

  useEffect(() => {
    setChoosingId(null);
    setActionError("");
  }, [status]);

  useEffect(() => {
    const index = pendingFocusIndex.current;
    if (index === null) return;
    pendingFocusIndex.current = null;
    const next = list.current?.children[index]?.querySelector<HTMLElement>("button, a");
    (next ?? filter.current?.querySelector("select"))?.focus();
  }, [state]);

  async function showMore() {
    setActionError("");
    await loadMore();
  }

  async function leaveList(group: UnansweredGroup, action: () => Promise<unknown>, failure: string, success: string) {
    setBusyId(group.id);
    setActionError("");
    try {
      await action();
    } catch {
      setActionError(failure);
      return;
    } finally {
      setBusyId(null);
    }
    removeFromList(group, success);
  }

  function removeFromList(group: UnansweredGroup, success: string) {
    const index = indexOf(group.id);
    remove(group.id);
    if (index >= 0) pendingFocusIndex.current = index;
    setChoosingId(null);
    setAnswering(null);
    flash(success);
  }

  function dismiss(group: UnansweredGroup, reason: UnansweredDismissReason) {
    return leaveList(
      group,
      () => api.unanswered.dismiss(group.id, reason),
      "Couldn't mark this question as not needed. Try again.",
      "Moved to Not needed",
    );
  }

  function restore(group: UnansweredGroup) {
    return leaveList(
      group,
      () => api.unanswered.restore(group.id),
      "Couldn't restore this question. Try again.",
      "Restored to Open",
    );
  }

  const items = state.status === "ready" ? state.items : [];
  const loadFailed = state.status === "error" || (state.status === "ready" && state.refreshFailed);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div ref={filter}>
          <Select
            label="Show"
            value={show}
            onChange={(e) => onShowChange(parseShow(e.target.value))}
            className="w-56 max-w-full"
          >
            {SHOW_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="pt-6">
          <FlashMessage message={flashMessage} />
        </div>
      </div>

      {loadFailed && <LoadError message="Couldn't load unanswered questions." onRetry={load} />}
      {actionError && (
        <InlineNotice tone="danger" onDismiss={() => setActionError("")}>
          {actionError}
        </InlineNotice>
      )}

      {state.status === "loading" && (
        <RowListSkeleton rows={SKELETON_ROWS} className="border-y border-line" />
      )}

      {state.status === "ready" && items.length === 0 && (
        <EmptyState
          show={show}
          conversations30d={state.tenantConversations30d}
          hasConversations={state.tenantHasConversations}
        />
      )}

      {items.length > 0 && (
        <ul ref={list} className="divide-y divide-line border-y border-line">
          {items.map((group) => (
            <UnansweredRow
              key={group.id}
              group={group}
              choosingReason={choosingId === group.id}
              busy={busyId === group.id}
              onAnswer={() => setAnswering(group)}
              onChooseReason={() => setChoosingId(group.id)}
              onCancelReason={() => setChoosingId(null)}
              onDismiss={(reason) => void dismiss(group, reason)}
              onRestore={() => void restore(group)}
            />
          ))}
        </ul>
      )}

      {answering && (
        <AnswerDrawer
          group={answering}
          onClose={() => setAnswering(null)}
          onDraftStored={() => markDraftStored(answering.id)}
          onPublished={(published) => removeFromList(published, "Published. Find it under Answered.")}
        />
      )}

      {state.status === "ready" && state.nextOffset !== null && (
        <Button variant="secondary" busy={loadingMore} onClick={() => void showMore()}>
          Show more
        </Button>
      )}
    </div>
  );
}
