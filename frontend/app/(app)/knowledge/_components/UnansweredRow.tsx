"use client";

import Link from "next/link";
import { useEffect, useId, useRef } from "react";
import type { UnansweredDismissReason, UnansweredGroup } from "@/lib/api";
import { parseApiDate } from "@/lib/format";
import { Button, buttonClasses } from "@/components/ui/button";
import { DISMISS_REASON_LABELS, NotNeededForm } from "./NotNeededForm";
import { TEXT_LINK_CLASS } from "./link-class";
import { conversationsLabel } from "./shared";

type MetaPart = { text: string; warning?: boolean };

function lastAskedLabel(iso: string): string {
  const date = parseApiDate(iso);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  const formatted = date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
  });
  return `Last asked ${formatted}`;
}

function statusPart(group: UnansweredGroup): MetaPart | null {
  if (group.status === "dismissed" && group.dismiss_reason) {
    return { text: `Not needed: ${DISMISS_REASON_LABELS[group.dismiss_reason]}` };
  }
  if (group.status === "open" && group.has_draft) return { text: "Draft saved" };
  return null;
}

function metaParts(group: UnansweredGroup): MetaPart[] {
  const parts: MetaPart[] = [
    { text: conversationsLabel(group.conversations_30d) },
    { text: lastAskedLabel(group.last_asked_at) },
  ];
  if (group.reasons.some((reason) => reason !== "unanswered")) parts.push({ text: "Bot offered a human" });
  if (group.status === "open" && group.reopened_after_answer) parts.push({ text: "Answer didn't help", warning: true });
  const status = statusPart(group);
  if (status) parts.push(status);
  return parts;
}

function RowMeta({ group }: { group: UnansweredGroup }) {
  return (
    <p className="text-caption tabular-nums text-fg-muted">
      {metaParts(group).map(({ text, warning }, index) => (
        <span key={text}>
          {index > 0 && " · "}
          <span className={warning ? "text-warning" : undefined}>{text}</span>
        </span>
      ))}
    </p>
  );
}

export function UnansweredRow({
  group,
  choosingReason,
  busy,
  onAnswer,
  onChooseReason,
  onCancelReason,
  onDismiss,
  onRestore,
}: {
  group: UnansweredGroup;
  choosingReason: boolean;
  busy: boolean;
  onAnswer: () => void;
  onChooseReason: () => void;
  onCancelReason: () => void;
  onDismiss: (reason: UnansweredDismissReason) => void;
  onRestore: () => void;
}) {
  const questionId = useId();
  const row = useRef<HTMLLIElement>(null);
  const wasChoosing = useRef(false);

  useEffect(() => {
    if (wasChoosing.current && !choosingReason) row.current?.querySelector("button")?.focus();
    wasChoosing.current = choosingReason;
  }, [choosingReason]);

  return (
    <li ref={row} className="flex items-start gap-6 py-4">
      <div className="min-w-0 flex-1 space-y-1">
        <p id={questionId} className="break-anywhere text-body font-semibold text-fg">
          {group.question}
        </p>
        <RowMeta group={group} />
      </div>
      {choosingReason ? (
        <NotNeededForm name={`reason-${group.id}`} busy={busy} onSubmit={onDismiss} onCancel={onCancelReason} />
      ) : (
        <div className="-mt-2.5 flex shrink-0 items-center gap-2">
          <Link
            href={`/inbox?scope=all&session=${encodeURIComponent(group.session_id)}`}
            aria-describedby={questionId}
            className={buttonClasses({ variant: "ghost" })}
          >
            Open conversation
          </Link>
          {group.status === "open" && (
            <>
              <Button variant="ghost" aria-describedby={questionId} onClick={onChooseReason}>
                Not needed
              </Button>
              <Button variant="secondary" aria-describedby={questionId} onClick={onAnswer}>
                Answer
              </Button>
            </>
          )}
          {group.status === "answered" && (
            <Link
              href="/knowledge?tab=faq"
              aria-describedby={questionId}
              className={TEXT_LINK_CLASS}
            >
              View FAQ entry
            </Link>
          )}
          {group.status === "dismissed" && (
            <Button variant="secondary" busy={busy} aria-describedby={questionId} onClick={onRestore}>
              Restore
            </Button>
          )}
        </div>
      )}
    </li>
  );
}
