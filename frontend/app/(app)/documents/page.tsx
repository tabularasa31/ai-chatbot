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

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    processing: "bg-yellow-100 text-yellow-800",
    ready: "bg-green-100 text-green-800",
    error: "bg-red-100 text-red-800",
  };
  const style = styles[status] || "bg-slate-100 text-slate-800";
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${style}`}>
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

function HealthBadge({ health }: { health: DocumentHealthStatus | null | undefined }) {
  let dotClass = "bg-slate-300";
  let title = healthLabel(health);
  if (health == null) {
    title = "Checking…";
  } else if (health.error || health.score === null) {
    dotClass = "bg-slate-400";
    title = health.error ? `${title}: ${health.error}` : title;
  } else if (health.score >= 80) {
    dotClass = "bg-emerald-500";
  } else if (health.score >= 50) {
    dotClass = "bg-amber-400";
  } else {
    dotClass = "bg-red-500";
  }

  const warnings = health?.warnings ?? [];

  return (
    <div className="relative group inline-flex flex-col items-start gap-1">
      <span
        className="inline-flex items-center gap-1.5 text-xs text-slate-600 cursor-default"
        title={title}
      >
        <span className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${dotClass}`} aria-hidden />
        <span className="font-medium">{healthLabel(health)}</span>
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

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [recheckingId, setRecheckingId] = useState<string | null>(null);

  async function load() {
    try {
      const docs = await api.documents.list();
      setDocuments(docs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError("");
    setUploading(true);
    try {
      const doc = await api.documents.upload(file);
      setDocuments((prev) => [doc, ...prev]);
      await api.embeddings.create(doc.id);
      await load();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this document?")) return;
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

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-600">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-slate-800">Documents</h1>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h2 className="text-lg font-medium text-slate-800 mb-4">Upload document</h2>
        <p className="text-slate-600 text-sm mb-4">
          Allowed: .pdf, .md, .json, .yaml, .yml (max 50MB)
        </p>
        <label className="inline-block">
          <input
            type="file"
            accept=".pdf,.md,.json,.yaml,.yml"
            onChange={handleUpload}
            disabled={uploading}
            className="text-sm text-slate-600 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:bg-blue-50 file:text-blue-700 file:font-medium hover:file:bg-blue-100 file:cursor-pointer disabled:opacity-50"
          />
        </label>
        {uploading && <span className="ml-4 text-slate-600 text-sm">Uploading...</span>}
        {uploadError && (
          <div className="mt-4 text-red-600 text-sm bg-red-50 px-3 py-2 rounded-md">
            {uploadError}
          </div>
        )}
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 px-4 py-3 rounded-lg">{error}</div>
      )}

      <div className="bg-white rounded-lg shadow-md overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200">
          <h2 className="text-lg font-medium text-slate-800">Your documents</h2>
        </div>
        <div className="divide-y divide-slate-200">
          {documents.length === 0 ? (
            <div className="px-6 py-12 text-center text-slate-600">
              No documents yet. Upload one above.
            </div>
          ) : (
            documents.map((doc) => (
              <div
                key={doc.id}
                className="px-6 py-4 flex items-center justify-between gap-4 flex-wrap"
              >
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-slate-800 truncate flex items-center gap-2 flex-wrap">
                    <span>{doc.filename}</span>
                    <HealthBadge health={doc.health_status} />
                  </p>
                  <p className="text-slate-600 text-sm">
                    {new Date(doc.created_at).toLocaleString()} · {doc.file_type}
                  </p>
                  {doc.health_status && doc.health_status.warnings.length > 0 && (
                    <details className="mt-2 text-sm text-slate-600 max-w-xl">
                      <summary className="cursor-pointer text-slate-700 font-medium">
                        View {doc.health_status.warnings.length} warning
                        {doc.health_status.warnings.length === 1 ? "" : "s"}
                      </summary>
                      <ul className="mt-2 space-y-2 pl-4 list-disc">
                        {doc.health_status.warnings.map((w, i) => (
                          <li key={i}>
                            <span className="text-slate-500">[{w.severity}]</span> {w.message}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={doc.status} />
                  <button
                    type="button"
                    onClick={() => handleRecheckHealth(doc.id)}
                    disabled={recheckingId === doc.id}
                    className="text-blue-600 hover:text-blue-700 text-sm font-medium disabled:opacity-50"
                  >
                    {recheckingId === doc.id ? "Re-checking…" : "Re-check"}
                  </button>
                  <button
                    onClick={() => handleDelete(doc.id)}
                    className="text-red-600 hover:text-red-700 text-sm font-medium"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
