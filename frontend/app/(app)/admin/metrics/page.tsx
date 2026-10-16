"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import type { AdminMetricsSummary, AdminTenantMetricsItem } from "@/lib/api";
import { LoadError } from "@/components/ui/load-error";
import { useAdminMetrics, useClientMe } from "@/hooks/useApi";

const SKELETON_ROWS = 5;
const COLUMN_COUNT = 10;

const NUMERIC_COLUMNS = [
  "Users",
  "Documents",
  "Embedded",
  "Sessions",
  "Visitor messages",
  "Bot messages",
  "Tokens",
] as const;

const HEADER_CELL_CLASS = "px-4 py-2 align-bottom text-caption font-medium text-fg-muted";
const CELL_CLASS = "px-4 py-2 text-ui text-fg-muted";
const STICKY_CELL_CLASS = "sticky left-0 bg-canvas";

function isForbidden(error: unknown) {
  return error instanceof Error && (error.message.includes("403") || error.message.includes("Admin only"));
}

function formatCount(value: number) {
  return value.toLocaleString();
}

function Totals({ summary }: { summary: AdminMetricsSummary }) {
  const totals = [
    { label: "Tenants", value: summary.total_tenants },
    { label: "Active tenants", value: summary.active_tenants },
    { label: "Documents", value: summary.total_documents },
    { label: "Chat sessions", value: summary.total_chat_sessions },
    { label: "Messages", value: summary.total_messages_user + summary.total_messages_assistant },
    { label: "Chat tokens", value: summary.total_tokens_chat },
  ];
  return (
    <dl className="grid grid-cols-3 gap-6 lg:grid-cols-6">
      {totals.map(({ label, value }) => (
        <div key={label}>
          <dt className="text-caption font-medium text-fg-muted">{label}</dt>
          <dd className="text-h2 tabular-nums text-fg">{formatCount(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function TenantRow({ tenant }: { tenant: AdminTenantMetricsItem }) {
  const counts = [
    tenant.users_count,
    tenant.documents_count,
    tenant.embedded_documents_count,
    tenant.chat_sessions_count,
    tenant.messages_user_count,
    tenant.messages_assistant_count,
    tenant.tokens_used_chat,
  ];
  return (
    <tr className="group h-10 border-t border-line hover:bg-surface-alt">
      <td className={`${CELL_CLASS} ${STICKY_CELL_CLASS} font-mono font-medium text-fg group-hover:bg-surface-alt`}>
        {tenant.public_id}
      </td>
      <td className={CELL_CLASS}>
        {tenant.owner_email ? (
          <span className="block max-w-64 truncate" title={tenant.owner_email}>
            {tenant.owner_email}
          </span>
        ) : (
          "—"
        )}
      </td>
      {counts.map((count, index) => (
        <td key={NUMERIC_COLUMNS[index]} className={`${CELL_CLASS} text-right tabular-nums`}>
          {formatCount(count)}
        </td>
      ))}
      <td className={`${CELL_CLASS} ${tenant.has_openai_key ? "text-fg" : ""}`}>
        {tenant.has_openai_key ? "Yes" : "No"}
      </td>
    </tr>
  );
}

function TenantsTable({ tenants }: { tenants: AdminTenantMetricsItem[] }) {
  return (
    <div role="region" aria-label="Per-tenant metrics" className="overflow-x-auto">
      <table className="min-w-full">
        <thead>
          <tr className="border-b border-line text-left">
            <th scope="col" className={`${HEADER_CELL_CLASS} ${STICKY_CELL_CLASS}`}>
              Public ID
            </th>
            <th scope="col" className={HEADER_CELL_CLASS}>
              Owner email
            </th>
            {NUMERIC_COLUMNS.map((column) => (
              <th key={column} scope="col" className={`${HEADER_CELL_CLASS} text-right`}>
                {column}
              </th>
            ))}
            <th scope="col" className={HEADER_CELL_CLASS}>
              OpenAI key
            </th>
          </tr>
        </thead>
        <tbody>
          {tenants.map((tenant) => (
            <TenantRow key={tenant.tenant_id} tenant={tenant} />
          ))}
          {tenants.length === 0 && (
            <tr className="border-t border-line">
              <td colSpan={COLUMN_COUNT} className={`${CELL_CLASS} h-10`}>
                No tenants yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function MetricsSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="space-y-8">
      <div className="grid grid-cols-3 gap-6 lg:grid-cols-6">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="h-12 rounded-tag bg-surface-alt" />
        ))}
      </div>
      <div className="space-y-2">
        {Array.from({ length: SKELETON_ROWS }, (_, index) => (
          <div key={index} className="h-10 rounded-tag bg-surface-alt" />
        ))}
      </div>
    </div>
  );
}

export default function AdminMetricsPage() {
  const router = useRouter();
  const { data: client, isLoading: clientLoading } = useClientMe();
  const isAdmin = clientLoading ? null : client?.is_admin ?? false;
  const { data, error, isLoading, mutate } = useAdminMetrics(isAdmin === true);
  const forbidden = isForbidden(error);

  useEffect(() => {
    if (isAdmin === false || forbidden) {
      router.replace("/dashboard");
    }
  }, [isAdmin, forbidden, router]);

  if (!isAdmin || forbidden) {
    return null;
  }

  return (
    <div className="space-y-8" aria-busy={isLoading}>
      <h1 className="text-h1 text-fg">Platform metrics</h1>
      {error && <LoadError message="Couldn't load platform metrics." onRetry={() => mutate()} />}
      {isLoading && <MetricsSkeleton />}
      {data && (
        <>
          <section className="space-y-4">
            <h2 className="text-h2 text-fg">Totals</h2>
            <Totals summary={data.summary} />
          </section>
          <section className="space-y-4">
            <h2 className="text-h2 text-fg">Per-tenant metrics</h2>
            <TenantsTable tenants={data.tenants} />
          </section>
        </>
      )}
    </div>
  );
}
