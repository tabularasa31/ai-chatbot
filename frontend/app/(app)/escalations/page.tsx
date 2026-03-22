"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type EscalationTicket } from "@/lib/api";

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });
}

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In progress" },
  { value: "resolved", label: "Resolved" },
];

function priorityClass(p: string): string {
  const map: Record<string, string> = {
    low: "bg-slate-100 text-slate-700",
    medium: "bg-amber-100 text-amber-900",
    high: "bg-orange-100 text-orange-900",
    critical: "bg-red-100 text-red-900",
  };
  return map[p] || "bg-slate-100 text-slate-700";
}

function statusClass(s: string): string {
  const map: Record<string, string> = {
    open: "bg-emerald-50 text-emerald-800",
    in_progress: "bg-blue-50 text-blue-800",
    resolved: "bg-slate-100 text-slate-600",
  };
  return map[s] || "bg-slate-100 text-slate-700";
}

export default function EscalationsPage() {
  const [tickets, setTickets] = useState<EscalationTicket[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const list = await api.escalations.list(
        statusFilter ? { status: statusFilter } : undefined
      );
      setTickets(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tickets");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-slate-800">Escalations</h1>
      <p className="text-slate-500 text-sm max-w-2xl">
        Support tickets created when the bot could not answer, the user asked for a human, or they
        used &quot;Talk to support&quot;. Resolve here when your team has replied by email.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm text-slate-600">
          Status{" "}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="ml-2 border border-slate-200 rounded-lg px-2 py-1 text-slate-800 bg-white outline-none focus:border-slate-400"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value || "all"} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => load()}
          className="text-sm text-violet-600 hover:underline"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="text-red-600 text-sm bg-red-50 border border-red-100 px-3 py-2 rounded-lg">{error}</div>
      )}

      {loading ? (
        <div className="text-slate-500 text-sm">Loading…</div>
      ) : tickets.length === 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-slate-500">
          No tickets for this filter.
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-slate-600 text-left">
                <tr>
                  <th className="px-4 py-3 font-medium">Ticket</th>
                  <th className="px-4 py-3 font-medium">Question</th>
                  <th className="px-4 py-3 font-medium">Priority</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">User</th>
                  <th className="px-4 py-3 font-medium">Created</th>
                </tr>
              </thead>
              <tbody>
                {tickets.map((t) => (
                  <TicketRow
                    key={t.id}
                    ticket={t}
                    expanded={expandedId === t.id}
                    onToggle={() =>
                      setExpandedId((id) => (id === t.id ? null : t.id))
                    }
                    onResolved={load}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function TicketRow({
  ticket,
  expanded,
  onToggle,
  onResolved,
}: {
  ticket: EscalationTicket;
  expanded: boolean;
  onToggle: () => void;
  onResolved: () => void;
}) {
  const [resolution, setResolution] = useState("");
  const [saving, setSaving] = useState(false);
  const [localError, setLocalError] = useState("");

  const userLabel =
    ticket.user_email ||
    ticket.user_name ||
    ticket.user_id ||
    "anonymous";

  const resolve = async () => {
    if (!resolution.trim()) {
      setLocalError("Enter resolution notes.");
      return;
    }
    setLocalError("");
    setSaving(true);
    try {
      await api.escalations.resolve(ticket.id, resolution.trim());
      setResolution("");
      onResolved();
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "Failed to resolve");
    } finally {
      setSaving(false);
    }
  };

  const qPreview =
    ticket.primary_question.length > 72
      ? `${ticket.primary_question.slice(0, 72)}…`
      : ticket.primary_question;

  return (
    <>
      <tr
        className="border-t border-slate-100 hover:bg-slate-50/80 cursor-pointer"
        onClick={onToggle}
      >
        <td className="px-4 py-3 font-mono text-xs text-slate-800">
          {ticket.ticket_number}
        </td>
        <td className="px-4 py-3 text-slate-700 max-w-xs" title={ticket.primary_question}>
          {qPreview}
        </td>
        <td className="px-4 py-3">
          <span
            className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${priorityClass(ticket.priority)}`}
          >
            {ticket.priority}
          </span>
        </td>
        <td className="px-4 py-3">
          <span
            className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${statusClass(ticket.status)}`}
          >
            {ticket.status.replace("_", " ")}
          </span>
        </td>
        <td className="px-4 py-3 text-slate-600 max-w-[140px] truncate" title={userLabel}>
          {userLabel}
        </td>
        <td className="px-4 py-3 text-slate-500 whitespace-nowrap">
          {formatDateTime(ticket.created_at)}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50/50 border-t border-slate-100">
          <td colSpan={6} className="px-4 py-4 text-slate-700" onClick={(e) => e.stopPropagation()}>
            <div className="space-y-3 max-w-3xl">
              <p className="text-xs uppercase tracking-wide text-slate-400">Trigger</p>
              <p className="text-sm">{ticket.trigger}</p>
              <p className="text-xs uppercase tracking-wide text-slate-400">Primary question</p>
              <p className="text-sm whitespace-pre-wrap">{ticket.primary_question}</p>
              {ticket.conversation_summary && (
                <>
                  <p className="text-xs uppercase tracking-wide text-slate-400">
                    Conversation summary
                  </p>
                  <p className="text-sm whitespace-pre-wrap font-mono text-xs bg-white border border-slate-200 rounded p-2">
                    {ticket.conversation_summary}
                  </p>
                </>
              )}
              {ticket.user_note && (
                <>
                  <p className="text-xs uppercase tracking-wide text-slate-400">User note</p>
                  <p className="text-sm whitespace-pre-wrap">{ticket.user_note}</p>
                </>
              )}
              {ticket.retrieved_chunks_preview &&
                ticket.retrieved_chunks_preview.length > 0 && (
                  <>
                    <p className="text-xs uppercase tracking-wide text-slate-400">
                      Retrieved chunks
                    </p>
                    <ul className="text-xs space-y-1 font-mono bg-white border border-slate-200 rounded p-2 max-h-40 overflow-y-auto">
                      {ticket.retrieved_chunks_preview.map((c, i) => (
                        <li key={i}>
                          {(c.document_id as string) ?? "?"} — score{" "}
                          {String(c.score ?? "")}: {(c.preview as string)?.slice(0, 120)}
                          …
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              {ticket.resolution_text && (
                <>
                  <p className="text-xs uppercase tracking-wide text-slate-400">Resolution</p>
                  <p className="text-sm whitespace-pre-wrap">{ticket.resolution_text}</p>
                </>
              )}
              {ticket.status !== "resolved" && (
                <div className="pt-2 space-y-2">
                  <p className="text-xs uppercase tracking-wide text-slate-400">
                    Mark resolved
                  </p>
                  <textarea
                    value={resolution}
                    onChange={(e) => setResolution(e.target.value)}
                    placeholder="What you did / told the user…"
                    rows={3}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-400"
                  />
                  {localError && (
                    <p className="text-red-600 text-xs">{localError}</p>
                  )}
                  <button
                    type="button"
                    onClick={resolve}
                    disabled={saving}
                    className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm hover:bg-violet-700 disabled:opacity-50"
                  >
                    {saving ? "Saving…" : "Mark as resolved"}
                  </button>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
