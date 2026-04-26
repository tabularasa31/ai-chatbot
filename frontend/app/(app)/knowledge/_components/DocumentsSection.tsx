"use client";

import { Fragment } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { DocumentListItem, UrlSource, UrlSourceDetail } from "@/lib/api";
import {
  KnowledgeTabs,
  TypeBadge,
  StatusBadge,
  MixedRow,
  stopRowClick,
  formatSchedule,
} from "./shared";
import { HealthCell, SourceHealthCell } from "./HealthPanel";
import { UrlSourcesSection } from "./UrlSourcesSection";

export interface DocumentsSectionProps {
  activeTab: "documents" | "profile" | "faq";
  onTabChange: (tab: "documents" | "profile" | "faq") => void;

  documents: DocumentListItem[];
  sources: UrlSource[];
  filter: string;
  setFilter: Dispatch<SetStateAction<string>>;

  uploading: boolean;
  uploadProgress: { current: number; total: number } | null;
  submittingUrl: boolean;
  error: string;
  uploadError: string;
  recheckingId: string | null;
  refreshingSourceId: string | null;
  deletingPageId: string | null;

  expandedSourceId: string | null;
  detail: UrlSourceDetail | null;
  detailLoading: boolean;

  showUrlForm: boolean;
  setShowUrlForm: Dispatch<SetStateAction<boolean>>;
  urlInput: string;
  setUrlInput: Dispatch<SetStateAction<string>>;
  nameInput: string;
  setNameInput: Dispatch<SetStateAction<string>>;
  scheduleInput: string;
  setScheduleInput: Dispatch<SetStateAction<string>>;
  exclusionsInput: string;
  setExclusionsInput: Dispatch<SetStateAction<string>>;

  isEditing: boolean;
  setIsEditing: Dispatch<SetStateAction<boolean>>;
  editName: string;
  setEditName: Dispatch<SetStateAction<string>>;
  editSchedule: string;
  setEditSchedule: Dispatch<SetStateAction<string>>;
  editExclusions: string;
  setEditExclusions: Dispatch<SetStateAction<string>>;
  isSaving: boolean;

