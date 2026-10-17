"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import type { AdminMetricsSummary, AdminTenantMetricsItem } from "@/lib/api";
import { SkeletonRegion } from "@/components/ui/skeleton";
import { LoadError } from "@/components/ui/load-error";
import { cn } from "@/components/ui/utils";
import { useAdminMetrics, useClientMe } from "@/hooks/useApi";

const SKELETON_ROWS = 5;

type Column = {
  header: string;
  value: (tenant: AdminTenantMetricsItem) => ReactNode;
  numeric?: boolean;
  sticky?: boolean;
  cellClassName?: (tenant: AdminTenantMetricsItem) => string | undefined;
};

const count = (header: string, value: (tenant: AdminTenantMetricsItem) => number): Column => ({
  header,
  numeric: true,
  value: (tenant) => value(tenant).toLocaleString(),
});

const COLUMNS: readonly Column[] = [
  { header: "Public ID", sticky: true, value: (t) => t.public_id, cellClassName: () => "font-mono font-medium text-fg" },
  {
    header: "Owner email",
    value: (t) =>
      t.owner_email ? (
        <span className="block max-w-64 truncate" title={t.owner_email}>
          {t.owner_email}
        </span>
      ) : (
        "—"
      ),
  },
  count("Users", (t) => t.users_count),
  count("Documents", (t) => t.documents_count),
  count("Embedded", (t) => t.embedded_documents_count),
  count("Sessions", (t) => t.chat_sessions_count),
  count("Visitor messages", (t) => t.messages_user_count),
  count("Bot messages", (t) => t.messages_assistant_count),
  count("Tokens", (t) => t.tokens_used_chat),
  {
    header: "OpenAI key",
    value: (t) => (t.has_openai_key ? "Yes" : "No"),
    cellClassName: (t) => (t.has_openai_key ? "text-fg" : undefined),
  },
];

const HEADER_CELL_CLASS = "px-4 py-2 align-bottom text-caption font-medium text-fg-muted";
const CELL_CLASS = "px-4 py-2 text-ui text-fg-muted";
const STICKY_CELL_CLASS = "sticky left-0 bg-canvas";
const STICKY_EDGE_CLASS = "group-data-[scrolled]/scroll:shadow-[inset_-1px_0_0_rgb(var(--c-line))]";

function isForbidden(error: unknown) {
  return error instanceof Error && (error.message.includes("403") || error.message.includes("Admin only"));
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
          <dd className="text-h2 tabular-nums text-fg">{value.toLocaleString()}</dd>
        </div>
      ))}
    </dl>
  );
}

function TenantRow({ tenant }: { tenant: AdminTenantMetricsItem }) {
  return (
    <tr className="group h-10 border-t border-line hover:bg-surface-alt">
      {COLUMNS.map((column) => (
        <td
          key={column.header}
          className={cn(
            CELL_CLASS,
            column.numeric && "text-right tabular-nums",
            column.sticky && cn(STICKY_CELL_CLASS, STICKY_EDGE_CLASS, "group-hover:bg-surface-alt"),
            column.cellClassName?.(tenant),
          )}
        >
          {column.value(tenant)}
        </td>
      ))}
    </tr>
  );
}

function TenantsTable({ tenants }: { tenants: AdminTenantMetricsItem[] }) {
  const [scrolled, setScrolled] = useState(false);
  return (
    <div
      role="region"
      aria-label="Per-tenant metrics"
      tabIndex={0}
      data-scrolled={scrolled || undefined}
      onScroll={(e) => setScrolled(e.currentTarget.scrollLeft > 0)}
      className="group/scroll overflow-x-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
    >
      <table className="min-w-full">
        <thead>
          <tr className="border-b border-line text-left">
            {COLUMNS.map((column) => (
              <th
                key={column.header}
                scope="col"
                className={cn(HEADER_CELL_CLASS, column.numeric && "text-right", column.sticky && cn(STICKY_CELL_CLASS, STICKY_EDGE_CLASS))}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tenants.map((tenant) => (
            <TenantRow key={tenant.tenant_id} tenant={tenant} />
          ))}
          {tenants.length === 0 && (
            <tr className="border-t border-line">
              <td colSpan={COLUMNS.length} className={cn(CELL_CLASS, "h-10")}>
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
    <SkeletonRegion>
      <div className="space-y-8">
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
    </SkeletonRegion>
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
