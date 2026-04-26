"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  api,
  type DocumentListItem,
  type KnowledgeFaqItem,
  type KnowledgeProfile,
  type UrlSource,
  type UrlSourceDetail,
} from "@/lib/api";
import { KnowledgeTabs, confidenceBadge, POLLABLE_SOURCE_STATUSES } from "./_components/shared";
import { FaqSection } from "./_components/FaqSection";
import { DocumentsSection } from "./_components/DocumentsSection";

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
  const [uploadProgress, setUploadProgress] = useState<{ current: number; total: number } | null>(null);
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
  const [newTopic, setNewTopic] = useState("");
  const [newSupportUrl, setNewSupportUrl] = useState("");
  const [profileEditingField, setProfileEditingField] = useState<"product_name" | "topics" | "support_email" | "support_urls" | null>(null);
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
      topics: profile.topics,
      support_email: profile.support_email,
      support_urls: profile.support_urls,
    }) !== JSON.stringify({
      product_name: profileDraft.product_name,
      topics: profileDraft.topics,
      support_email: profileDraft.support_email,
      support_urls: profileDraft.support_urls,
    });
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
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;
    setUploadError("");
    setUploading(true);
    const errors: string[] = [];
    for (let i = 0; i < files.length; i++) {
      setUploadProgress({ current: i + 1, total: files.length });
      try {
        const doc = await api.documents.upload(files[i]);
        setDocuments((prev) => [doc as DocumentListItem, ...prev]);
        await api.embeddings.create(doc.id);
        await pollUntilEmbedded(doc.id);
      } catch (err) {
        errors.push(`${files[i].name}: ${err instanceof Error ? err.message : "Upload failed"}`);
      }
    }
    await load();
    if (errors.length > 0) setUploadError(errors.join("\n"));
    setUploading(false);
    setUploadProgress(null);
    e.target.value = "";
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
                <span className="mb-1 block text-xs text-slate-500">Topics</span>
                <div className="flex flex-wrap gap-2">
                  {profileDraft.topics.map((topic) => (
                    <span key={topic} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700">
                      {topic}
                      {profileEditingField === "topics" && (
                        <button
                          type="button"
                          onClick={() => setProfileDraft({ ...profileDraft, topics: profileDraft.topics.filter((item) => item !== topic) })}
                          className="text-slate-400 hover:text-slate-600"
                        >
                          ×
                        </button>
                      )}
                    </span>
                  ))}
                </div>
                {profileEditingField === "topics" ? (
                  <div className="mt-2 flex gap-2">
                    <input value={newTopic} onChange={(e) => setNewTopic(e.target.value)} className="w-64 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700" placeholder="Add topic" />
                    <button
                      type="button"
                      onClick={() => {
                        const value = newTopic.trim();
                        if (!value || profileDraft.topics.includes(value)) return;
                        setProfileDraft({ ...profileDraft, topics: [...profileDraft.topics, value] });
                        setNewTopic("");
                      }}
                      className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                    >
                      Add
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setProfileEditingField("topics")}
                    className="mt-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                  >
                    Edit topics
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
                        topics: profileDraft.topics,
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
      <FaqSection
        botId={botId}
        activeTab={activeTab}
        onTabChange={setTab}
        faqItems={faqItems}
        setFaqItems={setFaqItems}
        pendingCount={pendingCount}
        faqFilter={faqFilter}
        setFaqFilter={setFaqFilter}
        faqError={faqError}
        setFaqError={setFaqError}
        faqSaved={faqSaved}
        setFaqSaved={setFaqSaved}
        approvingAll={approvingAll}
        setApprovingAll={setApprovingAll}
        updatingFaqId={updatingFaqId}
        setUpdatingFaqId={setUpdatingFaqId}
        editingFaqId={editingFaqId}
        setEditingFaqId={setEditingFaqId}
        editingFaqQuestion={editingFaqQuestion}
        setEditingFaqQuestion={setEditingFaqQuestion}
        editingFaqAnswer={editingFaqAnswer}
        setEditingFaqAnswer={setEditingFaqAnswer}
        removingFaqIds={removingFaqIds}
        setRemovingFaqIds={setRemovingFaqIds}
        loadFaq={loadFaq}
      />
    );
  }

  return (
    <DocumentsSection
      activeTab={activeTab}
      onTabChange={setTab}
      documents={documents}
      sources={sources}
      filter={filter}
      setFilter={setFilter}
      uploading={uploading}
      uploadProgress={uploadProgress}
      submittingUrl={submittingUrl}
      error={error}
      uploadError={uploadError}
      recheckingId={recheckingId}
      refreshingSourceId={refreshingSourceId}
      deletingPageId={deletingPageId}
      expandedSourceId={expandedSourceId}
      detail={detail}
      detailLoading={detailLoading}
      showUrlForm={showUrlForm}
      setShowUrlForm={setShowUrlForm}
      urlInput={urlInput}
      setUrlInput={setUrlInput}
      nameInput={nameInput}
      setNameInput={setNameInput}
      scheduleInput={scheduleInput}
      setScheduleInput={setScheduleInput}
      exclusionsInput={exclusionsInput}
      setExclusionsInput={setExclusionsInput}
      isEditing={isEditing}
      setIsEditing={setIsEditing}
      editName={editName}
      setEditName={setEditName}
      editSchedule={editSchedule}
      setEditSchedule={setEditSchedule}
      editExclusions={editExclusions}
      setEditExclusions={setEditExclusions}
      isSaving={isSaving}
      onUpload={handleUpload}
      onCreateUrlSource={handleCreateUrlSource}
      onDeleteFile={handleDeleteFile}
      onDeleteSource={handleDeleteSource}
      onRefreshSource={handleRefreshSource}
      onDeleteSourcePage={handleDeleteSourcePage}
      onOpenEdit={openEdit}
      onSaveEdit={handleSaveEdit}
      onRecheckHealth={handleRecheckHealth}
      onToggleDetail={toggleDetail}
    />
  );
}
