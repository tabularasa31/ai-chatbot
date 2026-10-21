"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { mutate as mutateGlobal } from "swr";
import { api, type UnansweredDismissReason, type UnansweredGroup, type UnansweredStatus } from "@/lib/api";
import { UNANSWERED_BADGE_KEY } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { FlashMessage, useFlash } from "@/components/ui/flash";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { LINK_CLASS } from "./link-class";
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

type ListState =
  | { status: "loading" }
  | { status: "error" }
  | {
      status: "ready";
      items: UnansweredGroup[];
      nextOffset: number | null;
      tenantConversations30d: number;
      tenantHasConversations: boolean;
      refreshFailed: boolean;
    };

export function parseShow(value: string | null): UnansweredShow {
  return SHOW_OPTIONS.find((option) => option.value === value)?.value ?? "open";
}

function conversationsLabel(count: number): string {
  return count === 1 ? "1 conversation" : `${count} conversations`;
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
        <p className="pt-3 text-ui">
          <Link href="/dashboard" className={LINK_CLASS}>
            Install widget
          </Link>
        </p>
      )}
    </div>
  );
}

export function UnansweredSection({
  show,
  onShowChange,
  onAnswer,
}: {
  show: UnansweredShow;
  onShowChange: (show: UnansweredShow) => void;
  onAnswer: (group: UnansweredGroup) => void;
}) {
  const status = STATUS_BY_SHOW[show];
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [actionError, setActionError] = useState("");
  const [flashMessage, flash] = useFlash();
  const [choosingId, setChoosingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const latestRequest = useRef(0);
  const filter = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const pendingFocusIndex = useRef<number | null>(null);

  useEffect(() => {
    void mutateGlobal(UNANSWERED_BADGE_KEY, { new_count: 0 }, { revalidate: false });
    api.unanswered.markSeen().catch(() => {});
  }, []);

  const load = useCallback(async () => {
    const request = ++latestRequest.current;
    try {
      const data = await api.unanswered.list(status);
      if (request !== latestRequest.current) return;
      setState({
        status: "ready",
        items: data.items,
        nextOffset: data.next_offset,
        tenantConversations30d: data.tenant_conversations_30d,
        tenantHasConversations: data.tenant_has_conversations,
        refreshFailed: false,
      });
    } catch {
      if (request !== latestRequest.current) return;
      setState((prev) => (prev.status === "ready" ? { ...prev, refreshFailed: true } : { status: "error" }));
    }
  }, [status]);

  useEffect(() => {
    setState({ status: "loading" });
    setChoosingId(null);
    setActionError("");
    void load();
  }, [load]);

  useEffect(() => {
    const index = pendingFocusIndex.current;
    if (index === null) return;
    pendingFocusIndex.current = null;
    const next = list.current?.children[index]?.querySelector<HTMLElement>("button, a");
    (next ?? filter.current?.querySelector("select"))?.focus();
  }, [state]);

  async function loadMore() {
    if (state.status !== "ready" || state.nextOffset === null) return;
    setLoadingMore(true);
    setActionError("");
    try {
      const data = await api.unanswered.list(status, state.nextOffset);
      setState({ ...state, items: [...state.items, ...data.items], nextOffset: data.next_offset, refreshFailed: false });
    } catch {
      setState({ ...state, refreshFailed: true });
    } finally {
      setLoadingMore(false);
    }
  }

  async function leaveList(group: UnansweredGroup, action: () => Promise<unknown>, failure: string, success: string) {
    if (state.status !== "ready") return;
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
    const index = state.items.findIndex((item) => item.id === group.id);
    pendingFocusIndex.current = index;
    setChoosingId(null);
    setState({
      ...state,
      items: state.items.filter((item) => item.id !== group.id),
      nextOffset: state.nextOffset === null ? null : state.nextOffset - 1,
    });
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
        <SkeletonRegion>
          <div className="divide-y divide-line border-y border-line">
            {Array.from({ length: SKELETON_ROWS }, (_, index) => (
              <div key={index} className="space-y-2 py-4">
                <Skeleton className="h-6 w-2/3" />
                <Skeleton className="h-4 w-1/3" />
              </div>
            ))}
          </div>
        </SkeletonRegion>
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
              onAnswer={() => onAnswer(group)}
              onChooseReason={() => setChoosingId(group.id)}
              onCancelReason={() => setChoosingId(null)}
              onDismiss={(reason) => void dismiss(group, reason)}
              onRestore={() => void restore(group)}
            />
          ))}
        </ul>
      )}

      {state.status === "ready" && state.nextOffset !== null && (
        <Button variant="secondary" busy={loadingMore} onClick={() => void loadMore()}>
          Show more
        </Button>
      )}
    </div>
  );
}
