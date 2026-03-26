"use client";

import { useEffect, useState } from "react";
import {
  api,
  type DocumentHealthStatus,
  type DocumentListItem,
  type UrlSource,
  type UrlSourceDetail,
} from "@/lib/api";

function TypeBadge({ type }: { type: string }) {
  const styles: Record<string, string> = {
    file: "bg-indigo-400/10 text-indigo-500",
    url: "bg-amber-400/15 text-amber-600",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-[10px] font-mono font-medium ${styles[type] ?? "bg-slate-100 text-slate-600"}`}>
      {type}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    processing: "bg-yellow-100 text-yellow-800",
    embedding: "bg-blue-100 text-blue-800",
    ready: "bg-green-100 text-green-800",
    error: "bg-red-100 text-red-800",
    queued: "bg-amber-100 text-amber-800",
    indexing: "bg-sky-100 text-sky-800",
    paused: "bg-orange-100 text-orange-800",
    stale: "bg-yellow-100 text-yellow-800",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${styles[status] ?? "bg-slate-100 text-slate-800"}`}>
      {status}
    </span>
  );
}

function healthLabel(health: DocumentHealthStatus | null | undefined): string {
  if (health == null) return "Checking…";
  if (health.error || health.score === null) return "Unavailable";
  if (health.score >= 80) return "Good";
  if (health.score >= 50) return "Fair";
  return "Needs attention";
}

function HealthCell({
  health,
  isEmbedding,
}: {
  health: DocumentHealthStatus | null | undefined;
  isEmbedding?: boolean;
}) {
  if (isEmbedding) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-400">
        <span className="h-2 w-2 animate-pulse rounded-full bg-slate-300" />
        Pending
      </span>
    );
  }

  let dotClass = "bg-slate-300";
  const label = healthLabel(health);
  if (health != null && !health.error && health.score !== null) {
    if (health.score >= 80) dotClass = "bg-emerald-500";
    else if (health.score >= 50) dotClass = "bg-amber-400";
    else dotClass = "bg-red-500";
  }

  const warnings = health?.warnings ?? [];
  const checkedAt = health?.checked_at ? new Date(health.checked_at).toLocaleString() : null;
  const tooltipLines =
    health == null
      ? ["Health check is still running."]
      : health.error
        ? ["Health check is currently unavailable."]
        : warnings.length > 0
          ? warnings.map((warning) => warning.message)
          : ["No issues found."];
  const tooltipText = [...tooltipLines, checkedAt ? `Checked: ${checkedAt}` : null].filter(Boolean).join("\n");

  return (
    <span
      className="group relative inline-flex items-center gap-1.5 text-xs text-slate-600"
      tabIndex={0}
      title={tooltipText}
    >
      <span className={`h-2 w-2 rounded-full ${dotClass}`} />
      <span className="font-medium">{label}</span>
      <span className="pointer-events-none absolute right-0 top-full z-50 mt-2 hidden w-72 rounded-lg bg-slate-900 px-3 py-2 text-left text-[11px] leading-5 text-white shadow-xl group-hover:block group-focus-visible:block">
        {tooltipLines.map((line) => (
          <span key={line} className="block">
            {line}
          </span>
        ))}
        {checkedAt && <span className="mt-2 block text-[10px] text-slate-300">Checked: {checkedAt}</span>}
      </span>
    </span>
  );
}

type MixedRow =
  | { kind: "file"; item: DocumentListItem }
  | { kind: "url"; item: UrlSource };

