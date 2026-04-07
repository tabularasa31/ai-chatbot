"use client";

import { useCallback, useEffect, useState } from "react";
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
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${styles[status] ?? styles.dismissed}`}>
      {status}
    </span>
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
  onDismiss,
  onReactivate,
  onDraft,
}: {
  item: GapItem;
  busy: boolean;
  onDismiss: (item: GapItem) => Promise<void>;
  onReactivate: (item: GapItem) => Promise<void>;
  onDraft: (item: GapItem) => Promise<void>;
}) {
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
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onDraft(item)}
            disabled={busy}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Draft
          </button>
          {item.status === "dismissed" ? (
            <button
              type="button"
              onClick={() => onReactivate(item)}
              disabled={busy}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              Reactivate
            </button>
          ) : (
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

      {item.example_questions.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Example questions</p>
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

export default function GapAnalyzerPage() {
  const [data, setData] = useState<GapAnalyzerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<GapDraftResponse | null>(null);
  const [busyItemId, setBusyItemId] = useState<string | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [modeAStatus, setModeAStatus] = useState<GapModeAStatusFilter>("active");
  const [modeBStatus, setModeBStatus] = useState<GapModeBStatusFilter>("active");

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

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Active gaps" value={summary?.total_active ?? "—"} note={summary?.impact_statement} />
        <StatCard label="Uncovered" value={summary?.uncovered_count ?? "—"} />
        <StatCard label="Partial" value={summary?.partial_count ?? "—"} />
        <StatCard label="Last updated" value={summary?.last_updated ? formatDateTime(summary.last_updated) : "—"} />
      </div>

      {draft && <DraftPanel draft={draft} onClose={() => setDraft(null)} />}

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Mode A</h2>
              <p className="text-sm text-slate-500">Docs-side coverage gaps</p>
            </div>
            <select
              value={modeAStatus}
              onChange={(event) => setModeAStatus(event.target.value as GapModeAStatusFilter)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <option value="active">Active</option>
              <option value="dismissed">Dismissed</option>
              <option value="all">All</option>
            </select>
          </div>
          {loading ? (
            <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">Loading Mode A…</div>
          ) : data && data.mode_a_items.length > 0 ? (
            data.mode_a_items.map((item) => (
              <GapCard
                key={item.id}
                item={item}
                busy={busyItemId === item.id}
                onDismiss={handleDismiss}
                onReactivate={handleReactivate}
                onDraft={handleDraft}
              />
            ))
          ) : (
            <div className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
              No Mode A items for the current filter.
            </div>
          )}
        </section>

        <section className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Mode B</h2>
              <p className="text-sm text-slate-500">Real user question clusters</p>
            </div>
            <select
              value={modeBStatus}
              onChange={(event) => setModeBStatus(event.target.value as GapModeBStatusFilter)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <option value="active">Active</option>
              <option value="closed">Closed</option>
              <option value="dismissed">Dismissed</option>
              <option value="all">All</option>
            </select>
          </div>
          {loading ? (
            <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">Loading Mode B…</div>
          ) : data && data.mode_b_items.length > 0 ? (
            data.mode_b_items.map((item) => (
              <GapCard
                key={item.id}
                item={item}
                busy={busyItemId === item.id}
                onDismiss={handleDismiss}
                onReactivate={handleReactivate}
                onDraft={handleDraft}
              />
            ))
          ) : (
            <div className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
              No Mode B items for the current filter.
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
