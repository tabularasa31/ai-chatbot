"use client";

import { Fragment, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Loader2, Pencil, RefreshCw, RotateCw, Trash2 } from "lucide-react";
import type { DocumentListItem, UrlSource } from "@/lib/api";
import { formatDateTimeLocale } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { FileStatus, UrlStatus } from "./HealthPanel";
import { DETAIL_ID_PREFIX } from "./UrlSourcesSection";
import { isIndexing, scheduleCaption } from "./shared";

const NAME_BUTTON_CLASS =
  "inline-flex min-h-11 items-center gap-1 break-words rounded-control text-left text-ui font-medium text-fg hover:underline " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";
const CELL_CLASS = "px-4 py-3 align-top";
const NUMERIC_CELL_CLASS = `${CELL_CLASS} text-right text-ui tabular-nums text-fg`;

function IconAction({
  label,
  icon,
  busy = false,
  disabled = false,
  onClick,
}: {
  label: string;
  icon: ReactNode;
  busy?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      title={label}
      disabled={disabled}
      aria-busy={busy || undefined}
      onClick={() => !busy && onClick()}
    >
      {busy ? <Loader2 size={20} aria-hidden="true" className="animate-spin" /> : icon}
    </Button>
  );
}

function Actions({ children }: { children: ReactNode }) {
  return (
    <td className="px-2 py-2 align-top">
      <div className="flex justify-end">{children}</div>
    </td>
  );
}

function displayUrl(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

export function FileRow({
  doc,
  rechecking,
  onPreview,
  onRecheck,
  onRequestDelete,
}: {
  doc: DocumentListItem;
  rechecking: boolean;
  onPreview: () => void;
  onRecheck: () => void;
  onRequestDelete: () => void;
}) {
  const indexing = isIndexing(doc.status);
  return (
    <tr className="border-b border-line hover:bg-surface-alt">
      <td className={CELL_CLASS}>
        <button type="button" onClick={onPreview} className={NAME_BUTTON_CLASS} title="View extracted text">
          {doc.filename}
        </button>
        <p className="text-caption text-fg-muted">File</p>
      </td>
      <td className={CELL_CLASS}>
        <FileStatus doc={doc} />
      </td>
      <td className={`${CELL_CLASS} hidden xl:table-cell`} />
      <td className={NUMERIC_CELL_CLASS}>{indexing ? "" : formatDateTimeLocale(doc.updated_at || doc.created_at)}</td>
      <Actions>
        <IconAction label="Re-check health" icon={<RotateCw size={20} aria-hidden="true" />} busy={rechecking} disabled={indexing} onClick={onRecheck} />
        <IconAction label="Delete file" icon={<Trash2 size={20} aria-hidden="true" />} onClick={onRequestDelete} />
      </Actions>
    </tr>
  );
}

export function UrlRow({
  source,
  expanded,
  refreshing,
  onToggle,
  onEdit,
  onRefresh,
  onRequestDelete,
  detail,
}: {
  source: UrlSource;
  expanded: boolean;
  refreshing: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onRefresh: () => void;
  onRequestDelete: () => void;
  detail: ReactNode;
}) {
  const pages = source.pages_found ? `${source.pages_indexed} / ${source.pages_found}` : `${source.pages_indexed}`;
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return (
    <Fragment>
      <tr className="cursor-pointer border-b border-line hover:bg-surface-alt" onClick={onToggle}>
        <td className={CELL_CLASS}>
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={expanded ? `${DETAIL_ID_PREFIX}${source.id}` : undefined}
            onClick={(event) => {
              event.stopPropagation();
              onToggle();
            }}
            className={NAME_BUTTON_CLASS}
          >
            <Chevron size={16} aria-hidden="true" className="shrink-0" />
            {source.name}
          </button>
          <p className="truncate text-caption text-fg-muted" title={source.url}>
            {displayUrl(source.url)}
          </p>
          <p className="text-caption text-fg-muted">{scheduleCaption(source)}</p>
        </td>
        <td className={CELL_CLASS}>
          <UrlStatus source={source} />
        </td>
        <td className={`${NUMERIC_CELL_CLASS} hidden xl:table-cell`}>{pages}</td>
        <td className={NUMERIC_CELL_CLASS}>
          {formatDateTimeLocale(source.updated_at)}
          <p className="text-caption text-fg-muted xl:hidden">{`${pages} pages`}</p>
        </td>
        <td className="px-2 py-2 align-top" onClick={(event) => event.stopPropagation()}>
          <div className="flex justify-end">
            <IconAction label="Edit source" icon={<Pencil size={20} aria-hidden="true" />} onClick={onEdit} />
            <IconAction label="Refresh now" icon={<RefreshCw size={20} aria-hidden="true" />} busy={refreshing} onClick={onRefresh} />
            <IconAction label="Delete source" icon={<Trash2 size={20} aria-hidden="true" />} onClick={onRequestDelete} />
          </div>
        </td>
      </tr>
      {expanded && detail}
    </Fragment>
  );
}
