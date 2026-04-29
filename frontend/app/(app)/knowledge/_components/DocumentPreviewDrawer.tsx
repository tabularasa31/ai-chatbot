"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type DocumentDetail } from "@/lib/api";

const REFETCH_DELAY_MS = 5000;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

interface HighlightedTextProps {
  text: string;
  query: string;
}

function HighlightedText({ text, query }: HighlightedTextProps): JSX.Element {
  const trimmed = query.trim();
  if (!trimmed) {
    return <>{text}</>;
  }
  const pattern = new RegExp(`(${escapeRegExp(trimmed)})`, "gi");
  const parts = text.split(pattern);
  const lowerTrimmed = trimmed.toLowerCase();
  return (
    <>
      {parts.map((part, idx) =>
        part.toLowerCase() === lowerTrimmed ? (
          <mark key={idx} className="rounded bg-yellow-200 px-0.5 text-slate-900">
            {part}
          </mark>
        ) : (
          <span key={idx}>{part}</span>
        ),
      )}
    </>
  );
}

function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}

function isProcessingStatus(status: string): boolean {
  return status === "processing" || status === "embedding" || status === "pending";
}

function downloadAsTxt(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  const baseName = filename.replace(/\.[^./]+$/, "");
  link.download = `${baseName || "document"}.txt`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export interface DocumentPreviewDrawerProps {
  documentId: string | null;
  onClose: () => void;
}

export function DocumentPreviewDrawer({ documentId, onClose }: DocumentPreviewDrawerProps) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [copied, setCopied] = useState(false);
  const refetchRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isOpen = documentId !== null;

  const load = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.documents.getById(id);
      setDoc(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load document");
      setDoc(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch on open / id change.
  useEffect(() => {
    if (refetchRef.current) {
      clearTimeout(refetchRef.current);
      refetchRef.current = null;
    }
    if (!documentId) {
      setDoc(null);
      setError(null);
      setSearch("");
      return;
    }
    void load(documentId);
  }, [documentId, load]);

  // One-shot refetch if document is still being processed.
  useEffect(() => {
    if (!documentId || !doc) return;
    if (doc.parsed_text || !isProcessingStatus(doc.status)) return;
    refetchRef.current = setTimeout(() => {
      void load(documentId);
    }, REFETCH_DELAY_MS);
    return () => {
      if (refetchRef.current) {
        clearTimeout(refetchRef.current);
        refetchRef.current = null;
      }
    };
  }, [documentId, doc, load]);

  // ESC closes.
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  const handleCopy = useCallback(async () => {
    if (!doc?.parsed_text) return;
    try {
      await navigator.clipboard.writeText(doc.parsed_text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }, [doc]);

  const matchCount = useMemo(() => {
    if (!doc?.parsed_text) return 0;
    const trimmed = search.trim();
    if (!trimmed) return 0;
    const re = new RegExp(escapeRegExp(trimmed), "gi");
    return doc.parsed_text.match(re)?.length ?? 0;
  }, [doc, search]);

  if (!isOpen) return null;

  const text = doc?.parsed_text ?? "";
  const showProcessingState = doc && !doc.parsed_text && isProcessingStatus(doc.status);
  const showEmptyState = doc && !doc.parsed_text && !isProcessingStatus(doc.status);

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="Document preview">
      <button
        type="button"
        aria-label="Close preview"
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/30 backdrop-blur-[1px]"
      />
      <aside
        className="absolute inset-y-0 right-0 flex w-full max-w-[560px] flex-col bg-white shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-800">
              {doc?.filename || (loading ? "Loading…" : "Document")}
            </div>
            {doc?.source_url && (
              <a
                href={doc.source_url}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-0.5 block truncate text-xs text-slate-400 hover:text-slate-600 hover:underline"
              >
                {doc.source_url}
              </a>
            )}
            {doc?.parsed_text_length != null && (
              <div className="mt-1 text-xs text-slate-400">
                {formatNumber(doc.parsed_text_length)} chars · {doc.file_type}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handleCopy()}
              disabled={!text}
              className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <button
              type="button"
              onClick={() => doc && downloadAsTxt(doc.filename, text)}
              disabled={!text}
              className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Download .txt
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100"
              aria-label="Close"
            >
              ✕
            </button>
          </div>
        </header>

        {text && (
          <div className="flex items-center gap-3 border-b border-slate-100 px-5 py-2.5">
            <input
              type="search"
              placeholder="Search in this document…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-700 outline-none focus:border-slate-400"
            />
            {search.trim() && (
              <span className="shrink-0 text-xs text-slate-400">
                {matchCount} match{matchCount === 1 ? "" : "es"}
              </span>
            )}
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && !doc && (
            <div className="space-y-2">
              <div className="h-3 w-3/4 animate-pulse rounded bg-slate-100" />
              <div className="h-3 w-2/3 animate-pulse rounded bg-slate-100" />
              <div className="h-3 w-5/6 animate-pulse rounded bg-slate-100" />
              <div className="h-3 w-1/2 animate-pulse rounded bg-slate-100" />
            </div>
          )}
          {error && !loading && (
            <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}
          {showProcessingState && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
              Indexing in progress. Content will appear here once parsing completes.
            </div>
          )}
          {showEmptyState && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
              No text was extracted from this document.
            </div>
          )}
          {text && (
            <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-slate-700">
              <HighlightedText text={text} query={search} />
            </pre>
          )}
        </div>
      </aside>
    </div>
  );
}
