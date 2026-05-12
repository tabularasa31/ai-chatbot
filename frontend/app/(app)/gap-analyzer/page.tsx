"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type GapAnalyzerResponse,
  type GapDraftResponse,
  type GapItem,
  type GapModeAStatusFilter,
  type GapModeBStatusFilter,
} from "@/lib/api";

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function CoverageBadge({ item }: { item: GapItem }) {
  const styles: Record<string, string> = {
    uncovered: "bg-rose-100 text-rose-700",
    partial: "bg-amber-100 text-amber-700",
    covered: "bg-emerald-100 text-emerald-700",
    unknown: "bg-slate-100 text-slate-600",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${styles[item.classification] ?? styles.unknown}`}>
      {item.classification}
    </span>
  );
}

function StatusBadge({ status }: { status: GapItem["status"] }) {
  const styles: Record<string, string> = {
    active: "bg-violet-100 text-violet-700",
    closed: "bg-emerald-100 text-emerald-700",
    dismissed: "bg-slate-200 text-slate-700",
    inactive: "bg-slate-100 text-slate-500",
    drafting: "bg-amber-100 text-amber-700",
    in_review: "bg-indigo-100 text-indigo-700",
    resolved: "bg-emerald-100 text-emerald-700",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${styles[status] ?? styles.dismissed}`}>
      {status.replace("_", " ")}
    </span>
  );
}

