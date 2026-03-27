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
  const parsedSinceDays = Number(sinceDays);
  const parsedRetentionDays = Number(retentionDays);
  const hasValidSinceDays = Number.isInteger(parsedSinceDays) && parsedSinceDays >= 1;
  const hasValidRetentionDays = Number.isInteger(parsedRetentionDays) && parsedRetentionDays >= 1;

  const totalCount = useMemo(
    () => events.reduce((sum, event) => sum + event.count, 0),
    [events]
  );

  useEffect(() => {
    async function loadAdmin() {
      try {
        const client = await api.clients.getMe().catch(() => null);
        setIsAdmin(Boolean(client?.is_admin));
      } finally {
        setLoading(false);
      }
    }
    void loadAdmin();
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    if (!hasValidSinceDays) {
      setEvents([]);
      setLoading(false);
      return;
    }
    setError("");
    try {
      const rows = await api.admin.getPiiEvents({
        direction: direction || undefined,
        sinceDays: parsedSinceDays,
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
  }, [direction, parsedSinceDays, hasValidSinceDays]);

  useEffect(() => {
    if (isAdmin !== true) return;
    void load();
  }, [isAdmin, load]);

  useEffect(() => {
    if (!loading && isAdmin === false) {
      router.replace("/dashboard");
    }
  }, [isAdmin, loading, router]);

  async function handleCleanup() {
    if (!hasValidRetentionDays) {
      setError("Retention days must be a whole number greater than 0.");
      return;
    }
    if (!window.confirm(`Delete privacy audit rows older than ${parsedRetentionDays} days? This cannot be undone.`)) {
      return;
    }
    setCleaning(true);
    setCleanupMessage("");
    setError("");
    try {
      const result = await api.admin.cleanupPiiEvents(parsedRetentionDays);
      setCleanupMessage(`Deleted ${result.deleted_count} old audit event(s).`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to clean up privacy log");
    } finally {
      setCleaning(false);
    }
  }

  function handleResetFilters() {
    setDirection("");
    setSinceDays("30");
    setError("");
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
                <option key={option.value || "all"} value={option.value}>
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
            disabled={!hasValidSinceDays}
            className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium hover:bg-violet-700"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={handleResetFilters}
            className="px-4 py-2 rounded-lg border border-slate-200 bg-white text-slate-700 text-sm font-medium hover:bg-slate-50"
          >
            Reset filters
          </button>
        </div>
        {!hasValidSinceDays && (
          <div className="rounded-lg bg-amber-50 text-amber-800 text-sm px-3 py-2 border border-amber-100">
            Since days must be a whole number greater than 0.
          </div>
        )}

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

        <div className="rounded-lg bg-slate-50 text-slate-600 text-sm px-3 py-2 border border-slate-200">
          Showing the latest 100 events for the current filter.
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
                </tr>
              ))}
              {events.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-slate-500">
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
            disabled={cleaning || !hasValidRetentionDays}
            className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-medium disabled:opacity-50 hover:bg-slate-800"
          >
            {cleaning ? "Cleaning…" : "Run cleanup"}
          </button>
        </div>
        {!hasValidRetentionDays && (
          <div className="rounded-lg bg-amber-50 text-amber-800 text-sm px-3 py-2 border border-amber-100">
            Retention days must be a whole number greater than 0.
          </div>
        )}
      </section>
    </div>
  );
}
