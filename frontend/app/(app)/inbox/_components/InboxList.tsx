import Link from "next/link";
import type { InboxRow } from "@/lib/api";
import { Button, FOCUS_RING, buttonClasses } from "@/components/ui/button";
import { LoadError } from "@/components/ui/load-error";
import { cn } from "@/components/ui/utils";
import { previewPrefix, rowMeta, visitorLabel } from "../_lib/labels";
import { DeferredSkeleton } from "./DeferredSkeleton";
import { StatePill } from "./StatePill";

export type Scope = "attention" | "all";

const SKELETON_ROWS = 6;
const SKELETON_BARS = ["w-1/2", "w-3/4", "w-1/3"];

function InboxRowItem({
  row,
  viewerId,
  selected,
  now,
  onSelect,
}: {
  row: InboxRow;
  viewerId: string | undefined;
  selected: boolean;
  now: number;
  onSelect: () => void;
}) {
  const prefix = previewPrefix(row, viewerId);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "block w-full border-l-2 border-transparent px-4 py-3 text-left text-ui hover:bg-surface-alt",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
          selected && "border-brand bg-brand-subtle hover:bg-brand-subtle",
        )}
      >
        <span className="flex items-center gap-2">
          <span
            dir="auto"
            className="min-w-0 flex-1 truncate font-medium text-fg"
            title={row.visitor_email ?? undefined}
          >
            {visitorLabel(row)}
          </span>
          <StatePill state={row.handoff_state} />
        </span>
        <span className="block truncate text-fg-muted">
          {prefix && <span title={prefix.title}>{prefix.text}</span>}
          <span dir="auto" title={row.last_message_preview ?? undefined}>
            {row.last_message_preview ?? "(no messages)"}
          </span>
        </span>
        <span className="block truncate text-caption tabular-nums text-fg-muted">
          {rowMeta(row, viewerId, now)}
          {row.ticket && (
            <>
              {" · "}
              <span className="font-mono">{row.ticket.ticket_number}</span>
            </>
          )}
        </span>
      </button>
    </li>
  );
}

function RowsSkeleton() {
  return (
    <DeferredSkeleton>
      {Array.from({ length: SKELETON_ROWS }, (_, row) => (
        <div key={row} className="space-y-2 border-b border-line px-4 py-3">
          {SKELETON_BARS.map((width) => (
            <div key={width} className={cn("h-4 rounded-tag bg-surface-alt", width)} />
          ))}
        </div>
      ))}
    </DeferredSkeleton>
  );
}

function EmptyList({
  scope,
  query,
  onShowAll,
}: {
  scope: Scope;
  query: string;
  onShowAll: () => void;
}) {
  const showAll = (label: string) => (
    <Button variant="secondary" offset="surface" onClick={onShowAll}>
      {label}
    </Button>
  );
  return (
    <div className="space-y-3 p-4 text-ui text-fg-muted">
      <p dir="auto">
        {query
          ? `Nothing matches “${query}”.`
          : scope === "attention"
            ? "No conversation needs a person right now."
            : "No conversations yet."}
      </p>
      {query && scope === "attention" && showAll("Search all conversations")}
      {!query && scope === "attention" && showAll("Show all conversations")}
      {!query && scope === "all" && (
        <Link href="/dashboard" className={buttonClasses({ variant: "secondary", offset: "surface" })}>
          Install widget
        </Link>
      )}
    </div>
  );
}

export function InboxList({
  rows,
  error,
  isCurrent,
  scope,
  search,
  query,
  viewerId,
  selected,
  now,
  onSearchChange,
  onSelect,
  onShowAll,
  onRetry,
}: {
  rows: InboxRow[];
  error: unknown;
  isCurrent: boolean;
  scope: Scope;
  search: string;
  query: string;
  viewerId: string | undefined;
  selected: string | null;
  now: number;
  onSearchChange: (value: string) => void;
  onSelect: (sessionId: string) => void;
  onShowAll: () => void;
  onRetry: () => Promise<unknown>;
}) {
  const searching = (search.trim() !== "" || query !== "") && (!isCurrent || search.trim() !== query);
  const noRows = rows.length === 0;

  return (
    <>
      <div className="space-y-1 border-b border-line p-4">
        <input
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          maxLength={200}
          aria-label="Search conversations"
          placeholder="Name, email or ticket number"
          className={cn(
            "h-11 w-full rounded-control border border-line-strong bg-surface px-3 text-ui text-fg",
            "placeholder:text-fg-faint",
            FOCUS_RING.surface,
          )}
        />
        <p aria-live="polite" className="text-caption text-fg-muted">
          {searching ? "Searching…" : null}
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <div className="p-4">
            <LoadError message="Couldn't load conversations." onRetry={onRetry} />
          </div>
        ) : null}
        {noRows && !error && !isCurrent && <RowsSkeleton />}
        {noRows && !error && isCurrent && <EmptyList scope={scope} query={query} onShowAll={onShowAll} />}
        {!noRows && (
          <ul className="divide-y divide-line">
            {rows.map((row) => (
              <InboxRowItem
                key={row.session_id}
                row={row}
                viewerId={viewerId}
                now={now}
                selected={selected !== null && row.session_ids.includes(selected)}
                onSelect={() => onSelect(row.session_id)}
              />
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