const POLLABLE_SOURCE_STATUSES = new Set(["queued", "indexing"]);

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [sources, setSources] = useState<UrlSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [submittingUrl, setSubmittingUrl] = useState(false);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [recheckingId, setRecheckingId] = useState<string | null>(null);
  const [refreshingSourceId, setRefreshingSourceId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [detail, setDetail] = useState<UrlSourceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showUrlForm, setShowUrlForm] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [nameInput, setNameInput] = useState("");
  const [scheduleInput, setScheduleInput] = useState("weekly");
  const [exclusionsInput, setExclusionsInput] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editSchedule, setEditSchedule] = useState("");
  const [editExclusions, setEditExclusions] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  async function load() {
    try {
      const data = await api.documents.listSources();
      setDocuments(data.documents);
      setSources(data.url_sources);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const shouldPoll = sources.some((source) => POLLABLE_SOURCE_STATUSES.has(source.status));
    if (!shouldPoll) return;
    const timer = window.setInterval(() => {
      void load();
      if (detail && POLLABLE_SOURCE_STATUSES.has(detail.status) && !isEditing) {
        void openDetail(detail.id);
      }
    }, 10000);
    return () => window.clearInterval(timer);
  }, [sources, detail, isEditing]);

  async function openDetail(sourceId: string) {
    setIsEditing(false);
    try {
      setDetailLoading(true);
      const next = await api.documents.getSourceById(sourceId);
      setDetail(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load source details");
    } finally {
      setDetailLoading(false);
    }
  }

  async function pollUntilEmbedded(docId: string, timeoutMs = 120_000) {
    const interval = 2_000;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, interval));
      const updated = await api.documents.getById(docId);
      setDocuments((prev) =>
        prev.map((d) => (d.id === docId ? { ...d, status: updated.status, health_status: updated.health_status } : d))
      );
      if (updated.status === "ready" || updated.status === "error") return updated.status;
    }
    return "timeout";
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError("");
    setUploading(true);
    try {
      const doc = await api.documents.upload(file);
      setDocuments((prev) => [doc as DocumentListItem, ...prev]);
      await api.embeddings.create(doc.id);
      await pollUntilEmbedded(doc.id);
      await load();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleCreateUrlSource() {
    setSubmittingUrl(true);
    setError("");
    try {
      const source = await api.documents.createUrlSource({
        url: urlInput,
        name: nameInput || undefined,
        schedule: scheduleInput,
        exclusions: exclusionsInput
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      setSources((prev) => [source, ...prev]);
      setShowUrlForm(false);
      setUrlInput("");
      setNameInput("");
      setExclusionsInput("");
      setScheduleInput("weekly");
      await openDetail(source.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create source");
    } finally {
      setSubmittingUrl(false);
    }
  }

  async function handleDeleteFile(id: string) {
    if (!confirm("Delete this file?")) return;
    try {
      await api.documents.delete(id);
      setDocuments((prev) => prev.filter((d) => d.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleDeleteSource(id: string) {
    if (!confirm("Delete this URL source and all indexed pages?")) return;
    try {
      await api.documents.deleteSource(id);
      setSources((prev) => prev.filter((source) => source.id !== id));
      if (detail?.id === id) setDetail(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleRefreshSource(id: string) {
    setRefreshingSourceId(id);
    try {
      const source = await api.documents.refreshSource(id);
      setSources((prev) => prev.map((item) => (item.id === id ? source : item)));
      if (detail?.id === id) await openDetail(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setRefreshingSourceId(null);
    }
  }

  function openEdit() {
    if (!detail) return;
    setEditName(detail.name ?? "");
    setEditSchedule(detail.schedule);
    setEditExclusions((detail.exclusion_patterns ?? []).join("\n"));
    setIsEditing(true);
  }

  async function handleSaveEdit() {
    if (!detail) return;
    setIsSaving(true);
    try {
      await api.documents.updateSource(detail.id, {
        name: editName.trim(),
        schedule: editSchedule,
        exclusions: editExclusions.split("\n").map((s) => s.trim()).filter(Boolean),
      });
      setIsEditing(false);
      await load();
      await openDetail(detail.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRecheckHealth(docId: string) {
    setRecheckingId(docId);
    try {
      const hs = await api.documents.runHealth(docId);
      setDocuments((prev) => prev.map((d) => (d.id === docId ? { ...d, health_status: hs } : d)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Health re-check failed");
    } finally {
      setRecheckingId(null);
    }
  }

  const rows = (() => {
    const text = filter.trim().toLowerCase();
    const mixed: MixedRow[] = [
      ...documents.map((item) => ({ kind: "file" as const, item })),
      ...sources.map((item) => ({ kind: "url" as const, item })),
    ];
    return mixed.filter((row) => {
      if (!text) return true;
      if (row.kind === "file") return row.item.filename.toLowerCase().includes(text);
      return (
        row.item.name.toLowerCase().includes(text) ||
        row.item.url.toLowerCase().includes(text)
      );
    });
  })();

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-sm text-slate-500">Loading…</div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-800">Knowledge</h1>
          <p className="mt-1 text-sm text-slate-500">Files and URL sources that power your bot.</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="inline-flex items-center gap-2">
            <span className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-700">
              {uploading ? "Processing…" : "Upload file"}
            </span>
            <input
              type="file"
              accept=".pdf,.md,.json,.yaml,.yml"
              onChange={handleUpload}
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
              onClick={() => void handleCreateUrlSource()}
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
        <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600">
          {uploadError}
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="overflow-x-auto overflow-y-visible rounded-xl border border-slate-200 bg-white">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50">
                <th className="px-5 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Name</th>
                <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Type</th>
                <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Status</th>
                <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Pages / Updated</th>
                <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-slate-400">Health / Warnings</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-sm text-slate-500">
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
                          {isEmbedding ? "embedding…" : new Date(doc.updated_at || doc.created_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-3.5"><HealthCell health={doc.health_status} isEmbedding={isEmbedding} /></td>
                        <td className="px-4 py-3.5">
                          <div className="flex items-center justify-end gap-2">
                            {!isEmbedding && (
                              <button
                                type="button"
                                onClick={() => void handleRecheckHealth(doc.id)}
                                disabled={recheckingId === doc.id}
                                className="text-xs text-slate-400 hover:text-slate-600 disabled:opacity-40"
                              >
                                {recheckingId === doc.id ? "…" : "Re-check"}
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => void handleDeleteFile(doc.id)}
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
                  const pageMeta = source.pages_found ? `${source.pages_indexed} / ${source.pages_found}` : `${source.pages_indexed}`;
                  const note = source.warning_message || source.error_message || "—";
                  return (
                    <tr key={source.id} className="transition-colors hover:bg-slate-50/60">
                      <td className="px-5 py-3.5">
                        <button
                          type="button"
                          onClick={() => void openDetail(source.id)}
                          className="max-w-[320px] truncate text-left text-sm font-medium text-slate-800 hover:text-violet-700"
                        >
                          {source.name}
                        </button>
                        <div className="mt-1 max-w-[320px] truncate text-xs text-slate-400">{source.url}</div>
                      </td>
                      <td className="px-4 py-3.5"><TypeBadge type="url" /></td>
                      <td className="px-4 py-3.5"><StatusBadge status={source.status} /></td>
                      <td className="px-4 py-3.5 text-xs text-slate-500">
                        <div>{pageMeta} pages</div>
                        <div className="mt-1">{new Date(source.updated_at).toLocaleString()}</div>
                      </td>
                      <td className="max-w-[220px] px-4 py-3.5 text-xs text-slate-500">{note}</td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => void handleRefreshSource(source.id)}
                            disabled={refreshingSourceId === source.id}
                            className="text-xs text-indigo-400 hover:text-indigo-600 disabled:opacity-40"
                          >
                            {refreshingSourceId === source.id ? "…" : "Refresh"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleDeleteSource(source.id)}
                            className="text-xs text-red-400 hover:text-red-600"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          {!detail ? (
            <div className="text-sm text-slate-500">Select a URL source to see crawl history, failed URLs, and indexed pages.</div>
          ) : detailLoading ? (
            <div className="text-sm text-slate-500">Loading source details…</div>
          ) : (
            <div className="space-y-5">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <h2 className="text-lg font-semibold text-slate-800">{detail.name}</h2>
                    <StatusBadge status={detail.status} />
                  </div>
                  {!isEditing && (
                    <button
                      type="button"
                      onClick={openEdit}
                      className="rounded-lg border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50"
                    >
                      Edit
                    </button>
                  )}
                </div>
                <p className="mt-1 break-all text-xs text-slate-500">{detail.url}</p>
              </div>

              {isEditing ? (
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
                      onClick={() => void handleSaveEdit()}
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
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-lg bg-slate-50 p-3">
                      <div className="text-xs uppercase tracking-wide text-slate-400">Schedule</div>
                      <div className="mt-1 font-medium text-slate-700">{detail.schedule}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-3">
                      <div className="text-xs uppercase tracking-wide text-slate-400">Indexed</div>
                      <div className="mt-1 font-medium text-slate-700">{detail.pages_indexed} pages</div>
                    </div>
                  </div>

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

                  <div>
                    <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Exclusions</div>
                    <div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
                      {detail.exclusion_patterns.length ? detail.exclusion_patterns.join(", ") : "No exclusions"}
                    </div>
                  </div>
                </>
              )}

              <div>
                <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Recent runs</div>
                <div className="space-y-2">
                  {detail.recent_runs.length === 0 ? (
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
                            Failed: {run.failed_urls.slice(0, 2).map((item) => item.url).join(", ")}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div>
                <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Indexed pages</div>
                <div className="space-y-2">
                  {detail.pages.length === 0 ? (
                    <div className="text-sm text-slate-500">Pages will appear here after indexing starts.</div>
                  ) : (
                    detail.pages.map((page) => (
                      <div key={page.id} className="rounded-lg border border-slate-200 p-3">
                        <div className="text-sm font-medium text-slate-700">{page.title}</div>
                        <div className="mt-1 break-all text-xs text-slate-400">{page.url}</div>
                        <div className="mt-2 text-xs text-slate-500">{page.chunk_count} chunks</div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
