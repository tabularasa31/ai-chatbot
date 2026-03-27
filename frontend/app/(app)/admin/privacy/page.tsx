"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type AdminPiiEventItem } from "@/lib/api";

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function csvCell(value: string | number | null | undefined): string {
  const stringValue = value == null ? "" : String(value);
  return `"${stringValue.replaceAll('"', '""')}"`;
}

function buildCsv(events: AdminPiiEventItem[]): string {
  const header = [
    "time",
    "direction",
    "entity_type",
    "count",
    "client_id",
    "actor_user_id",
    "action_path",
    "chat_id",
    "message_id",
  ];
  const rows = events.map((event) =>
    [
      event.created_at,
      event.direction,
      event.entity_type,
      event.count,
      event.client_id,
      event.actor_user_id,
      event.action_path,
      event.chat_id,
      event.message_id,
    ]
      .map((cell) => csvCell(cell))
      .join(",")
  );
  return [header.join(","), ...rows].join("\n");
}

const DIRECTION_OPTIONS = [
  { value: "", label: "All directions" },
  { value: "message_storage", label: "Message storage" },
  { value: "escalation_ticket", label: "Escalation ticket" },
  { value: "original_view", label: "Original view" },
  { value: "original_delete", label: "Original delete" },
];

export default function AdminPrivacyPage() {
  const router = useRouter();
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [events, setEvents] = useState<AdminPiiEventItem[]>([]);
  const [direction, setDirection] = useState("");
  const [sinceDays, setSinceDays] = useState("30");
  const [retentionDays, setRetentionDays] = useState("365");
  const [cleaning, setCleaning] = useState(false);
  const [cleanupMessage, setCleanupMessage] = useState("");
  const [exportMessage, setExportMessage] = useState("");

  const totalCount = useMemo(
    () => events.reduce((sum, event) => sum + event.count, 0),
    [events]
  );

  const load = useCallback(async () => {
    setError("");
    setExportMessage("");
    setLoading(true);
    try {
      const client = await api.clients.getMe().catch(() => null);
      if (!client?.is_admin) {
        setIsAdmin(false);
        return;
      }
      setIsAdmin(true);
      const rows = await api.admin.getPiiEvents({
        direction: direction || undefined,
        sinceDays: Number(sinceDays),
        limit: 100,
      });
      setEvents(rows);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Failed to load privacy log";
      if (message.includes("Admin only")) {
        setIsAdmin(false);
        return;
      }
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [direction, sinceDays]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!loading && isAdmin === false) {
      router.replace("/dashboard");
    }
  }, [isAdmin, loading, router]);

  function handleExportCsv() {
    setExportMessage("");
    if (events.length === 0) {
      setExportMessage("Nothing to export for the current filter.");
      return;
    }
    const csv = buildCsv(events);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const directionLabel = direction || "all";
    link.href = url;
    link.download = `privacy-log-${directionLabel}-${sinceDays}d.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    setExportMessage(`Exported ${events.length} privacy log row(s) as CSV.`);
  }

  async function handleCleanup() {
    setCleaning(true);
    setCleanupMessage("");
    setError("");
    try {
      const result = await api.admin.cleanupPiiEvents(Number(retentionDays));
      setCleanupMessage(`Deleted ${result.deleted_count} old audit event(s).`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to clean up privacy log");
    } finally {
      setCleaning(false);
    }
  }

  if (loading) {
    return <div className="animate-pulse text-slate-500 text-sm">Loading…</div>;
  }

  if (!isAdmin) {
    return null;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Privacy log</h1>
        <p className="text-sm text-slate-500 mt-1">
          Audit trail for redaction, original-content access, and original-content deletion.
        </p>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-6">
        <div className="grid gap-4 md:grid-cols-3">
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
            <div className="text-xs uppercase tracking-wide text-slate-500">Rows loaded</div>
            <div className="mt-2 text-2xl font-semibold text-slate-800">{events.length}</div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
            <div className="text-xs uppercase tracking-wide text-slate-500">Total count</div>
            <div className="mt-2 text-2xl font-semibold text-slate-800">{totalCount}</div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
            <div className="text-xs uppercase tracking-wide text-slate-500">Window</div>
            <div className="mt-2 text-2xl font-semibold text-slate-800">{sinceDays}d</div>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-4">
        <div className="flex flex-wrap gap-3 items-end">
          <label className="text-sm text-slate-600">
            Direction
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value)}
              className="mt-1 block border border-slate-200 rounded-lg px-3 py-2 text-slate-800 bg-white outline-none focus:border-slate-400"
            >
              {DIRECTION_OPTIONS.map((option) => (
                <option key={option.label} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-600">
            Since days
            <input
              type="number"
              min={1}
              value={sinceDays}
              onChange={(e) => setSinceDays(e.target.value)}
              className="mt-1 block border border-slate-200 rounded-lg px-3 py-2 text-slate-800 bg-white outline-none focus:border-slate-400"
            />
          </label>
          <button
            type="button"
            onClick={load}
            className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium hover:bg-violet-700"
          >
            Refresh
          </button>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 text-red-600 text-sm px-3 py-2 border border-red-100">
            {error}
          </div>
        )}
        {cleanupMessage && (
          <div className="rounded-lg bg-emerald-50 text-emerald-700 text-sm px-3 py-2 border border-emerald-100">
            {cleanupMessage}
          </div>
        )}
        {exportMessage && (
          <div className="rounded-lg bg-sky-50 text-sky-700 text-sm px-3 py-2 border border-sky-100">
            {exportMessage}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-slate-500">
            Export uses the same filters and rows currently loaded in the table.
          </p>
          <button
            type="button"
            onClick={handleExportCsv}
            disabled={loading || events.length === 0}
            className="px-4 py-2 rounded-lg border border-slate-200 bg-white text-slate-700 text-sm font-medium hover:bg-slate-50 disabled:opacity-50"
          >
            Export CSV
          </button>
        </div>

        <div className="overflow-x-auto rounded-xl border border-slate-200">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 text-left">
              <tr>
                <th className="px-4 py-3 font-medium">Time</th>
                <th className="px-4 py-3 font-medium">Direction</th>
                <th className="px-4 py-3 font-medium">Entity</th>
                <th className="px-4 py-3 font-medium">Count</th>
                <th className="px-4 py-3 font-medium">Client</th>
                <th className="px-4 py-3 font-medium">Actor</th>
                <th className="px-4 py-3 font-medium">Path</th>
                <th className="px-4 py-3 font-medium">Refs</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {events.map((event) => (
                <tr key={event.id} className="bg-white hover:bg-slate-50">
                  <td className="px-4 py-3 text-slate-700 whitespace-nowrap">{formatDateTime(event.created_at)}</td>
                  <td className="px-4 py-3 text-slate-700">{event.direction}</td>
                  <td className="px-4 py-3 text-slate-700">{event.entity_type}</td>
                  <td className="px-4 py-3 text-slate-700">{event.count}</td>
                  <td className="px-4 py-3 text-slate-500 font-mono text-xs">{event.client_id}</td>
                  <td className="px-4 py-3 text-slate-500 font-mono text-xs">{event.actor_user_id ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-500 font-mono text-xs">{event.action_path ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-500 font-mono text-xs">
                    <div>{event.chat_id ? `chat:${event.chat_id}` : "chat:—"}</div>
                    <div>{event.message_id ? `message:${event.message_id}` : "message:—"}</div>
                  </td>
                </tr>
              ))}
              {events.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-slate-500">
                    No privacy events for this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Retention cleanup</h2>
          <p className="text-sm text-slate-500 mt-1">
            Delete old privacy audit rows while keeping recent access and deletion history.
          </p>
        </div>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="text-sm text-slate-600">
            Retention days
            <input
              type="number"
              min={1}
              value={retentionDays}
              onChange={(e) => setRetentionDays(e.target.value)}
              className="mt-1 block border border-slate-200 rounded-lg px-3 py-2 text-slate-800 bg-white outline-none focus:border-slate-400"
            />
          </label>
          <button
            type="button"
            onClick={handleCleanup}
            disabled={cleaning}
            className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-medium disabled:opacity-50 hover:bg-slate-800"
          >
            {cleaning ? "Cleaning…" : "Run cleanup"}
          </button>
        </div>
      </section>
    </div>
  );
}
