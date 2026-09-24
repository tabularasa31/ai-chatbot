"use client";

import type { UrlSource, UrlSourceDetail } from "@/lib/api";
import { StatusBadge, quickAnswerLabel } from "./shared";

function formatFailedUrlPreview(failedUrls: Array<{ url: string; reason: string }>): string {
  const preview = failedUrls
    .slice(0, 2)
    .map((item) => `${item.url} (${item.reason})`)
    .join(", ");
  if (failedUrls.length <= 2) return preview;
  return `${preview} +${failedUrls.length - 2} more`;
}

export interface UrlSourcesSectionProps {
  source: UrlSource;
  detail: UrlSourceDetail | null;
  detailLoading: boolean;
  isEditing: boolean;
  setIsEditing: (v: boolean) => void;
  editName: string;
  setEditName: (v: string) => void;
  editSchedule: string;
  setEditSchedule: (v: string) => void;
  editExclusions: string;
  setEditExclusions: (v: string) => void;
  isSaving: boolean;
  onSaveEdit: () => Promise<void>;
  onDeletePage: (sourceId: string, docId: string) => Promise<void>;
  deletingPageId: string | null;
  onPreviewPage: (docId: string) => void;
}

export function UrlSourcesSection({
  source,
  detail,
  detailLoading,
  isEditing,
  setIsEditing,
  editName,
  setEditName,
  editSchedule,
  setEditSchedule,
  editExclusions,
  setEditExclusions,
  isSaving,
  onSaveEdit,
  onDeletePage,
  deletingPageId,
  onPreviewPage,
}: UrlSourcesSectionProps) {
  return (
    <tr className="bg-slate-50/60">
      <td colSpan={7} className="px-5 py-4">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          {detailLoading && !detail ? (
            <div className="text-sm text-slate-500">Loading source details…</div>
          ) : (
            <div className="space-y-5">
              {isEditing && (
                <div className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
                  <label className="block">
                    <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-400">Name</span>
                    <input
                      type="text"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      placeholder="Display name"
                      className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-slate-400"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-400">Schedule</span>
                    <select
                      value={editSchedule}
                      onChange={(e) => setEditSchedule(e.target.value)}
                      className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-slate-400"
                    >
                      <option value="daily">Daily</option>
                      <option value="weekly">Weekly</option>
                      <option value="manual">Manual only</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-400">Exclusions</span>
                    <textarea
                      value={editExclusions}
                      onChange={(e) => setEditExclusions(e.target.value)}
                      placeholder={"/blog/*\n/changelog/*"}
                      rows={4}
                      className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-slate-400"
                    />
                  </label>
                  <div className="flex items-center gap-2 pt-1">
                    <button
                      type="button"
                      onClick={() => void onSaveEdit()}
                      disabled={isSaving}
                      className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {isSaving ? "Saving…" : "Save"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsEditing(false)}
                      disabled={isSaving}
                      className="rounded-lg px-3 py-2 text-sm text-slate-500 hover:bg-slate-100 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {(detail?.warning_message || detail?.error_message) && (
                <div className="space-y-2">
                  {detail.warning_message && (
                    <div className="rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      {detail.warning_message}
                    </div>
                  )}
                  {detail.error_message && (
                    <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
                      {detail.error_message}
                    </div>
                  )}
                </div>
              )}

              <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
                <div>
                  <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Recent runs</div>
                  <div className="space-y-2">
                    {!detail || detail.recent_runs.length === 0 ? (
                      <div className="text-sm text-slate-500">No crawl runs yet.</div>
                    ) : (
                      detail.recent_runs.map((run) => (
                        <div key={run.id} className="rounded-lg border border-slate-200 p-3 text-sm">
                          <div className="flex items-center justify-between gap-3">
                            <StatusBadge status={run.status} />
                            <span className="text-xs text-slate-400">{new Date(run.created_at).toLocaleString()}</span>
                          </div>
                          <div className="mt-2 text-slate-600">
                            {run.pages_indexed}
                            {run.pages_found ? ` / ${run.pages_found}` : ""} pages indexed
                          </div>
                          {run.failed_urls.length > 0 && (
                            <div className="mt-2 text-xs text-slate-500">
                              Failed: {formatFailedUrlPreview(run.failed_urls)}
                            </div>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                </div>

                <div>
                  <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Exclusions</div>
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
                    {detail && detail.exclusion_patterns.length
                      ? detail.exclusion_patterns.join(", ")
                      : "No exclusions"}
                  </div>
                </div>
              </div>

              <div>
                <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Quick answers</div>
                {!detail || !detail.quick_answers || detail.quick_answers.length === 0 ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-500">
                    No structured quick answers detected yet.
                  </div>
                ) : (
                  <div className="grid gap-3 xl:grid-cols-2">
                    {detail.quick_answers.map((item) => (
                      <div key={item.key} className="rounded-lg border border-slate-200 p-3">
                        <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
                          {quickAnswerLabel(item.key)}
                        </div>
                        <div className="mt-1 break-all text-sm text-slate-700">{item.value}</div>
                        <div className="mt-2 text-xs text-slate-400">{item.source_url}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Indexed pages</div>
                {!detail || detail.pages.length === 0 ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-500">
                    Pages will appear here after indexing starts.
                  </div>
                ) : (
                  <div className="grid gap-3 xl:grid-cols-2">
                    {detail.pages.map((page) => (
                      <div key={page.id} className="rounded-lg border border-slate-200 p-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <button
                              type="button"
                              onClick={() => onPreviewPage(page.id)}
                              className="block w-full truncate text-left text-sm font-medium text-slate-700 hover:text-violet-700 hover:underline"
                              title="View extracted text"
                            >
                              {page.title}
                            </button>
                            <div className="mt-1 break-all text-xs text-slate-400">{page.url}</div>
                          </div>
                          <button
                            type="button"
                            onClick={() => void onDeletePage(source.id, page.id)}
                            disabled={deletingPageId === page.id}
                            className="shrink-0 text-xs text-red-400 hover:text-red-600 disabled:opacity-40"
                          >
                            {deletingPageId === page.id ? "Deleting…" : "Delete"}
                          </button>
                        </div>
                        <div className="mt-2 text-xs text-slate-500">{page.chunk_count} chunks</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}
