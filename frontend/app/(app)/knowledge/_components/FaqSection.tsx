"use client";

import type { Dispatch, SetStateAction } from "react";
import { api, type KnowledgeFaqItem } from "@/lib/api";
import { KnowledgeTabs, confidenceBadge } from "./shared";

export interface FaqSectionProps {
  botId?: string;
  activeTab: "documents" | "profile" | "faq";
  onTabChange: (tab: "documents" | "profile" | "faq") => void;
  faqLoading: boolean;
  faqItems: KnowledgeFaqItem[];
  setFaqItems: Dispatch<SetStateAction<KnowledgeFaqItem[]>>;
  pendingCount: number;
  faqFilter: "all" | "pending" | "approved" | "docs" | "logs";
  setFaqFilter: Dispatch<SetStateAction<"all" | "pending" | "approved" | "docs" | "logs">>;
  faqError: string;
  setFaqError: Dispatch<SetStateAction<string>>;
  faqSaved: string;
  setFaqSaved: Dispatch<SetStateAction<string>>;
  approvingAll: boolean;
  setApprovingAll: Dispatch<SetStateAction<boolean>>;
  updatingFaqId: string | null;
  setUpdatingFaqId: Dispatch<SetStateAction<string | null>>;
  editingFaqId: string | null;
  setEditingFaqId: Dispatch<SetStateAction<string | null>>;
  editingFaqQuestion: string;
  setEditingFaqQuestion: Dispatch<SetStateAction<string>>;
  editingFaqAnswer: string;
  setEditingFaqAnswer: Dispatch<SetStateAction<string>>;
  removingFaqIds: string[];
  setRemovingFaqIds: Dispatch<SetStateAction<string[]>>;
  loadFaq: () => Promise<{ items: KnowledgeFaqItem[]; pending_count: number } | null>;
}

export function FaqSection({
  botId,
  activeTab,
  onTabChange,
  faqLoading,
  faqItems,
  setFaqItems,
  pendingCount,
  faqFilter,
  setFaqFilter,
  faqError,
  setFaqError,
  faqSaved,
  setFaqSaved,
  approvingAll,
  setApprovingAll,
  updatingFaqId,
  setUpdatingFaqId,
  editingFaqId,
  setEditingFaqId,
  editingFaqQuestion,
  setEditingFaqQuestion,
  editingFaqAnswer,
  setEditingFaqAnswer,
  removingFaqIds,
  setRemovingFaqIds,
  loadFaq,
}: FaqSectionProps) {
  return (
    <div className="max-w-7xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Knowledge Hub</h1>
        <p className="mt-1 text-sm text-slate-500">Files and URL sources that power your bot.</p>
      </div>
      <KnowledgeTabs activeTab={activeTab} onChange={onTabChange} />
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