function LinkedContextPanel({ item }: { item: GapItem }) {
  if (!item.linked_source || !item.linked_label) return null;
  return (
    <div className="mt-4 rounded-xl border border-sky-200 bg-sky-50/80 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-sky-700">
          Linked docs gap
        </span>
        <span className="text-sm font-medium text-slate-900">{item.linked_label}</span>
      </div>
      {item.linked_example_questions.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm text-slate-700">
          {item.linked_example_questions.slice(0, 3).map((question, index) => (
            <li key={`${item.id}:linked:${index}:${question}`} className="rounded-lg bg-white/80 px-3 py-2">
              {question}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  note,
}: {
  label: string;
  value: string | number;
  note?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-900">{value}</p>
      {note && <p className="mt-1 text-xs text-slate-500">{note}</p>}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
      {message}
    </div>
  );
}

function DraftPanel({
  draft,
  onClose,
}: {
  draft: GapDraftResponse;
  onClose: () => void;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-slate-900">{draft.title}</p>
          <p className="text-xs text-slate-500">Transient draft preview</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => navigator.clipboard.writeText(draft.markdown).catch(() => {})}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          >
            Copy
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          >
            Close
          </button>
        </div>
      </div>
      <pre className="overflow-x-auto rounded-lg bg-slate-950 p-4 text-xs leading-6 text-slate-100">
        {draft.markdown}
      </pre>
    </div>
  );
}

function GapCard({
  item,
  busy,
  hasOpenAiKey,
  onDismiss,
  onReactivate,
  onDraft,
  onGenerateModeBDraft,
}: {
  item: GapItem;
  busy: boolean;
  hasOpenAiKey: boolean;
  onDismiss: (item: GapItem) => Promise<void>;
  onReactivate: (item: GapItem) => Promise<void>;
  onDraft: (item: GapItem) => Promise<void>;
  onGenerateModeBDraft: (item: GapItem) => Promise<void>;
}) {
  const isModeB = item.source === "mode_b";
  const showGenerate = isModeB && item.status === "active";
  const showContinueDraft = isModeB && (item.status === "in_review" || item.status === "drafting");
  const showResolved = isModeB && item.status === "resolved";
  const generateDisabled = busy || !hasOpenAiKey;
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={item.status} />
            <CoverageBadge item={item} />
            {item.is_new && (
              <span className="rounded-full bg-fuchsia-100 px-2 py-0.5 text-xs font-medium text-fuchsia-700">
                new
              </span>
            )}
            {item.also_missing_in_docs && (
              <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-700">
                also missing in docs
              </span>
            )}
            <span className="text-xs uppercase tracking-wide text-slate-400">{item.source === "mode_a" ? "Mode A" : "Mode B"}</span>
          </div>
          <div>
            <h3 className="text-base font-semibold text-slate-900">{item.label}</h3>
            <p className="mt-1 text-xs text-slate-500">
              Coverage: {item.coverage_score == null ? "—" : item.coverage_score.toFixed(2)} · Questions: {item.question_count}
              {item.aggregate_signal_weight != null && ` · Signal: ${item.aggregate_signal_weight.toFixed(1)}`}
            </p>
            {showResolved && (
              <p className="mt-2 text-xs">
                <Link
                  href="/knowledge?tab=faq"
                  className="text-emerald-700 underline decoration-emerald-300 underline-offset-2 hover:text-emerald-800"
                >
                  View published FAQ →
                </Link>
              </p>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {showGenerate && (
            <button
              type="button"
              onClick={() => onGenerateModeBDraft(item)}
              disabled={generateDisabled}
              title={hasOpenAiKey ? undefined : "Connect your OpenAI API key in Settings to use this feature"}
              className="rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
            >
              Generate FAQ draft
            </button>
          )}
          {showContinueDraft && (
            <Link
              href={`/gap-analyzer/mode_b/${item.id}/draft`}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
            >
              Continue draft
            </Link>
          )}
          {!isModeB && (
            <button
              type="button"
              onClick={() => onDraft(item)}
              disabled={busy}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Draft
            </button>
          )}
          {item.status === "dismissed" || item.status === "inactive" ? (
            <button
              type="button"
              onClick={() => onReactivate(item)}
              disabled={busy}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              Reactivate
            </button>
          ) : item.status === "resolved" ? null : (
            <button
              type="button"
              onClick={() => onDismiss(item)}
              disabled={busy}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              Dismiss
            </button>
          )}
        </div>
      </div>

      <LinkedContextPanel item={item} />

      {item.example_questions.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
            {item.linked_source === "mode_a" ? "User signal questions" : "Example questions"}
          </p>
          <ul className="space-y-1 text-sm text-slate-700">
            {item.example_questions.map((question, index) => (
              <li key={`${item.id}:${index}:${question}`} className="rounded-lg bg-slate-50 px-3 py-2">
                {question}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-4 text-xs text-slate-400">Last updated: {formatDateTime(item.last_updated)}</p>
    </div>
  );
}

function GapCardList({
  items,
  busyItemId,
  hasOpenAiKey,
  onDismiss,
  onReactivate,
  onDraft,
  onGenerateModeBDraft,
}: {
  items: GapItem[];
  busyItemId: string | null;
  hasOpenAiKey: boolean;
  onDismiss: (item: GapItem) => Promise<void>;
  onReactivate: (item: GapItem) => Promise<void>;
  onDraft: (item: GapItem) => Promise<void>;
  onGenerateModeBDraft: (item: GapItem) => Promise<void>;
}) {
  return (
    <>
      {items.map((item) => (
        <GapCard
          key={item.id}
          item={item}
          busy={busyItemId === item.id}
          hasOpenAiKey={hasOpenAiKey}
          onDismiss={onDismiss}
          onReactivate={onReactivate}
          onDraft={onDraft}
          onGenerateModeBDraft={onGenerateModeBDraft}
        />
      ))}
    </>
  );
}

function ArchiveGroup({
  title,
  note,
  items,
  emptyMessage,
  busyItemId,
  hasOpenAiKey,
  onDismiss,
  onReactivate,
  onDraft,
  onGenerateModeBDraft,
}: {
  title: string;
  note: string;
  items: GapItem[];
  emptyMessage: string;
  busyItemId: string | null;
  hasOpenAiKey: boolean;
  onDismiss: (item: GapItem) => Promise<void>;
  onReactivate: (item: GapItem) => Promise<void>;
  onDraft: (item: GapItem) => Promise<void>;
  onGenerateModeBDraft: (item: GapItem) => Promise<void>;
}) {
  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        <p className="text-xs text-slate-500">{note}</p>
      </div>
      {items.length > 0 ? (
        <GapCardList
          items={items}
          busyItemId={busyItemId}
          hasOpenAiKey={hasOpenAiKey}
          onDismiss={onDismiss}
          onReactivate={onReactivate}
          onDraft={onDraft}
          onGenerateModeBDraft={onGenerateModeBDraft}
        />
      ) : (
        <EmptyState message={emptyMessage} />
      )}
    </div>
  );
}

export default function GapAnalyzerPage() {
  const router = useRouter();
  const [data, setData] = useState<GapAnalyzerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<GapDraftResponse | null>(null);
  const [busyItemId, setBusyItemId] = useState<string | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [modeAStatus, setModeAStatus] = useState<GapModeAStatusFilter>("active");
  const [modeBStatus, setModeBStatus] = useState<GapModeBStatusFilter>("active");
  const [hasOpenAiKey, setHasOpenAiKey] = useState(true);

  useEffect(() => {
    api.tenants
      .getMe()
      .then((tenant) => setHasOpenAiKey(Boolean(tenant.has_openai_key)))
      .catch(() => setHasOpenAiKey(false));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await api.gapAnalyzer.get({
        modeAStatus,
        modeBStatus,
        modeASort: "coverage_asc",
        modeBSort: "signal_desc",
      });
      setData(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Gap Analyzer");
    } finally {
      setLoading(false);
    }
  }, [modeAStatus, modeBStatus]);

  useEffect(() => {
    load();
  }, [load]);

  const handleDismiss = useCallback(async (item: GapItem) => {
    setBusyItemId(item.id);
    setNotice("");
    try {
      await api.gapAnalyzer.dismiss(item.source, item.id);
      setNotice("Gap dismissed.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to dismiss gap");
    } finally {
      setBusyItemId(null);
    }
  }, [load]);

  const handleReactivate = useCallback(async (item: GapItem) => {
    setBusyItemId(item.id);
    setNotice("");
    try {
      await api.gapAnalyzer.reactivate(item.source, item.id);
      setNotice("Gap reactivated.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reactivate gap");
    } finally {
      setBusyItemId(null);
    }
  }, [load]);

  const handleDraft = useCallback(async (item: GapItem) => {
    setBusyItemId(item.id);
    setError("");
    try {
      setDraft(await api.gapAnalyzer.draft(item.source, item.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate draft");
    } finally {
      setBusyItemId(null);
    }
  }, []);

  const handleGenerateModeBDraft = useCallback(
    async (item: GapItem) => {
      setBusyItemId(item.id);
      setError("");
      try {
        await api.gapAnalyzer.generateModeBDraft(item.id);
        router.push(`/gap-analyzer/mode_b/${item.id}/draft`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to generate FAQ draft");
        setBusyItemId(null);
      }
    },
    [router],
  );

  const handleRecalculate = useCallback(async () => {
    setRecalculating(true);
    setNotice("");
    setError("");
    try {
      await api.gapAnalyzer.recalculate("both");
      setNotice("Recalculation accepted and queued.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start recalculation");
    } finally {
      setRecalculating(false);
    }
  }, []);

  const summary = data?.summary;
  const archiveModeSelected = modeAStatus === "archived" && modeBStatus === "archived";
  const archiveItems = useMemo(
    () => [...(data?.mode_a_items ?? []), ...(data?.mode_b_items ?? [])],
    [data?.mode_a_items, data?.mode_b_items],
  );
  const archiveModeAItems = useMemo(
    () => (data?.mode_a_items ?? []).filter((item) => item.status === "dismissed"),
    [data?.mode_a_items],
  );
  const archiveModeBClosedItems = useMemo(
    () => (data?.mode_b_items ?? []).filter((item) => item.status === "closed"),
    [data?.mode_b_items],
  );
  const archiveModeBDismissedItems = useMemo(
    () => (data?.mode_b_items ?? []).filter((item) => item.status === "dismissed"),
    [data?.mode_b_items],
  );
  const archiveModeBInactiveItems = useMemo(
    () => (data?.mode_b_items ?? []).filter((item) => item.status === "inactive"),
    [data?.mode_b_items],
  );
  const archiveModeADismissedCount = archiveModeAItems.length;
  const archiveClosedCount = useMemo(
    () => archiveItems.filter((item) => item.status === "closed").length,
    [archiveItems],
  );
  const archiveDismissedCount = useMemo(
    () => archiveItems.filter((item) => item.status === "dismissed").length,
    [archiveItems],
  );
  const archiveInactiveCount = useMemo(
    () => archiveItems.filter((item) => item.status === "inactive").length,
    [archiveItems],
  );

  const activateActiveView = () => {
    setModeAStatus("active");
    setModeBStatus("active");
  };

  const activateArchiveView = () => {
    setModeAStatus("archived");
    setModeBStatus("archived");
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Gap Analyzer</h1>
          <p className="mt-1 text-sm text-slate-500">
            Docs-side and user-signal gaps in one operational view.
          </p>
        </div>
        <button
          type="button"
          onClick={handleRecalculate}
          disabled={recalculating}
          className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {recalculating ? "Starting…" : "Recalculate now"}
        </button>
      </div>

      {error && <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
      {notice && <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{notice}</div>}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={activateActiveView}
          className={`rounded-full px-3 py-1.5 text-sm font-medium ${
            !archiveModeSelected ? "bg-slate-900 text-white" : "border border-slate-200 bg-white text-slate-700"
          }`}
        >
          Active view
        </button>
        <button
          type="button"
          onClick={activateArchiveView}
          className={`rounded-full px-3 py-1.5 text-sm font-medium ${
            archiveModeSelected ? "bg-slate-900 text-white" : "border border-slate-200 bg-white text-slate-700"
          }`}
        >
          Archive view
        </button>
      </div>

      {archiveModeSelected && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          Archive keeps docs-side dismissals separate from Mode B closed and dismissed clusters, so lifecycle history stays source-specific.
        </div>
      )}

      <div className={`grid gap-4 ${archiveModeSelected ? "md:grid-cols-5" : "md:grid-cols-4"}`}>
        {archiveModeSelected ? (
          <>
            <StatCard label="Archived total" value={archiveItems.length} note="Source-specific archive inventory." />
            <StatCard label="Mode A dismissed" value={archiveModeADismissedCount} />
            <StatCard label="Mode B closed" value={archiveClosedCount} />
            <StatCard label="Mode B dismissed" value={archiveDismissedCount} />
            <StatCard label="Mode B inactive" value={archiveInactiveCount} />
          </>
        ) : (
          <>
            <StatCard label="Active gaps" value={summary?.total_active ?? "—"} note={summary?.impact_statement} />
            <StatCard label="Uncovered" value={summary?.uncovered_count ?? "—"} />
            <StatCard label="Partial" value={summary?.partial_count ?? "—"} />
            <StatCard label="Last updated" value={summary?.last_updated ? formatDateTime(summary.last_updated) : "—"} />
          </>
        )}
      </div>

      {draft && <DraftPanel draft={draft} onClose={() => setDraft(null)} />}

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Mode A</h2>
              <p className="text-sm text-slate-500">
                {archiveModeSelected ? "Dismissed docs-side topics" : "Docs-side coverage gaps"}
              </p>
            </div>
            <select
              value={modeAStatus}
              onChange={(event) => setModeAStatus(event.target.value as GapModeAStatusFilter)}
              aria-label="Mode A status filter"
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="dismissed">Dismissed only</option>
              <option value="all">All</option>
            </select>
          </div>
          {loading ? (
            <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">Loading Mode A…</div>
          ) : archiveModeSelected && modeAStatus === "archived" ? (
            <ArchiveGroup
              title="Dismissed Mode A topics"
              note="Docs-side candidates that were explicitly hidden from the active backlog."
              items={archiveModeAItems}
              emptyMessage="No dismissed Mode A topics in archive."
              busyItemId={busyItemId}
              onDismiss={handleDismiss}
              onReactivate={handleReactivate}
              onDraft={handleDraft}
              hasOpenAiKey={hasOpenAiKey}
              onGenerateModeBDraft={handleGenerateModeBDraft}
            />
          ) : data && data.mode_a_items.length > 0 ? (
            <GapCardList
              items={data.mode_a_items}
              busyItemId={busyItemId}
              onDismiss={handleDismiss}
              onReactivate={handleReactivate}
              onDraft={handleDraft}
              hasOpenAiKey={hasOpenAiKey}
              onGenerateModeBDraft={handleGenerateModeBDraft}
            />
          ) : (
            <EmptyState message="No Mode A items for the current filter." />
          )}
        </section>

        <section className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Mode B</h2>
              <p className="text-sm text-slate-500">
                {archiveModeSelected ? "Closed and dismissed user-question clusters" : "Real user question clusters"}
              </p>
            </div>
            <select
              value={modeBStatus}
              onChange={(event) => setModeBStatus(event.target.value as GapModeBStatusFilter)}
              aria-label="Mode B status filter"
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="closed">Closed</option>
              <option value="dismissed">Dismissed</option>
              <option value="inactive">Inactive</option>
              <option value="all">All</option>
            </select>
          </div>
          {loading ? (
            <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">Loading Mode B…</div>
          ) : archiveModeSelected && modeBStatus === "archived" ? (
            <div className="space-y-5">
              <ArchiveGroup
                title="Closed Mode B clusters"
                note="Resolved or sufficiently covered user-question clusters that remain visible in archive."
                items={archiveModeBClosedItems}
                emptyMessage="No closed Mode B clusters in archive."
                busyItemId={busyItemId}
                onDismiss={handleDismiss}
                onReactivate={handleReactivate}
                onDraft={handleDraft}
                hasOpenAiKey={hasOpenAiKey}
                onGenerateModeBDraft={handleGenerateModeBDraft}
              />
              <ArchiveGroup
                title="Dismissed Mode B clusters"
                note="Clusters explicitly hidden from the active backlog by an operator action."
                items={archiveModeBDismissedItems}
                emptyMessage="No dismissed Mode B clusters in archive."
                busyItemId={busyItemId}
                onDismiss={handleDismiss}
                onReactivate={handleReactivate}
                onDraft={handleDraft}
                hasOpenAiKey={hasOpenAiKey}
                onGenerateModeBDraft={handleGenerateModeBDraft}
              />
              <ArchiveGroup
                title="Inactive Mode B clusters"
                note="Older archived clusters that aged out of the main archive buckets but remain reviewable."
                items={archiveModeBInactiveItems}
                emptyMessage="No inactive Mode B clusters in archive."
                busyItemId={busyItemId}
                onDismiss={handleDismiss}
                onReactivate={handleReactivate}
                onDraft={handleDraft}
                hasOpenAiKey={hasOpenAiKey}
                onGenerateModeBDraft={handleGenerateModeBDraft}
              />
            </div>
          ) : data && data.mode_b_items.length > 0 ? (
            <GapCardList
              items={data.mode_b_items}
              busyItemId={busyItemId}
              onDismiss={handleDismiss}
              onReactivate={handleReactivate}
              onDraft={handleDraft}
              hasOpenAiKey={hasOpenAiKey}
              onGenerateModeBDraft={handleGenerateModeBDraft}
            />
          ) : (
            <EmptyState message="No Mode B items for the current filter." />
          )}
        </section>
      </div>
    </div>
  );
}
