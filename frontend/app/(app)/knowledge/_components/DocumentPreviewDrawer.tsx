"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type DocumentDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { Input } from "@/components/ui/field";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { FlashMessage, useFlash } from "./flash";

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
          <mark key={idx} className="rounded-tag bg-brand px-0.5 text-brand-on">
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
  const [copiedMessage, flashCopied] = useFlash();
  const refetchRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tracks the documentId for which we've already issued the one-shot
  // processing-state refetch, to keep the retry strictly single-shot.
  const refetchedForIdRef = useRef<string | null>(null);
  // Tracks the latest requested documentId so we can drop stale responses
  // when the user quickly switches documents or closes the drawer.
  const activeIdRef = useRef<string | null>(null);

  const isOpen = documentId !== null;

  const load = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.documents.getById(id);
      if (activeIdRef.current !== id) return;
      setDoc(data);
    } catch {
      if (activeIdRef.current !== id) return;
      setError("Couldn't load this document.");
      setDoc(null);
    } finally {
      if (activeIdRef.current === id) {
        setLoading(false);
      }
    }
  }, []);

  // Fetch on open / id change.
  useEffect(() => {
    if (refetchRef.current) {
      clearTimeout(refetchRef.current);
      refetchRef.current = null;
    }
    activeIdRef.current = documentId;
    if (!documentId) {
      refetchedForIdRef.current = null;
      setDoc(null);
      setError(null);
      setSearch("");
      return;
    }
    // New document selected — allow exactly one processing-state refetch for it.
    if (refetchedForIdRef.current !== documentId) {
      refetchedForIdRef.current = null;
    }
    void load(documentId);
  }, [documentId, load]);

  // One-shot refetch if document is still being processed.
  useEffect(() => {
    if (!documentId || !doc) return;
    if (doc.parsed_text || !isProcessingStatus(doc.status)) return;
    if (refetchedForIdRef.current === documentId) return;
    refetchedForIdRef.current = documentId;
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

  const handleCopy = useCallback(async () => {
    if (!doc?.parsed_text) return;
    try {
      await navigator.clipboard.writeText(doc.parsed_text);
      flashCopied("Copied");
    } catch {
      setError("Couldn't copy the text. Try again.");
    }
  }, [doc, flashCopied]);

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
    <Drawer
      title={doc?.filename || (loading ? "Loading" : "Document")}
      meta={
        <>
          {doc?.source_url && (
            <a
              href={doc.source_url}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-1 block break-all text-caption text-fg-muted hover:underline"
            >
              {doc.source_url}
            </a>
          )}
          {doc?.parsed_text_length != null && (
            <p className="mt-1 text-caption tabular-nums text-fg-muted">
              {formatNumber(doc.parsed_text_length)} chars · {doc.file_type}
            </p>
          )}
        </>
      }
      actions={
        <>
          <Button variant="secondary" offset="surface" onClick={() => void handleCopy()} disabled={!text}>
            Copy
          </Button>
          <Button variant="secondary" offset="surface" onClick={() => doc && downloadAsTxt(doc.filename, text)} disabled={!text}>
            Download .txt
          </Button>
        </>
      }
      onClose={onClose}
    >
      {text && (
        <div className="flex items-center gap-3 border-b border-line px-6 py-3">
          <Input
            label="Search in this document"
            hideLabel
            type="search"
            placeholder="Search in this document"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="min-w-0 flex-1"
          />
          {search.trim() && (
            <span className="shrink-0 text-caption tabular-nums text-fg-muted">
              {matchCount} match{matchCount === 1 ? "" : "es"}
            </span>
          )}
          <FlashMessage message={copiedMessage} />
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading && !doc && (
          <SkeletonRegion>
            <div className="space-y-2">
              <Skeleton className="w-3/4" />
              <Skeleton className="w-2/3" />
              <Skeleton className="w-5/6" />
              <Skeleton className="w-1/2" />
            </div>
          </SkeletonRegion>
        )}
        {error && !loading && documentId && (
          <LoadError message={error} onRetry={() => load(documentId)} />
        )}
        {showProcessingState && (
          <p className="text-ui text-fg-muted">Indexing in progress. Content will appear here once parsing completes.</p>
        )}
        {showEmptyState && <p className="text-ui text-fg-muted">No text was extracted from this document.</p>}
        {text && (
          <pre className="whitespace-pre-wrap break-words font-sans text-ui text-fg">
            <HighlightedText text={text} query={search} />
          </pre>
        )}
      </div>
    </Drawer>
  );
}
