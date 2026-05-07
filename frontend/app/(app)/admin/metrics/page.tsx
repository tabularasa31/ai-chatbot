"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type AdminTenantMetricsItem, type AdminMetricsSummary } from "@/lib/api";

export default function AdminMetricsPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState<AdminMetricsSummary | null>(null);
  const [clients, setClients] = useState<AdminTenantMetricsItem[]>([]);
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const tenant = await api.tenants.getMe().catch(() => null);
        if (!tenant) {
          setIsAdmin(false);
          return;
        }
        if (!tenant.is_admin) {
          setIsAdmin(false);
          return;
        }
        setIsAdmin(true);

        const [summaryData, clientsData] = await Promise.all([
          api.admin.getSummary(),
          api.admin.getTenants(),
        ]);
        setSummary(summaryData);
        setClients(clientsData);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "";
        if (msg.includes("403") || msg.includes("Admin only")) {
          setIsAdmin(false);
          return;
        }
        setError(msg || "Failed to load metrics, please try again.");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  useEffect(() => {
    if (loading) return;
    if (isAdmin === false) {
      router.replace("/dashboard");
    }
  }, [loading, isAdmin, router]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-600">Loading…</div>
      </div>
    );
  }

  if (!isAdmin) {
    return null;
  }

  if (error) {
    return (
      <div className="bg-red-50 text-red-700 px-4 py-3 rounded-lg">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold text-slate-800">Admin metrics</h1>

      {/* Summary cards */}
      <section>
        <h2 className="text-lg font-medium text-slate-700 mb-4">Platform summary</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-white rounded-lg shadow p-4 border border-slate-200">
            <div className="text-2xl font-bold text-slate-800">
              {summary?.total_tenants ?? 0}
            </div>
            <div className="text-sm text-slate-600">Total clients</div>
          </div>
          <div className="bg-white rounded-lg shadow p-4 border border-slate-200">
            <div className="text-2xl font-bold text-slate-800">
              {summary?.active_tenants ?? 0}
            </div>
            <div className="text-sm text-slate-600">Active clients</div>
          </div>
          <div className="bg-white rounded-lg shadow p-4 border border-slate-200">
            <div className="text-2xl font-bold text-slate-800">
              {summary?.total_documents ?? 0}
            </div>
            <div className="text-sm text-slate-600">Total documents</div>
          </div>
          <div className="bg-white rounded-lg shadow p-4 border border-slate-200">
            <div className="text-2xl font-bold text-slate-800">
              {summary?.total_chat_sessions ?? 0}
            </div>
            <div className="text-sm text-slate-600">Chat sessions</div>
          </div>
          <div className="bg-white rounded-lg shadow p-4 border border-slate-200">
            <div className="text-2xl font-bold text-slate-800">
              {(summary?.total_messages_user ?? 0) + (summary?.total_messages_assistant ?? 0)}
            </div>
            <div className="text-sm text-slate-600">Total messages</div>
          </div>
          <div className="bg-white rounded-lg shadow p-4 border border-slate-200">
            <div className="text-2xl font-bold text-slate-800">
              {summary?.total_tokens_chat ?? 0}
            </div>
            <div className="text-sm text-slate-600">Tokens (chat)</div>
          </div>
        </div>
      </section>

      {/* Clients table */}
      <section>
        <h2 className="text-lg font-medium text-slate-700 mb-4">Per-client metrics</h2>
        <div className="bg-white rounded-lg shadow border border-slate-200 overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-600 uppercase">
                  Public ID
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-600 uppercase">
                  Email
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-slate-600 uppercase">
                  Users
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-slate-600 uppercase">
                  Docs
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-slate-600 uppercase">
                  Embedded
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-slate-600 uppercase">
                  Sessions
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-slate-600 uppercase">
                  User msgs
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-slate-600 uppercase">
                  Asst msgs
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-slate-600 uppercase">
                  Tokens
                </th>
                <th scope="col" className="px-4 py-3 text-center text-xs font-medium text-slate-600 uppercase">
                  OpenAI key
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {clients.map((c) => (
                <tr key={c.tenant_id} className="hover:bg-slate-50">
                  <td className="px-4 py-3 text-sm text-slate-800 font-medium">{c.public_id}</td>
                  <td className="px-4 py-3 text-sm text-slate-600">{c.owner_email ?? "—"}</td>
                  <td className="px-4 py-3 text-sm text-slate-600 text-right">
                    {c.users_count}
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-600 text-right">
                    {c.documents_count}
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-600 text-right">
                    {c.embedded_documents_count}
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-600 text-right">
                    {c.chat_sessions_count}
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-600 text-right">
                    {c.messages_user_count}
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-600 text-right">
                    {c.messages_assistant_count}
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-600 text-right">
                    {c.tokens_used_chat}
                  </td>
                  <td className="px-4 py-3 text-sm text-center">
                    {c.has_openai_key ? (
                      <span className="text-green-600">Yes</span>
                    ) : (
                      <span className="text-slate-400">No</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {clients.length === 0 && (
            <div className="px-4 py-8 text-center text-slate-500 text-sm">
              No clients yet
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
