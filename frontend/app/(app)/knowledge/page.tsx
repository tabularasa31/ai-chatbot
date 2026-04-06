"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  api,
  type DocumentHealthStatus,
  type DocumentListItem,
  type KnowledgeFaqItem,
  type KnowledgeProfile,
  type UrlSource,
  type UrlSourceDetail,
} from "@/lib/api";
import { Tooltip } from "@/components/ui/tooltip";

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
  return (
    <Tooltip
      className="z-10 text-xs text-slate-600"
      content={
        <>
          {tooltipLines.map((line) => (
            <span key={line} className="block">
              {line}
            </span>
          ))}
          {checkedAt && <span className="mt-2 block text-[10px] text-slate-300">Checked: {checkedAt}</span>}
        </>
      }
    >
      <span className={`h-2 w-2 rounded-full ${dotClass}`} />
      <span className="font-medium">{label}</span>
    </Tooltip>
  );
}

function SourceHealthCell({
  status,
  warning,
  error,
}: {
  status: string;
  warning?: string | null;
  error?: string | null;
}) {
  let label = "Pending";
  let dotClass = "bg-slate-300";
  let note = "Source processing is still in progress.";

  if (error) {
    label = "Needs attention";
    dotClass = "bg-red-500";
    note = error;
  } else if (warning) {
    label = "Warning";
    dotClass = "bg-amber-400";
    note = warning;
  } else if (status === "ready") {
    label = "Good";
    dotClass = "bg-emerald-500";
    note = "No active warnings.";
  } else if (status === "paused" || status === "error") {
    label = "Needs attention";
    dotClass = "bg-red-500";
    note = "Source requires action before indexing can continue.";
  }

  return (
    <Tooltip className="z-10 max-w-[240px] text-xs text-slate-600" content={note}>
      <span className={`h-2 w-2 rounded-full ${dotClass}`} />
      <span className="font-medium">{label}</span>
      {(warning || error) && <span className="truncate text-slate-400">{warning || error}</span>}
    </Tooltip>
  );
}

type MixedRow =
  | { kind: "file"; item: DocumentListItem }
  | { kind: "url"; item: UrlSource };

const POLLABLE_SOURCE_STATUSES = new Set(["queued", "indexing"]);

