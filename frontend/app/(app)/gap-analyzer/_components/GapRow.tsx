import Link from "next/link";
import type { GapItem } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { Button, buttonClasses } from "@/components/ui/button";
import { QuestionList } from "./QuestionList";
import { coverageText, isInDraftFlow, lifecycleWord } from "./shared";

export type RowAction = "generate" | "preview" | "reactivate" | "dismiss";
export type RowPending = { id: string; action: RowAction } | null;

export type RowHandlers = {
  onGenerate: (item: GapItem) => void;
  onPreview: (item: GapItem) => void;
  onDismiss: (item: GapItem) => void;
  onReactivate: (item: GapItem) => void;
};

export type RowContext = {
  pending: RowPending;
  hasOpenAiKey: boolean;
  keyNoticeId: string;
  handlers: RowHandlers;
};

const DOT_CLASS = { danger: "bg-danger", warning: "bg-warning", success: "bg-success" } as const;
const TEXT_LINK_CLASS =
  "inline-flex h-11 items-center rounded-control text-ui text-brand underline " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

function MetaLine({ item }: { item: GapItem }) {
  const coverage = coverageText(item);
  const lifecycle = lifecycleWord(item);
  const parts = [
    coverage && (
      <span key="coverage" className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${DOT_CLASS[coverage.dot]}`} />
        {coverage.label}
      </span>
    ),
    <span key="count" className="tabular-nums">
      {item.question_count} {item.question_count === 1 ? "question" : "questions"}
    </span>,
    item.is_new && (
      <span key="new" className="text-brand">
        New
      </span>
    ),
    lifecycle && <span key="lifecycle">{lifecycle}</span>,
    item.linked_label && <span key="linked">Also missing from docs: {item.linked_label}</span>,
  ].filter(Boolean);
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-fg-muted">
      {parts.map((part, index) => (
        <span key={index} className="inline-flex items-center gap-2">
          {index > 0 && <span aria-hidden="true">-</span>}
          {part}
        </span>
      ))}
    </p>
  );
}

function RowActions({ item, context }: { item: GapItem; context: RowContext }) {
  const { pending, hasOpenAiKey, keyNoticeId, handlers } = context;
  const pendingAction = pending?.id === item.id ? pending.action : null;
  const locked = pendingAction !== null;
  const dismiss = (
    <Button variant="ghost" busy={pendingAction === "dismiss"} onClick={() => handlers.onDismiss(item)} disabled={locked}>
      Dismiss
    </Button>
  );

  if (item.status === "dismissed" || item.status === "inactive") {
    return (
      <Button variant="secondary" busy={pendingAction === "reactivate"} onClick={() => handlers.onReactivate(item)} disabled={locked && pendingAction !== "reactivate"}>
        Reactivate
      </Button>
    );
  }
  if (item.status === "resolved") {
    return item.published_faq_id ? (
      <Link href="/knowledge?tab=faq" className={TEXT_LINK_CLASS}>
        View published answer
      </Link>
    ) : null;
  }
  if (item.status === "closed") return null;
  if (isInDraftFlow(item)) {
    return (
      <>
        <Link href={`/gap-analyzer/mode_b/${item.id}/draft`} className={buttonClasses({ variant: "secondary" })}>
          Continue draft
        </Link>
        {dismiss}
      </>
    );
  }
  if (item.source === "mode_b") {
    return (
      <>
        <Button
          variant="secondary"
          busy={pendingAction === "generate"}
          onClick={() => handlers.onGenerate(item)}
          disabled={!hasOpenAiKey || (locked && pendingAction !== "generate")}
          aria-describedby={hasOpenAiKey ? undefined : keyNoticeId}
        >
          Draft FAQ answer
        </Button>
        {dismiss}
      </>
    );
  }
  return (
    <>
      <Button variant="secondary" busy={pendingAction === "preview"} onClick={() => handlers.onPreview(item)} disabled={locked && pendingAction !== "preview"}>
        Preview draft
      </Button>
      {dismiss}
    </>
  );
}

export function GapRow({ item, context, Heading }: { item: GapItem; context: RowContext; Heading: "h2" | "h3" }) {
  return (
    <li className="flex gap-6 border-b border-line py-4">
      <div className="min-w-0 flex-1 space-y-2">
        <Heading className="break-words text-body font-semibold text-fg">{item.label}</Heading>
        <MetaLine item={item} />
        {item.example_questions.length > 0 && <QuestionList questions={item.example_questions} />}
        {item.linked_example_questions.length > 0 && (
          <div>
            <p className="text-caption text-fg-muted">Docs gap on the same topic</p>
            <QuestionList questions={item.linked_example_questions} />
          </div>
        )}
        <p className="text-caption tabular-nums text-fg-muted">
          Last updated {formatDateTime(item.last_updated)}
        </p>
      </div>
      <div className="flex w-56 shrink-0 flex-col items-stretch gap-1">
        <RowActions item={item} context={context} />
      </div>
    </li>
  );
}
