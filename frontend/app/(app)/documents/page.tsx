"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Document = {
  id: string;
  filename: string;
  file_type: string;
  status: string;
  created_at: string;
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

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");

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
                  <p className="font-medium text-slate-800 truncate">{doc.filename}</p>
                  <p className="text-slate-600 text-sm">
                    {new Date(doc.created_at).toLocaleString()} · {doc.file_type}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={doc.status} />
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