  onUpload: (e: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
  onCreateUrlSource: () => Promise<void>;
  onDeleteFile: (id: string) => Promise<void>;
  onDeleteSource: (id: string) => Promise<void>;
  onRefreshSource: (id: string) => Promise<void>;
  onDeleteSourcePage: (sourceId: string, docId: string) => Promise<void>;
  onOpenEdit: (source: UrlSource) => void;
  onSaveEdit: () => Promise<void>;
  onRecheckHealth: (docId: string) => Promise<void>;
  onToggleDetail: (sourceId: string) => Promise<void>;
}

export function DocumentsSection({
  activeTab,
  onTabChange,
  documents,
  sources,
  filter,
  setFilter,
  uploading,
  uploadProgress,
  submittingUrl,
  error,
  uploadError,
  recheckingId,
  refreshingSourceId,
  deletingPageId,
  expandedSourceId,
  detail,
  detailLoading,
  showUrlForm,
  setShowUrlForm,
  urlInput,
  setUrlInput,
  nameInput,
  setNameInput,
  scheduleInput,
  setScheduleInput,
  exclusionsInput,
  setExclusionsInput,
  isEditing,
  setIsEditing,
  editName,
  setEditName,
  editSchedule,
  setEditSchedule,
  editExclusions,
  setEditExclusions,
  isSaving,
  onUpload,
  onCreateUrlSource,
  onDeleteFile,
  onDeleteSource,
  onRefreshSource,
  onDeleteSourcePage,
  onOpenEdit,
  onSaveEdit,
  onRecheckHealth,
  onToggleDetail,
}: DocumentsSectionProps) {
  const rows = (() => {
    const text = filter.trim().toLowerCase();
    const mixed: MixedRow[] = [
      ...documents.map((item) => ({ kind: "file" as const, item })),
      ...sources.map((item) => ({ kind: "url" as const, item })),
    ];
    return mixed.filter((row) => {
      if (!text) return true;
      if (row.kind === "file") return row.item.filename.toLowerCase().includes(text);
      return row.item.name.toLowerCase().includes(text) || row.item.url.toLowerCase().includes(text);
    });
  })();

  return (
    <div className="max-w-7xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-800">Knowledge Hub</h1>
          <p className="mt-1 text-sm text-slate-500">Files and URL sources that power your bot.</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="inline-flex items-center gap-2">
            <span className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-700">
              {uploading
                ? uploadProgress
                  ? `Processing ${uploadProgress.current}/${uploadProgress.total}…`
                  : "Processing…"
                : "Upload files"}
            </span>
            <input
              type="file"
              multiple
              accept=".pdf,.md,.mdx,.json,.yaml,.yml,.docx,.doc,.txt"
              onChange={onUpload}
              disabled={uploading}
              className="sr-only"
            />
          </label>
          <button
            type="button"
            onClick={() => setShowUrlForm((prev) => !prev)}
            className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-slate-300 hover:bg-slate-50"
          >
            + Add from URL
          </button>
        </div>
      </div>
      <KnowledgeTabs activeTab={activeTab} onChange={onTabChange} />

      {showUrlForm && (
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">URL</span>
              <input
                type="url"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                placeholder="https://docs.yourproduct.com"
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-slate-400"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">Name</span>
              <input
                type="text"
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                placeholder="Optional display name"
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-slate-400"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">Schedule</span>
              <select
                value={scheduleInput}
                onChange={(e) => setScheduleInput(e.target.value)}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-slate-400"
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="manual">Manual only</option>
              </select>
            </label>
            <label className="block md:col-span-2">
              <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">Exclusions</span>
              <textarea
                value={exclusionsInput}
                onChange={(e) => setExclusionsInput(e.target.value)}
                placeholder={"/blog/*\n/changelog/*"}
                rows={4}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-slate-400"
              />
            </label>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              onClick={() => void onCreateUrlSource()}
              disabled={submittingUrl || !urlInput.trim()}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submittingUrl ? "Checking and starting…" : "Add and start indexing"}
            </button>
            <button
              type="button"
              onClick={() => setShowUrlForm(false)}
              className="rounded-lg px-3 py-2 text-sm text-slate-500 hover:bg-slate-100"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-4">
        <input
          type="text"
          placeholder="Filter sources…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-60 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-slate-400"
        />
      </div>

      {uploadError && (
        <div className="whitespace-pre-line rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600">
          {uploadError}
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="overflow-x-auto overflow-y-visible rounded-xl border border-slate-200 bg-white">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50">
              <th className="px-5 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Name</th>
              <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Type</th>
              <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Status</th>
              <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Indexed / Updated</th>
              <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Scheduled</th>
              <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Health / Warnings</th>
              <th className="px-4 py-3 text-right text-[11px] font-medium uppercase tracking-wider text-slate-400">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-12 text-center text-sm text-slate-500">
                  {filter ? "No sources match your filter." : "No sources yet. Upload a file or add a docs URL."}
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                if (row.kind === "file") {
                  const doc = row.item;
                  const isEmbedding = doc.status === "processing" || doc.status === "embedding";
                  return (
                    <tr key={doc.id} className="transition-colors hover:bg-slate-50/60">
                      <td className="max-w-[280px] px-5 py-3.5 text-sm font-medium text-slate-800">{doc.filename}</td>
                      <td className="px-4 py-3.5"><TypeBadge type="file" /></td>
                      <td className="px-4 py-3.5"><StatusBadge status={doc.status} /></td>
                      <td className="px-4 py-3.5 text-xs text-slate-500">
                        <div>—</div>
                        <div className="mt-1">{isEmbedding ? "embedding…" : new Date(doc.updated_at || doc.created_at).toLocaleString()}</div>
                      </td>
                      <td className="px-4 py-3.5 text-xs text-slate-400">—</td>
                      <td className="px-4 py-3.5"><HealthCell health={doc.health_status} isEmbedding={isEmbedding} /></td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center justify-end gap-2">
                          {!isEmbedding && (
                            <button
                              type="button"
                              onClick={() => void onRecheckHealth(doc.id)}
                              disabled={recheckingId === doc.id}
                              className="text-xs text-slate-400 hover:text-slate-600 disabled:opacity-40"
                            >
                              {recheckingId === doc.id ? "…" : "Re-check"}
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => void onDeleteFile(doc.id)}
                            className="text-xs text-red-400 hover:text-red-600"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                }

                const source = row.item;
                const isExpanded = expandedSourceId === source.id;
                const currentDetail = detail?.id === source.id ? detail : null;
                const pageMeta = source.pages_found ? `${source.pages_indexed} / ${source.pages_found}` : `${source.pages_indexed}`;

                return (
                  <Fragment key={source.id}>
                    <tr
                      className={`cursor-pointer transition-colors hover:bg-slate-50/60 ${isExpanded ? "bg-violet-50/40" : ""}`}
                      onClick={() => void onToggleDetail(source.id)}
                    >
                      <td className="px-5 py-3.5">
                        <div className="max-w-[320px] text-sm font-medium text-slate-800">{source.name}</div>
                        <div className="mt-1 max-w-[320px] truncate text-xs text-slate-400">{source.url}</div>
                      </td>
                      <td className="px-4 py-3.5"><TypeBadge type="url" /></td>
                      <td className="px-4 py-3.5"><StatusBadge status={source.status} /></td>
                      <td className="px-4 py-3.5 text-xs text-slate-500">
                        <div>{pageMeta} pages</div>
                        <div className="mt-1">{new Date(source.updated_at).toLocaleString()}</div>
                      </td>
                      <td className="px-4 py-3.5 text-xs text-slate-500">
                        <div>{formatSchedule(source.schedule)}</div>
                        <div className="mt-1 text-slate-400">
                          {source.next_crawl_at ? new Date(source.next_crawl_at).toLocaleString() : "No next run"}
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <SourceHealthCell status={source.status} warning={source.warning_message} error={source.error_message} />
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center justify-end gap-2" onClick={stopRowClick}>
                          <button
                            type="button"
                            onClick={() => onOpenEdit(source)}
                            className="text-xs text-slate-500 hover:text-slate-700"
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            onClick={() => void onRefreshSource(source.id)}
                            disabled={refreshingSourceId === source.id}
                            className="text-xs text-indigo-400 hover:text-indigo-600 disabled:opacity-40"
                          >
                            {refreshingSourceId === source.id ? "…" : "Refresh"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void onDeleteSource(source.id)}
                            className="text-xs text-red-400 hover:text-red-600"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                    {isExpanded && (
                      <UrlSourcesSection
                        source={source}
                        detail={currentDetail}
                        detailLoading={detailLoading}
                        isEditing={isEditing}
                        setIsEditing={setIsEditing}
                        editName={editName}
                        setEditName={setEditName}
                        editSchedule={editSchedule}
                        setEditSchedule={setEditSchedule}
                        editExclusions={editExclusions}
                        setEditExclusions={setEditExclusions}
                        isSaving={isSaving}
                        onSaveEdit={onSaveEdit}
                        onDeletePage={onDeleteSourcePage}
                        deletingPageId={deletingPageId}
                      />
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
