"use client";

import { useEffect, useState } from "react";
import { api, type DocumentHealthStatus } from "@/lib/api";

type Document = {
  id: string;
  filename: string;
  file_type: string;
  status: string;
  created_at: string;
  updated_at?: string;
  health_status?: DocumentHealthStatus | null;
};

type ExternalSource = {
  id: string;
  name: string;
  type: "git" | "url";
  label: string;
  meta: string;
  chunks: number;
  indexedAt: string;
  health: "good";
};

const COMING_SOON_INTEGRATIONS = [
  { key: "confluence", name: "Confluence", icon: "📘" },
  { key: "notion", name: "Notion", icon: "◻" },
  { key: "url", name: "URL Crawler", icon: "🔗" },
];

function TypeBadge({ type }: { type: string }) {
  const styles: Record<string, string> = {
    file: "bg-indigo-400/10 text-indigo-400",
    git:  "bg-emerald-400/10 text-emerald-400",
    url:  "bg-amber-400/10 text-amber-400",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-medium ${styles[type] ?? "bg-slate-100 text-slate-600"}`}>
      {type}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    processing: "bg-yellow-100 text-yellow-800",
    embedding:  "bg-blue-100 text-blue-800",
    ready:      "bg-green-100 text-green-800",
    error:      "bg-red-100 text-red-800",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${styles[status] ?? "bg-slate-100 text-slate-800"}`}>
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

function HealthCell({ health, isEmbedding }: { health: DocumentHealthStatus | null | undefined; isEmbedding?: boolean }) {
  if (isEmbedding) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-400">
        <span className="w-2 h-2 rounded-full bg-slate-300 animate-pulse" />
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

  return (
    <div className="relative group inline-flex flex-col items-start gap-1">
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-600 cursor-default">
        <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass}`} />
        <span className="font-medium">{label}</span>
      </span>
      {warnings.length > 0 && (
        <div className="hidden group-hover:block absolute z-20 left-0 top-full mt-1 min-w-[240px] max-w-sm p-3 rounded-md border border-slate-200 bg-white shadow-lg text-left">
          <p className="text-xs font-semibold text-slate-700 mb-2">Warnings</p>
          <ul className="text-xs text-slate-600 space-y-2">
            {warnings.map((w, i) => (
              <li key={i}>
                <span className="text-slate-400 uppercase tracking-wide">{w.severity}</span>
                {": "}
                {w.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [recheckingId, setRecheckingId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const externalSources: ExternalSource[] = [];

  async function load() {
    try {
      const docs = await api.documents.list();
      setDocuments(docs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function pollUntilEmbedded(docId: string, timeoutMs = 120_000) {
    const interval = 2_000;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, interval));
      const updated = await api.documents.getById(docId);
      setDocuments((prev) =>
        prev.map((d) => (d.id === docId ? { ...d, status: updated.status } : d))
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
      setDocuments((prev) => [doc, ...prev]);
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

  async function handleDelete(id: string) {
    if (!confirm("Delete this source?")) return;
    try {
      await api.documents.delete(id);
      setDocuments((prev) => prev.filter((d) => d.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleRecheckHealth(docId: string) {
    setRecheckingId(docId);
    try {
      const hs = await api.documents.runHealth(docId);
      setDocuments((prev) =>
        prev.map((d) => (d.id === docId ? { ...d, health_status: hs } : d))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Health re-check failed");
    } finally {
      setRecheckingId(null);
    }
  }

  const filteredDocs = documents.filter((d) =>
    d.filename.toLowerCase().includes(filter.toLowerCase())
  );

  const allRows = [
    ...filteredDocs.map((d) => ({ kind: "file" as const, doc: d })),
    ...externalSources
      .filter((s) => s.name.toLowerCase().includes(filter.toLowerCase()))
      .map((s) => ({ kind: "external" as const, source: s })),
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Knowledge</h1>
        <p className="text-sm text-slate-500 mt-1">Everything your bot knows</p>
      </div>

      {/* External source cards */}
      <div>
        <p className="text-[11px] uppercase tracking-widest text-slate-400 mb-3">External sources</p>
        <div className="flex gap-3 flex-wrap">
          {/* GitHub — connected placeholder (wired when real integration exists) */}
          <div className="bg-white border border-slate-200 rounded-xl p-4 w-44 opacity-40 cursor-not-allowed select-none">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-base">⬡</span>
              <span className="text-sm font-semibold text-slate-700">GitHub</span>
            </div>
            <p className="text-xs text-slate-400">Coming soon</p>
          </div>
          {COMING_SOON_INTEGRATIONS.map((itg) => (
            <div
              key={itg.key}
              className="bg-white border border-slate-200 rounded-xl p-4 w-44 opacity-40 cursor-not-allowed select-none"
            >
              <div className="flex items-center gap-2 mb-2">
                <span className="text-base">{itg.icon}</span>
                <span className="text-sm font-semibold text-slate-700">{itg.name}</span>
              </div>
              <p className="text-xs text-slate-400">Coming soon</p>
            </div>
          ))}
        </div>
      </div>

      {/* Table controls */}
      <div className="flex items-center justify-between gap-4">
        <label className="inline-flex items-center gap-2">
          <span className="inline-flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg cursor-pointer transition-colors">
            {uploading ? (
              <span className="animate-pulse">Processing…</span>
            ) : (
              <>
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                  <path d="M6.5 1v8M3 4.5L6.5 1 10 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M1 10.5v1a.5.5 0 00.5.5h10a.5.5 0 00.5-.5v-1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                Upload file
              </>
            )}
          </span>
          <input
            type="file"
            accept=".pdf,.md,.json,.yaml,.yml"
            onChange={handleUpload}
            disabled={uploading}
            className="sr-only"
          />
        </label>
        <input
          type="text"
          placeholder="Filter sources…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-slate-400 text-slate-700 placeholder:text-slate-400 w-52"
        />
      </div>

      {uploadError && (
        <div className="text-red-600 text-sm bg-red-50 px-3 py-2 rounded-lg border border-red-100">
          {uploadError}
        </div>
      )}
      {error && (
        <div className="text-red-700 text-sm bg-red-50 px-4 py-3 rounded-lg">{error}</div>
      )}

      {/* Unified table */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50">
              <th className="text-left px-5 py-3 text-[11px] uppercase tracking-wider text-slate-400 font-medium">Name</th>
              <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-slate-400 font-medium">Type</th>
              <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-slate-400 font-medium">Status</th>
              <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-slate-400 font-medium">Indexed</th>
              <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-slate-400 font-medium">Health</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {allRows.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center text-slate-500 text-sm py-12">
                  {filter ? "No sources match your filter." : "No sources yet. Upload a file above."}
                </td>
              </tr>
            ) : (
              allRows.map((row) => {
                if (row.kind === "file") {
                  const doc = row.doc;
                  const isEmbedding = doc.status === "processing" || doc.status === "embedding";
                  return (
                    <tr key={doc.id} className="hover:bg-slate-50/60 transition-colors">
                      <td className="px-5 py-3.5 text-sm font-medium text-slate-800 max-w-[220px] truncate">
                        {doc.filename}
                      </td>
                      <td className="px-4 py-3.5">
                        <TypeBadge type="file" />
                      </td>
                      <td className="px-4 py-3.5">
                        <StatusBadge status={doc.status} />
                      </td>
                      <td className="px-4 py-3.5 text-xs text-slate-500">
                        {isEmbedding
                          ? <span className="text-slate-400 font-mono text-[11px] animate-pulse">embedding…</span>
                          : new Date(doc.created_at).toLocaleDateString()
                        }
                      </td>
                      <td className="px-4 py-3.5">
                        <HealthCell health={doc.health_status} isEmbedding={isEmbedding} />
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-2 justify-end">
                          {!isEmbedding && (
                            <button
                              type="button"
                              onClick={() => handleRecheckHealth(doc.id)}
                              disabled={recheckingId === doc.id}
                              className="text-xs text-slate-400 hover:text-slate-600 disabled:opacity-40"
                            >
                              {recheckingId === doc.id ? "…" : "Re-check"}
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => handleDelete(doc.id)}
                            disabled={isEmbedding}
                            className="text-xs text-red-400 hover:text-red-600 disabled:opacity-30"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                }

                const src = row.source;
                return (
                  <tr key={src.id} className="hover:bg-slate-50/60 transition-colors">
                    <td className="px-5 py-3.5 text-sm font-medium text-slate-800">{src.label}</td>
                    <td className="px-4 py-3.5"><TypeBadge type={src.type} /></td>
                    <td className="px-4 py-3.5"><span className="text-xs text-slate-400">—</span></td>
                    <td className="px-4 py-3.5 text-xs text-slate-500">{src.indexedAt}</td>
                    <td className="px-4 py-3.5">
                      <span className="inline-flex items-center gap-1.5 text-xs text-slate-600">
                        <span className="w-2 h-2 rounded-full bg-emerald-500" />
                        Good
                      </span>
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-2 justify-end">
                        <button type="button" className="text-xs text-indigo-400 hover:text-indigo-600">
                          Sync
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
    </div>
  );
}
