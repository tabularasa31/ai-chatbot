"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type GapDraftPayload } from "@/lib/api";

function StatusPill({ status }: { status: GapDraftPayload["status"] }) {
  const styles: Record<string, string> = {
    drafting: "bg-amber-100 text-amber-800",
    in_review: "bg-indigo-100 text-indigo-800",
    resolved: "bg-emerald-100 text-emerald-800",
    active: "bg-violet-100 text-violet-800",
  };
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${styles[status] ?? "bg-slate-100 text-slate-700"}`}>
      {status.replace("_", " ")}
    </span>
  );
}

export default function ModeBDraftReviewPage() {
  const router = useRouter();
  const params = useParams<{ gapId: string }>();
  const gapId = params?.gapId ?? "";

  const [draft, setDraft] = useState<GapDraftPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "refine" | "publish" | "discard" | "resolve">("");
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [guidance, setGuidance] = useState("");

  const load = useCallback(async () => {
    if (!gapId) return;
    setLoading(true);
    setError("");
    try {
      const data = await api.gapAnalyzer.getModeBDraft(gapId);
      setDraft(data);
      setTitle(data.title);
      setQuestion(data.question);
      setMarkdown(data.markdown);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load draft");
    } finally {
      setLoading(false);
    }
  }, [gapId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = useCallback(async () => {
    if (!draft) return;
    setBusy("save");
    setError("");
    setInfo("");
    try {
      const updated = await api.gapAnalyzer.updateModeBDraft(gapId, {
        title,
        question,
        markdown,
        if_match: draft.draft_updated_at,
      });
      setDraft(updated);
      setTitle(updated.title);
      setQuestion(updated.question);
      setMarkdown(updated.markdown);
      setInfo("Draft saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save draft");
    } finally {
      setBusy("");
    }
  }, [draft, gapId, markdown, question, title]);

  const handleRefine = useCallback(async () => {
    if (!guidance.trim()) {
      setError("Add guidance for the agent before clicking Ask agent.");
      return;
    }
    setBusy("refine");
    setError("");
    setInfo("");
    try {
      const updated = await api.gapAnalyzer.refineModeBDraft(gapId, guidance.trim());
      setDraft(updated);
      setTitle(updated.title);
      setQuestion(updated.question);
      setMarkdown(updated.markdown);
      setGuidance("");
      setInfo("Draft refined by agent.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refine draft");
    } finally {
      setBusy("");
    }
  }, [gapId, guidance]);

  const handlePublish = useCallback(async () => {
    if (!window.confirm("Publish FAQ to your bot? It will start answering this question immediately.")) {
      return;
    }
    setBusy("publish");
    setError("");
    setInfo("");
    try {
      await api.gapAnalyzer.publishModeBDraft(gapId);
      router.push("/gap-analyzer");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to publish FAQ");
      setBusy("");
    }
  }, [gapId, router]);

  const handleDiscard = useCallback(async () => {
    if (!window.confirm("Discard this draft? The gap will return to the active list.")) {
      return;
    }
    setBusy("discard");
    setError("");
    try {
      await api.gapAnalyzer.discardModeBDraft(gapId);
      router.push("/gap-analyzer");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to discard draft");
      setBusy("");
    }
  }, [gapId, router]);

  const handleResolve = useCallback(async () => {
    if (!window.confirm("Mark this gap as resolved without publishing? Use this if you'll publish the answer elsewhere.")) {
      return;
    }
    setBusy("resolve");
    setError("");
    try {
      await api.gapAnalyzer.resolveModeBGap(gapId);
      router.push("/gap-analyzer");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to mark gap as resolved");
      setBusy("");
    }
  }, [gapId, router]);

  if (loading) {
    return <div className="p-6 text-sm text-slate-500">Loading draft…</div>;
  }
  if (!draft && error) {
    return (
      <div className="p-6 space-y-3">
        <Link href="/gap-analyzer" className="text-sm text-slate-600 hover:underline">
          ← Back to Gap Analyzer
        </Link>
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
      </div>
    );
  }
  if (!draft) {
    return <div className="p-6 text-sm text-slate-500">Draft not found.</div>;
  }

  const anyBusy = busy !== "";

  return (
    <div className="space-y-6 p-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href="/gap-analyzer" className="text-sm text-slate-600 hover:underline">
            ← Back to Gap Analyzer
          </Link>
          <StatusPill status={draft.status} />
          <span className="text-xs text-slate-400">Language: {draft.language}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={anyBusy}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {busy === "save" ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={handleResolve}
            disabled={anyBusy}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Mark as resolved
          </button>
          <button
            type="button"
            onClick={handleDiscard}
            disabled={anyBusy}
            className="rounded-lg border border-rose-200 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-50 disabled:opacity-50"
          >
            Discard
          </button>
          <button
            type="button"
            onClick={handlePublish}
            disabled={anyBusy}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {busy === "publish" ? "Publishing…" : "Publish to bot"}
          </button>
        </div>
      </div>

      {error && <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
      {info && <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{info}</div>}

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="space-y-3 self-start rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Gap context</h2>
          <p className="text-xs text-slate-500">
            Draft language: {draft.language}<br />
            Last updated: {new Date(draft.draft_updated_at).toLocaleString()}
          </p>
          <Link href={`/gap-analyzer`} className="block text-sm text-slate-600 hover:underline">
            See the full cluster on the Gap Analyzer list
          </Link>
        </aside>

        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500" htmlFor="draft-title">
              Title
            </label>
            <input
              id="draft-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-violet-400 focus:outline-none"
            />
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500" htmlFor="draft-question">
              Canonical question
            </label>
            <input
              id="draft-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-violet-400 focus:outline-none"
            />
            <p className="mt-2 text-xs text-slate-500">
              Used by the bot&apos;s FAQ matcher to find this answer.
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500" htmlFor="draft-markdown">
              Answer (Markdown)
            </label>
            <textarea
              id="draft-markdown"
              value={markdown}
              onChange={(e) => setMarkdown(e.target.value)}
              rows={16}
              className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-xs leading-6 focus:border-violet-400 focus:outline-none"
            />
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500" htmlFor="draft-guidance">
              Ask agent
            </label>
            <textarea
              id="draft-guidance"
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
              placeholder="e.g. Make it shorter, add a code example, write in a friendlier tone."
              rows={3}
              className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-violet-400 focus:outline-none"
            />
            <div className="mt-3 flex justify-end">
              <button
                type="button"
                onClick={handleRefine}
                disabled={anyBusy || !guidance.trim()}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busy === "refine" ? "Refining…" : "Ask agent"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
