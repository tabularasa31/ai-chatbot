"use client";

import { useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import type { UrlSource } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/field";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { DocumentRowsSkeleton } from "@/components/ui/skeleton";
import { DocumentPreviewDrawer } from "./DocumentPreviewDrawer";
import { FlashMessage } from "@/components/ui/flash";
import { SourceForm } from "./SourceForm";
import { FileRow, UrlRow } from "./SourceRows";
import { UrlSourcesSection, type DetailView } from "./UrlSourcesSection";
import { useSources, type Sources } from "./useSources";
import type { MixedRow } from "./shared";

const ACCEPTED_FILE_TYPES = ".pdf,.md,.mdx,.json,.yaml,.yml,.docx,.doc,.txt,.html,.htm";
const ADD_URL_FORM_ID = "add-url-form";
const SKELETON_ROWS = 5;
const SOURCES_TABLE_ID = "sources-table";

function sourcesTable() {
  return document.getElementById(SOURCES_TABLE_ID);
}

type PendingDelete =
  | { kind: "file"; id: string; name: string }
  | { kind: "source"; id: string; name: string; pages: number }
  | { kind: "page"; sourceId: string; pageId: string };

function sourceDeleteBody(pages: number): string {
  if (pages === 0) return "The source is removed. No pages are indexed yet.";
  if (pages === 1) return "The 1 indexed page is removed and the bot stops using it.";
  return `All ${pages} indexed pages are removed and the bot stops using them.`;
}

function deleteDialogProps(pending: PendingDelete, sources: Sources) {
  switch (pending.kind) {
    case "file":
      return {
        title: `Delete "${pending.name}"?`,
        body: "The bot stops using this file. This can't be undone.",
        confirmLabel: "Delete file",
        failureMessage: "Couldn't delete this file. Try again.",
        onConfirm: () => sources.deleteFile(pending.id),
      };
    case "source":
      return {
        title: `Delete "${pending.name}"?`,
        body: sourceDeleteBody(pending.pages),
        confirmLabel: "Delete source",
        failureMessage: "Couldn't delete this source. Try again.",
        onConfirm: () => sources.deleteSource(pending.id),
      };
    case "page":
      return {
        title: "Delete this page?",
        body: "It is removed from Knowledge and skipped in future refreshes.",
        confirmLabel: "Delete page",
        failureMessage: "Couldn't delete this page. Try again.",
        onConfirm: () => sources.deleteSourcePage(pending.sourceId, pending.pageId),
      };
  }
}

function detailView(sources: Sources, source: UrlSource): DetailView {
  const onRetry = () => sources.loadDetail(source.id);
  if (sources.detail?.id === source.id) {
    return { state: "ready", detail: sources.detail, onRetryRefresh: sources.detailState === "error" ? onRetry : null };
  }
  if (sources.detailState === "error") return { state: "error", onRetry };
  return { state: "loading" };
}

export function DocumentsSection() {
  const sources = useSources();
  const fileInput = useRef<HTMLInputElement>(null);
  const [filter, setFilter] = useState("");
  const [showUrlForm, setShowUrlForm] = useState(false);
  const [previewDocumentId, setPreviewDocumentId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);

  const rows = useMemo(() => {
    const text = filter.trim().toLowerCase();
    const mixed: MixedRow[] = [
      ...sources.documents.map((item) => ({ kind: "file" as const, item })),
      ...sources.sources.map((item) => ({ kind: "url" as const, item })),
    ];
    if (!text) return mixed;
    return mixed.filter((row) =>
      row.kind === "file"
        ? row.item.filename.toLowerCase().includes(text)
        : row.item.name.toLowerCase().includes(text) || row.item.url.toLowerCase().includes(text),
    );
  }, [sources.documents, sources.sources, filter]);

  const uploading = sources.uploadProgress !== null;

  async function handleFilesChosen(event: ChangeEvent<HTMLInputElement>) {
    const input = event.target;
    const files = Array.from(input.files ?? []);
    input.value = "";
    if (files.length > 0) await sources.uploadFiles(files);
  }

  function renderEmpty() {
    if (sources.loadState === "error") return null;
    if (filter.trim()) {
      return (
        <div className="space-y-3">
          <p className="text-ui text-fg-muted">{`No sources match "${filter.trim()}".`}</p>
          <Button variant="secondary" onClick={() => setFilter("")}>
            Clear filter
          </Button>
        </div>
      );
    }
    return (
      <p className="max-w-prose text-ui text-fg-muted">
        No sources yet. Upload files or add a docs website so the bot has something to answer from.
      </p>
    );
  }

  const dialog = pendingDelete && deleteDialogProps(pendingDelete, sources);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <Input
          label="Filter sources"
          hideLabel
          type="search"
          placeholder="Filter sources"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-72 max-w-full"
        />
        <div className="flex flex-wrap items-center gap-3">
          <p role="status" className="w-40 text-right text-ui tabular-nums text-fg-muted empty:hidden sm:empty:block">
            {sources.uploadProgress && `Uploading ${sources.uploadProgress.current} of ${sources.uploadProgress.total}`}
          </p>
          <FlashMessage message={sources.uploadedMessage} />
          <Button
            variant="secondary"
            aria-expanded={showUrlForm}
            aria-controls={ADD_URL_FORM_ID}
            onClick={() => setShowUrlForm((open) => !open)}
          >
            Add from URL
          </Button>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept={ACCEPTED_FILE_TYPES}
            aria-label="Choose files to upload"
            onChange={(event) => void handleFilesChosen(event)}
          />
          <Button variant="primary" busy={uploading} onClick={() => fileInput.current?.click()}>
            Upload files
          </Button>
        </div>
      </div>

      {showUrlForm && (
        <section id={ADD_URL_FORM_ID} aria-label="Add from URL" className="border-y border-line py-6">
          <SourceForm
            initial={{ name: "", schedule: "weekly", exclusions: "" }}
            withUrl
            submitLabel="Add and start indexing"
            failureFallback="Couldn't add this source. Try again."
            onSubmit={async (values) => {
              await sources.createUrlSource(values);
              setShowUrlForm(false);
            }}
            onCancel={() => setShowUrlForm(false)}
          />
        </section>
      )}

      {sources.loadState === "error" && (
        <LoadError message="Couldn't load your sources." onRetry={sources.load} />
      )}
      {sources.actionError && (
        <InlineNotice tone="danger" onDismiss={sources.clearActionError}>
          {sources.actionError}
        </InlineNotice>
      )}
      {sources.uploadErrors.length > 0 && (
        <InlineNotice tone="danger" onDismiss={sources.clearUploadErrors}>
          <ul>
            {sources.uploadErrors.map(({ filename, message }) => (
              <li key={filename} className="break-words">{`${filename}: ${message}`}</li>
            ))}
          </ul>
        </InlineNotice>
      )}

      <table id={SOURCES_TABLE_ID} tabIndex={-1} className="w-full table-fixed border-collapse focus:outline-none" aria-busy={sources.loadState === "loading"}>
        <thead>
          <tr className="border-b border-line text-left text-caption font-medium text-fg-muted">
            <th scope="col" className="px-4 py-3 font-medium">Source</th>
            <th scope="col" className="w-48 px-4 py-3 font-medium">Status</th>
            <th scope="col" className="hidden w-24 px-4 py-3 text-right font-medium xl:table-cell">Pages</th>
            <th scope="col" className="w-36 px-4 py-3 text-right font-medium">Updated</th>
            <th scope="col" className="w-40 px-2 py-3">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && sources.loadState === "loading" ? (
            <DocumentRowsSkeleton rows={SKELETON_ROWS} />
          ) : rows.length === 0 ? (
            <tr>
              <td colSpan={5} className="px-4 py-6">
                {renderEmpty()}
              </td>
            </tr>
          ) : (
            rows.map((row) =>
              row.kind === "file" ? (
                <FileRow
                  key={row.item.id}
                  doc={row.item}
                  rechecking={sources.recheckingId === row.item.id}
                  onPreview={() => setPreviewDocumentId(row.item.id)}
                  onRecheck={() => void sources.recheckHealth(row.item.id)}
                  onRequestDelete={() => setPendingDelete({ kind: "file", id: row.item.id, name: row.item.filename })}
                />
              ) : (
                <UrlRow
                  key={row.item.id}
                  source={row.item}
                  expanded={sources.expandedSourceId === row.item.id}
                  refreshing={sources.refreshingSourceId === row.item.id}
                  onToggle={() => void sources.toggleDetail(row.item.id)}
                  onEdit={() => void sources.startEdit(row.item.id)}
                  onRefresh={() => void sources.refreshSource(row.item.id)}
                  onRequestDelete={() =>
                    setPendingDelete({ kind: "source", id: row.item.id, name: row.item.name, pages: row.item.pages_indexed })
                  }
                  detail={
                    <UrlSourcesSection
                      source={row.item}
                      view={detailView(sources, row.item)}
                      isEditing={sources.isEditing}
                      onSaveEdit={(values) => sources.saveEdit(row.item.id, values)}
                      onCancelEdit={sources.stopEditing}
                      onRequestDeletePage={(pageId) =>
                        setPendingDelete({ kind: "page", sourceId: row.item.id, pageId })
                      }
                      onPreviewPage={setPreviewDocumentId}
                    />
                  }
                />
              ),
            )
          )}
        </tbody>
      </table>

      <DocumentPreviewDrawer documentId={previewDocumentId} onClose={() => setPreviewDocumentId(null)} />
      {dialog && (
        <ConfirmDialog
          {...dialog}
          tone="destructive"
          fallbackFocus={sourcesTable}
          onClose={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}