function formatSchedule(value: string) {
  if (value === "manual") return "Manual only";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function stopRowClick(event: React.MouseEvent<HTMLElement>) {
  event.stopPropagation();
}

function KnowledgeTabs({
  activeTab,
  onChange,
}: {
  activeTab: "documents" | "profile" | "faq";
  onChange: (tab: "documents" | "profile" | "faq") => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1">
      <button
        className={`rounded-md px-3 py-1.5 text-sm ${activeTab === "documents" ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
        onClick={() => onChange("documents")}
      >
        Documents
      </button>
      <button
        className={`rounded-md px-3 py-1.5 text-sm ${activeTab === "profile" ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
        onClick={() => onChange("profile")}
      >
        Profile
      </button>
      <button
        className={`rounded-md px-3 py-1.5 text-sm ${activeTab === "faq" ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
        onClick={() => onChange("faq")}
      >
        FAQ
      </button>
    </div>
  );
}

export default function KnowledgePage() {
  const router = useRouter();
  const pathname = usePathname();
  const botIdMatch = pathname.match(/^\/dashboard\/bots\/([^/]+)\/knowledge$/);
  const botId = botIdMatch?.[1];
  const [activeTab, setActiveTab] = useState<"documents" | "profile" | "faq">("documents");

  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [sources, setSources] = useState<UrlSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [submittingUrl, setSubmittingUrl] = useState(false);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [recheckingId, setRecheckingId] = useState<string | null>(null);
  const [refreshingSourceId, setRefreshingSourceId] = useState<string | null>(null);
  const [deletingPageId, setDeletingPageId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [expandedSourceId, setExpandedSourceId] = useState<string | null>(null);
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
  const [profileLoading, setProfileLoading] = useState(false);
  const [faqLoading, setFaqLoading] = useState(false);
  const [profile, setProfile] = useState<KnowledgeProfile | null>(null);
  const [profileDraft, setProfileDraft] = useState<KnowledgeProfile | null>(null);
  const [profileSaved, setProfileSaved] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [newModule, setNewModule] = useState("");
  const [newSupportUrl, setNewSupportUrl] = useState("");
  const [profileEditingField, setProfileEditingField] = useState<"product_name" | "modules" | "support_email" | "support_urls" | null>(null);
  const [faqItems, setFaqItems] = useState<KnowledgeFaqItem[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [faqFilter, setFaqFilter] = useState<"all" | "pending" | "approved" | "docs" | "logs">("all");
  const [faqError, setFaqError] = useState("");
  const [faqSaved, setFaqSaved] = useState("");
  const [approvingAll, setApprovingAll] = useState(false);
  const [updatingFaqId, setUpdatingFaqId] = useState<string | null>(null);
  const [editingFaqId, setEditingFaqId] = useState<string | null>(null);
  const [editingFaqQuestion, setEditingFaqQuestion] = useState("");
  const [editingFaqAnswer, setEditingFaqAnswer] = useState("");
  const [removingFaqIds, setRemovingFaqIds] = useState<string[]>([]);
  const faqItemsRef = useRef<KnowledgeFaqItem[]>([]);

  function setTab(tab: "documents" | "profile" | "faq") {
    const next = new URLSearchParams(
      typeof window === "undefined" ? "" : window.location.search
    );
    if (tab === "documents") next.delete("tab");
    else next.set("tab", tab);
    const query = next.toString();
    setActiveTab(tab);
    router.replace(`${pathname}${query ? `?${query}` : ""}`);
  }

  function hasProfileChanges(): boolean {
    if (!profile || !profileDraft) return false;
    return JSON.stringify({
      product_name: profile.product_name,
      modules: profile.modules,
      support_email: profile.support_email,
      support_urls: profile.support_urls,
    }) !== JSON.stringify({
      product_name: profileDraft.product_name,
      modules: profileDraft.modules,
      support_email: profileDraft.support_email,
      support_urls: profileDraft.support_urls,
    });
  }

  function confidenceBadge(value?: number | null): string {
    if (value == null) return "Low";
    if (value >= 0.85) return "High";
    if (value >= 0.6) return "Medium";
    return "Low";
  }

  const loadProfile = useCallback(async () => {
    setProfileLoading(true);
    setProfileError("");
    try {
      const next = await api.knowledge.getProfile(botId);
      setProfile(next);
      setProfileDraft(next);
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : "Failed to load profile");
    } finally {
      setProfileLoading(false);
    }
  }, [botId]);

  const loadFaq = useCallback(async () => {
    setFaqLoading(true);
    setFaqError("");
    try {
      const mapping = {
        all: { approved: "all" as const, source: "all" as const },
        pending: { approved: "false" as const, source: "all" as const },
        approved: { approved: "true" as const, source: "all" as const },
        docs: { approved: "all" as const, source: "docs" as const },
        logs: { approved: "all" as const, source: "logs" as const },
      };
      const data = await api.knowledge.listFaq(mapping[faqFilter], botId);
      setFaqItems(data.items);
      setPendingCount(data.pending_count);
      return data;
    } catch (err) {
      setFaqError(err instanceof Error ? err.message : "Failed to load FAQ");
      return null;
    } finally {
      setFaqLoading(false);
    }
  }, [botId, faqFilter]);

  const load = useCallback(async () => {
    try {
      const data = await api.documents.listSources();
      setDocuments(data.documents);
      setSources(data.url_sources);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const tab = params.get("tab");
    if (tab === "profile" || tab === "faq") {
      setActiveTab(tab);
    } else {
      setActiveTab("documents");
    }
  }, []);

  useEffect(() => {
    if (activeTab === "profile") void loadProfile();
    if (activeTab === "faq") void loadFaq();
  }, [activeTab, loadFaq, loadProfile]);

  const loadDetail = useCallback(async (sourceId: string) => {
    try {
      setDetailLoading(true);
      const next = await api.documents.getSourceById(sourceId);
      setDetail(next);
      return next;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load source details");
      return null;
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    const shouldPoll = sources.some((source) => POLLABLE_SOURCE_STATUSES.has(source.status));
    if (!shouldPoll) return;
    const timer = window.setInterval(() => {
      void load();
      if (expandedSourceId && detail && detail.id === expandedSourceId && POLLABLE_SOURCE_STATUSES.has(detail.status) && !isEditing) {
        void loadDetail(expandedSourceId);
      }
    }, 10000);
    return () => window.clearInterval(timer);
  }, [sources, expandedSourceId, detail, isEditing, load, loadDetail]);

  useEffect(() => {
    faqItemsRef.current = faqItems;
  }, [faqItems]);

  useEffect(() => {
    if (!profile || profile.extraction_status !== "pending") return;
    const timer = window.setInterval(async () => {
      const next = await api.knowledge.getProfile(botId);
      const wasPending = profile.extraction_status === "pending";
      setProfile(next);
      setProfileDraft((prev) => prev ?? next);
      if (wasPending && next.extraction_status === "done") {
        const before = faqItemsRef.current.length;
        const nextFaq = await loadFaq();
        const after = nextFaq?.items.length ?? before;
        const added = Math.max(0, after - before);
        setFaqSaved(`New knowledge extracted! ${added} FAQ suggestions added.`);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [botId, profile, loadFaq]);

  async function toggleDetail(sourceId: string) {
    if (expandedSourceId === sourceId) {
      setExpandedSourceId(null);
      setDetail(null);
      setIsEditing(false);
      return;
    }
    setExpandedSourceId(sourceId);
    setIsEditing(false);
    await loadDetail(sourceId);
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
      setExpandedSourceId(source.id);
      await loadDetail(source.id);
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
      if (expandedSourceId === id) {
        setExpandedSourceId(null);
        setDetail(null);
        setIsEditing(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleRefreshSource(id: string) {
    setRefreshingSourceId(id);
    try {
      const source = await api.documents.refreshSource(id);
      setSources((prev) => prev.map((item) => (item.id === id ? source : item)));
      if (expandedSourceId === id) await loadDetail(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setRefreshingSourceId(null);
    }
  }

  async function handleDeleteSourcePage(sourceId: string, documentId: string) {
    if (!confirm("Delete this indexed page from Knowledge Hub and exclude it from future refreshes?")) return;
    setDeletingPageId(documentId);
    try {
      await api.documents.deleteSourcePage(sourceId, documentId);
      await load();
      if (expandedSourceId === sourceId) {
        await loadDetail(sourceId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeletingPageId(null);
    }
  }

  function openEdit(source: UrlSource) {
    setExpandedSourceId(source.id);
    setEditName(source.name ?? "");
    setEditSchedule(source.schedule);
    setEditExclusions((detail?.id === source.id ? detail.exclusion_patterns : source.exclusion_patterns).join("\n"));
    setIsEditing(true);
    if (detail?.id !== source.id) {
      void loadDetail(source.id);
    }
  }

  async function handleSaveEdit() {
    if (!expandedSourceId) return;
    setIsSaving(true);
    try {
      await api.documents.updateSource(expandedSourceId, {
        name: editName.trim(),
        schedule: editSchedule,
        exclusions: editExclusions.split("\n").map((s) => s.trim()).filter(Boolean),
      });
      setIsEditing(false);
      await load();
      await loadDetail(expandedSourceId);
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
      return row.item.name.toLowerCase().includes(text) || row.item.url.toLowerCase().includes(text);
    });
  })();

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-sm text-slate-500">Loading…</div>
      </div>
    );
  }

  if (activeTab === "profile") {
    return (
      <div className="max-w-7xl space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-slate-800">Knowledge Hub</h1>
          <p className="mt-1 text-sm text-slate-500">Files and URL sources that power your bot.</p>
        </div>
        <KnowledgeTabs activeTab="profile" onChange={setTab} />
        {profileError && <div className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{profileError}</div>}
        {profileSaved && <div className="rounded-lg border border-green-100 bg-green-50 px-4 py-3 text-sm text-green-700">Profile updated</div>}
        {profileLoading || !profileDraft ? (
          <div className="rounded-2xl border border-slate-200 bg-white p-5 text-sm text-slate-500">Loading profile…</div>
        ) : (
          <>
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
              <div className="text-sm font-medium text-slate-700">Extracted profile</div>
              <label className="block">
                <span className="mb-1 block text-xs text-slate-500">Product name</span>
                {profileEditingField === "product_name" ? (
                  <input
                    value={profileDraft.product_name ?? ""}
                    onChange={(e) => setProfileDraft({ ...profileDraft, product_name: e.target.value || null })}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700"
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => setProfileEditingField("product_name")}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50"
                  >
                    {profileDraft.product_name ?? "—"}
                  </button>
                )}
              </label>
              <div>
                <span className="mb-1 block text-xs text-slate-500">Modules</span>
                <div className="flex flex-wrap gap-2">
                  {profileDraft.modules.map((module) => (
                    <span key={module} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700">
                      {module}
                      {profileEditingField === "modules" && (
                        <button
                          type="button"
                          onClick={() => setProfileDraft({ ...profileDraft, modules: profileDraft.modules.filter((m) => m !== module) })}
                          className="text-slate-400 hover:text-slate-600"
                        >
                          ×
                        </button>
                      )}
                    </span>
                  ))}
                </div>
                {profileEditingField === "modules" ? (
                  <div className="mt-2 flex gap-2">
                    <input value={newModule} onChange={(e) => setNewModule(e.target.value)} className="w-64 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700" placeholder="Add module" />
                    <button
                      type="button"
                      onClick={() => {
                        const value = newModule.trim();
                        if (!value || profileDraft.modules.includes(value)) return;
                        setProfileDraft({ ...profileDraft, modules: [...profileDraft.modules, value] });
                        setNewModule("");
                      }}
                      className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                    >
                      Add
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setProfileEditingField("modules")}
                    className="mt-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                  >
                    Edit modules
                  </button>
                )}
              </div>
              <label className="block">
                <span className="mb-1 block text-xs text-slate-500">Support email</span>
                {profileEditingField === "support_email" ? (
                  <input
                    value={profileDraft.support_email ?? ""}
                    onChange={(e) => setProfileDraft({ ...profileDraft, support_email: e.target.value || null })}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700"
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => setProfileEditingField("support_email")}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50"
                  >
                    {profileDraft.support_email ?? "—"}
                  </button>
                )}
              </label>
              <div>
                <span className="mb-1 block text-xs text-slate-500">Support URLs</span>
                <div className="space-y-2">
                  {profileDraft.support_urls.map((url) => (
                    <div key={url} className="flex items-center gap-2 text-sm">
                      <span className="truncate text-slate-700">{url}</span>
                      {profileEditingField === "support_urls" && (
                        <button
                          type="button"
                          onClick={() => setProfileDraft({ ...profileDraft, support_urls: profileDraft.support_urls.filter((u) => u !== url) })}
                          className="text-red-400 hover:text-red-600"
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  ))}
                </div>
                {profileEditingField === "support_urls" ? (
                  <div className="mt-2 flex gap-2">
                    <input value={newSupportUrl} onChange={(e) => setNewSupportUrl(e.target.value)} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700" placeholder="https://..." />
                    <button
                      type="button"
                      onClick={() => {
                        const value = newSupportUrl.trim();
                        if (!value || profileDraft.support_urls.includes(value)) return;
                        setProfileDraft({ ...profileDraft, support_urls: [...profileDraft.support_urls, value] });
                        setNewSupportUrl("");
                      }}
                      className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                    >
                      Add
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setProfileEditingField("support_urls")}
                    className="mt-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                  >
                    Edit URLs
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={!hasProfileChanges()}
                  onClick={async () => {
                    try {
                      if (!profileDraft) return;
                      const updated = await api.knowledge.patchProfile({
                        product_name: profileDraft.product_name,
                        modules: profileDraft.modules,
                        support_email: profileDraft.support_email,
                        support_urls: profileDraft.support_urls,
                      }, botId);
                      setProfile(updated);
                      setProfileDraft(updated);
                      setProfileSaved(true);
                      window.setTimeout(() => setProfileSaved(false), 2500);
                    } catch (err) {
                      setProfileError(err instanceof Error ? err.message : "Failed to update profile");
                    }
                  }}
                  className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Save changes
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setProfileDraft(profile);
                    setProfileEditingField(null);
                  }}
                  className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                >
                  Cancel
                </button>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="text-sm font-medium text-slate-700">Extraction status</div>
              <div className="mt-2 text-sm text-slate-600">
                {profile?.extraction_status === "pending" && "Extracting knowledge from your docs..."}
                {profile?.extraction_status === "done" && `Last updated: ${new Date(profile.updated_at).toLocaleString()}`}
                {profile?.extraction_status === "failed" && "Extraction failed. Try re-indexing your documents."}
              </div>
            </div>

            <details className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <summary className="cursor-pointer text-sm font-medium text-slate-700">
                Show glossary ({profileDraft.glossary.length} terms)
              </summary>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase text-slate-400">
                    <tr><th className="py-2">Term</th><th className="py-2">Definition</th><th className="py-2">Confidence</th><th className="py-2">Source</th></tr>
                  </thead>
                  <tbody>
                    {profileDraft.glossary.map((entry, idx) => (
                      <tr key={`${entry.term ?? "term"}-${idx}`} className="border-t border-slate-100">
                        <td className="py-2 text-slate-700">{entry.term ?? "—"}</td>
                        <td className="py-2 text-slate-600">{entry.definition ?? "—"}</td>
                        <td className="py-2"><span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">{confidenceBadge(entry.confidence)}</span></td>
                        <td className="py-2 text-slate-500">{entry.source ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </>
        )}
      </div>
    );
  }

  if (activeTab === "faq") {
    return (
      <div className="max-w-7xl space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-slate-800">Knowledge Hub</h1>
          <p className="mt-1 text-sm text-slate-500">Files and URL sources that power your bot.</p>
        </div>
        <KnowledgeTabs activeTab="faq" onChange={setTab} />
        <div className="flex flex-wrap items-center gap-3">
          <span className={`rounded-full px-3 py-1 text-xs font-medium ${pendingCount > 0 ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600"}`}>
            {pendingCount} pending review
          </span>
          <button
            type="button"
            disabled={approvingAll || pendingCount === 0}
            onClick={async () => {
              setApprovingAll(true);
              try {
                await api.knowledge.approveAll(botId);
                setFaqSaved("All pending FAQ accepted.");
                await loadFaq();
              } catch (err) {
                setFaqError(err instanceof Error ? err.message : "Failed to approve all");
              } finally {
                setApprovingAll(false);
              }
            }}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {approvingAll ? "Accepting..." : "Accept all"}
          </button>
          <select
            value={faqFilter}
            onChange={(e) => setFaqFilter(e.target.value as typeof faqFilter)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
          >
            <option value="all">All</option>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="docs">From docs</option>
            <option value="logs">From logs</option>
          </select>
        </div>
        {faqSaved && <div className="rounded-lg border border-green-100 bg-green-50 px-4 py-3 text-sm text-green-700">{faqSaved}</div>}
        {faqError && <div className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{faqError}</div>}
        {faqLoading ? (
          <div className="rounded-2xl border border-slate-200 bg-white p-5 text-sm text-slate-500">Loading FAQ…</div>
        ) : faqItems.length === 0 ? (
          <div className="rounded-2xl border border-slate-200 bg-white p-5 text-sm text-slate-500">
            {faqFilter === "pending"
              ? "All caught up! No FAQ entries need review."
              : faqFilter === "approved"
                ? "No approved FAQ yet. Review the suggestions above."
                : "No FAQ entries yet. They will appear after your documents are indexed."}
          </div>
        ) : (
          <div className="space-y-3">
            {faqItems.map((item) => {
              const isEditing = editingFaqId === item.id;
              return (
                <div
                  key={item.id}
                  className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition-all duration-200 ${removingFaqIds.includes(item.id) ? "opacity-0 scale-[0.98]" : "opacity-100 scale-100"}`}
                >
                  {isEditing ? (
                    <div className="space-y-3">
                      {item.approved && <div className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700">Will require re-approval</div>}
                      <input value={editingFaqQuestion} onChange={(e) => setEditingFaqQuestion(e.target.value)} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-800" />
                      <textarea value={editingFaqAnswer} onChange={(e) => setEditingFaqAnswer(e.target.value)} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700" rows={4} />
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          disabled={updatingFaqId === item.id}
                          onClick={async () => {
                            try {
                              setUpdatingFaqId(item.id);
                              await api.knowledge.updateFaq(item.id, { question: editingFaqQuestion, answer: editingFaqAnswer }, botId);
                              setEditingFaqId(null);
                              setFaqSaved(item.approved ? "Saved. Re-approval required." : "Saved.");
                              await loadFaq();
                            } catch (err) {
                              setFaqError(err instanceof Error ? err.message : "Failed to save FAQ");
                            } finally {
                              setUpdatingFaqId(null);
                            }
                          }}
                          className="rounded-lg bg-violet-600 px-3 py-2 text-sm text-white disabled:opacity-50"
                        >
                          Save
                        </button>
                        <button type="button" onClick={() => setEditingFaqId(null)} className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600">Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="space-y-2">
                          <div className="text-sm font-semibold text-slate-800">{item.question}</div>
                          <div className="text-sm text-slate-600">{item.answer}</div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">{item.source ?? "unknown"}</span>
                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">{confidenceBadge(item.confidence)}</span>
                            {item.approved && <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700">Approved</span>}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {!item.approved && (
                            <>
                              <button
                                type="button"
                                disabled={updatingFaqId === item.id}
                                onClick={async () => {
                                  try {
                                    setUpdatingFaqId(item.id);
                                    await api.knowledge.approveFaq(item.id, botId);
                                    await loadFaq();
                                  } catch (err) {
                                    setFaqError(err instanceof Error ? err.message : "Failed to approve FAQ");
                                  } finally {
                                    setUpdatingFaqId(null);
                                  }
                                }}
                                className="rounded-lg bg-green-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
                              >
                                Accept
                              </button>
                              <button
                                type="button"
                                disabled={updatingFaqId === item.id}
                                onClick={async () => {
                                  const snapshot = faqItems;
                                  setRemovingFaqIds((prev) => [...prev, item.id]);
                                  window.setTimeout(() => {
                                    setFaqItems((prev) => prev.filter((i) => i.id !== item.id));
                                  }, 140);
                                  try {
                                    setUpdatingFaqId(item.id);
                                    await api.knowledge.rejectFaq(item.id, botId);
                                    await loadFaq();
                                  } catch (err) {
                                    setFaqItems(snapshot);
                                    setFaqError(err instanceof Error ? err.message : "Failed to reject FAQ");
                                  } finally {
                                    setUpdatingFaqId(null);
                                    setRemovingFaqIds((prev) => prev.filter((id) => id !== item.id));
                                  }
                                }}
                                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 disabled:opacity-50"
                              >
                                Reject
                              </button>
                            </>
                          )}
                          <button
                            type="button"
                            onClick={() => {
                              setEditingFaqId(item.id);
                              setEditingFaqQuestion(item.question);
                              setEditingFaqAnswer(item.answer);
                            }}
                            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600"
                          >
                            Edit
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

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
      <KnowledgeTabs activeTab="documents" onChange={setTab} />

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
                const isExpanded = expandedSourceId === source.id;
                const currentDetail = detail?.id === source.id ? detail : null;
                const pageMeta = source.pages_found ? `${source.pages_indexed} / ${source.pages_found}` : `${source.pages_indexed}`;

                return (
                  <Fragment key={source.id}>
                    <tr
                      className={`cursor-pointer transition-colors hover:bg-slate-50/60 ${isExpanded ? "bg-violet-50/40" : ""}`}
                      onClick={() => void toggleDetail(source.id)}
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
                            onClick={() => openEdit(source)}
                            className="text-xs text-slate-500 hover:text-slate-700"
                          >
                            Edit
                          </button>
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
                    {isExpanded && (
                      <tr className="bg-slate-50/60">
                        <td colSpan={7} className="px-5 py-4">
                          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                            {detailLoading && !currentDetail ? (
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
                                )}

                                {(currentDetail?.warning_message || currentDetail?.error_message) && (
                                  <div className="space-y-2">
                                    {currentDetail.warning_message && (
                                      <div className="rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                                        {currentDetail.warning_message}
                                      </div>
                                    )}
                                    {currentDetail.error_message && (
                                      <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
                                        {currentDetail.error_message}
                                      </div>
                                    )}
                                  </div>
                                )}

                                <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
                                  <div>
                                    <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Recent runs</div>
                                    <div className="space-y-2">
                                      {!currentDetail || currentDetail.recent_runs.length === 0 ? (
                                        <div className="text-sm text-slate-500">No crawl runs yet.</div>
                                      ) : (
                                        currentDetail.recent_runs.map((run) => (
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
                                    <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Exclusions</div>
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
                                      {currentDetail && currentDetail.exclusion_patterns.length
                                        ? currentDetail.exclusion_patterns.join(", ")
                                        : "No exclusions"}
                                    </div>
                                  </div>
                                </div>

                                <div>
                                  <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Indexed pages</div>
                                  {!currentDetail || currentDetail.pages.length === 0 ? (
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-500">
                                      Pages will appear here after indexing starts.
                                    </div>
                                  ) : (
                                    <div className="grid gap-3 xl:grid-cols-2">
                                      {currentDetail.pages.map((page) => (
                                        <div key={page.id} className="rounded-lg border border-slate-200 p-3">
                                          <div className="flex items-start justify-between gap-3">
                                            <div className="min-w-0">
                                              <div className="text-sm font-medium text-slate-700">{page.title}</div>
                                              <div className="mt-1 break-all text-xs text-slate-400">{page.url}</div>
                                            </div>
                                            <button
                                              type="button"
                                              onClick={() => void handleDeleteSourcePage(source.id, page.id)}
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
