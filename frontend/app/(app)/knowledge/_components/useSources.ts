"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type DocumentListItem,
  type UrlSource,
  type UrlSourceDetail,
} from "@/lib/api";
import { useFlash } from "@/components/ui/flash";
import {
  POLLABLE_SOURCE_STATUSES,
  parseExclusions,
  type SourceFormValues,
} from "./shared";

const SOURCE_POLL_MS = 10_000;
const EMBED_POLL_MS = 2_000;
const EMBED_TIMEOUT_MS = 120_000;

type RequestState = "idle" | "loading" | "ready" | "error";

export function useSources() {
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [sources, setSources] = useState<UrlSource[]>([]);
  const [loadState, setLoadState] = useState<RequestState>("loading");
  const [uploadProgress, setUploadProgress] = useState<{ current: number; total: number } | null>(null);
  const [uploadErrors, setUploadErrors] = useState<string[]>([]);
  const [actionError, setActionError] = useState("");
  const [recheckingId, setRecheckingId] = useState<string | null>(null);
  const [refreshingSourceId, setRefreshingSourceId] = useState<string | null>(null);
  const [expandedSourceId, setExpandedSourceId] = useState<string | null>(null);
  const [detail, setDetail] = useState<UrlSourceDetail | null>(null);
  const [detailState, setDetailState] = useState<RequestState>("idle");
  const [isEditing, setIsEditing] = useState(false);
  const [uploadedMessage, flashUploaded] = useFlash();
  const expandedRef = useRef<string | null>(null);

  function expand(sourceId: string | null) {
    expandedRef.current = sourceId;
    setExpandedSourceId(sourceId);
  }

  const load = useCallback(async () => {
    try {
      const data = await api.documents.listSources();
      setDocuments(data.documents);
      setSources(data.url_sources);
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, []);

  const loadDetail = useCallback(async (sourceId: string) => {
    setDetailState("loading");
    try {
      const next = await api.documents.getSourceById(sourceId);
      if (expandedRef.current !== sourceId) return;
      setDetail(next);
      setDetailState("ready");
    } catch {
      if (expandedRef.current === sourceId) setDetailState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!sources.some((source) => POLLABLE_SOURCE_STATUSES.has(source.status))) return;
    const timer = window.setInterval(() => {
      void load();
      if (expandedSourceId && detail?.id === expandedSourceId && POLLABLE_SOURCE_STATUSES.has(detail.status) && !isEditing) {
        void loadDetail(expandedSourceId);
      }
    }, SOURCE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [sources, expandedSourceId, detail, isEditing, load, loadDetail]);

  function collapseDetail() {
    expand(null);
    setDetail(null);
    setIsEditing(false);
  }

  async function toggleDetail(sourceId: string) {
    if (expandedSourceId === sourceId) {
      collapseDetail();
      return;
    }
    expand(sourceId);
    setIsEditing(false);
    await loadDetail(sourceId);
  }

  async function pollUntilEmbedded(docId: string) {
    const deadline = Date.now() + EMBED_TIMEOUT_MS;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, EMBED_POLL_MS));
      const updated = await api.documents.getById(docId);
      setDocuments((prev) =>
        prev.map((d) => (d.id === docId ? { ...d, status: updated.status, health_status: updated.health_status } : d)),
      );
      if (updated.status === "ready" || updated.status === "error") return;
    }
  }

  async function uploadFiles(files: File[]) {
    setUploadErrors([]);
    const errors: string[] = [];
    for (let i = 0; i < files.length; i++) {
      setUploadProgress({ current: i + 1, total: files.length });
      try {
        const doc = await api.documents.upload(files[i]);
        setDocuments((prev) => [{ ...doc, updated_at: doc.created_at }, ...prev]);
        await api.embeddings.create(doc.id);
        await pollUntilEmbedded(doc.id);
      } catch {
        errors.push(`${files[i].name}: couldn't be uploaded`);
      }
    }
    await load();
    setUploadErrors(errors);
    setUploadProgress(null);
    const uploaded = files.length - errors.length;
    if (uploaded > 0) flashUploaded(uploaded === 1 ? "1 file uploaded" : `${uploaded} files uploaded`);
  }

  async function createUrlSource(values: SourceFormValues & { url: string }) {
    const source = await api.documents.createUrlSource({
      url: values.url,
      name: values.name || undefined,
      schedule: values.schedule,
      exclusions: parseExclusions(values.exclusions),
    });
    setSources((prev) => [source, ...prev]);
    expand(source.id);
    await loadDetail(source.id);
  }

  async function deleteFile(id: string) {
    await api.documents.delete(id);
    setDocuments((prev) => prev.filter((d) => d.id !== id));
  }

  async function deleteSource(id: string) {
    await api.documents.deleteSource(id);
    setSources((prev) => prev.filter((source) => source.id !== id));
    if (expandedSourceId === id) collapseDetail();
  }

  async function deleteSourcePage(sourceId: string, documentId: string) {
    await api.documents.deleteSourcePage(sourceId, documentId);
    await load();
    if (expandedSourceId === sourceId) await loadDetail(sourceId);
  }

  async function refreshSource(id: string) {
    setRefreshingSourceId(id);
    setActionError("");
    try {
      const source = await api.documents.refreshSource(id);
      setSources((prev) => prev.map((item) => (item.id === id ? source : item)));
      if (expandedSourceId === id) await loadDetail(id);
    } catch {
      setActionError("Couldn't refresh this source. Try again.");
    } finally {
      setRefreshingSourceId(null);
    }
  }

  async function startEdit(sourceId: string) {
    setActionError("");
    setIsEditing(true);
    if (expandedSourceId !== sourceId) {
      expand(sourceId);
      await loadDetail(sourceId);
    }
  }

  async function saveEdit(sourceId: string, values: SourceFormValues) {
    await api.documents.updateSource(sourceId, {
      name: values.name.trim(),
      schedule: values.schedule,
      exclusions: parseExclusions(values.exclusions),
    });
    setIsEditing(false);
    await load();
    await loadDetail(sourceId);
  }

  async function recheckHealth(docId: string) {
    setRecheckingId(docId);
    setActionError("");
    try {
      const health = await api.documents.runHealth(docId);
      setDocuments((prev) => prev.map((d) => (d.id === docId ? { ...d, health_status: health } : d)));
    } catch {
      setActionError("Couldn't re-check this file. Try again.");
    } finally {
      setRecheckingId(null);
    }
  }

  return {
    documents,
    sources,
    loadState,
    load,
    uploadProgress,
    uploadErrors,
    clearUploadErrors: () => setUploadErrors([]),
    uploadedMessage,
    actionError,
    clearActionError: () => setActionError(""),
    recheckingId,
    refreshingSourceId,
    expandedSourceId,
    detail,
    detailState,
    loadDetail,
    isEditing,
    stopEditing: () => setIsEditing(false),
    toggleDetail,
    uploadFiles,
    createUrlSource,
    deleteFile,
    deleteSource,
    deleteSourcePage,
    refreshSource,
    startEdit,
    saveEdit,
    recheckHealth,
  };
}

export type Sources = ReturnType<typeof useSources>;
