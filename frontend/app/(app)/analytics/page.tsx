"use client";

import { Suspense, useCallback, useEffect, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { AnalyticsPeriod } from "@/lib/api";
import { useAnalyticsSummary } from "@/hooks/useApi";
import { Alert } from "@/components/ui/alert";
import { StatCard } from "@/components/ui/stat-card";

const PERIODS: { value: AnalyticsPeriod; label: string }[] = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
];

const DEFAULT_PERIOD: AnalyticsPeriod = "30d";

function isAnalyticsPeriod(value: string | null): value is AnalyticsPeriod {
  return value === "7d" || value === "30d" || value === "90d";
}

function formatPercent(rate: number | null): string {
  if (rate == null) return "—";
  return `${Math.round(rate * 100)}%`;
}

function formatCount(value: number | undefined): string {
  return value == null ? "—" : value.toLocaleString();
}

function PeriodSwitcher({
  period,
  onChange,
}: {
  period: AnalyticsPeriod;
  onChange: (period: AnalyticsPeriod) => void;
}) {
  return (
    <div className="flex rounded-lg border border-slate-200 bg-white p-0.5 text-sm" role="group" aria-label="Period">
      {PERIODS.map((p) => (
        <button
          key={p.value}
          type="button"
          onClick={() => onChange(p.value)}
          aria-pressed={period === p.value}
          className={`rounded-md px-3 py-1 ${
            period === p.value ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-50"
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}

function AnalyticsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const periodParam = searchParams.get("period");
  const period: AnalyticsPeriod = isAnalyticsPeriod(periodParam) ? periodParam : DEFAULT_PERIOD;

  const { data, error, isLoading } = useAnalyticsSummary(period);

  useEffect(() => {
    if (periodParam !== null && !isAnalyticsPeriod(periodParam)) {
      router.replace(`/analytics?period=${DEFAULT_PERIOD}`);
    }
    // Normalize an invalid `period` query param once on mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setPeriod = useCallback(
    (next: AnalyticsPeriod) => {
      router.replace(`/analytics?period=${next}`);
    },
    [router]
  );

  const cards = useMemo(
    () => [
      { label: "Messages", value: formatCount(data?.messages) },
      { label: "Conversations", value: formatCount(data?.conversations) },
      { label: "Deflection Rate", value: formatPercent(data?.deflection_rate ?? null) },
      { label: "Answered Rate", value: formatPercent(data?.answered_rate ?? null) },
      { label: "Filtered", value: formatCount(data?.filtered) },
    ],
    [data]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-800">Analytics</h1>
          <p className="text-slate-500 text-sm mt-1">Traffic and outcome numbers for the selected period.</p>
        </div>
        <div className="flex items-center gap-2">
          {isLoading && data && (
            <span className="text-xs text-slate-400" role="status">
              Updating…
            </span>
          )}
          <PeriodSwitcher period={period} onChange={setPeriod} />
        </div>
      </div>

      {error && (
        <Alert tone="error" variant="soft">
          {error instanceof Error ? error.message : "Failed to load analytics"}
        </Alert>
      )}

      {isLoading && !data ? (
        <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
          Loading analytics…
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {cards.map((card) => (
            <StatCard key={card.label} label={card.label} value={card.value} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function AnalyticsPage() {
  return (
    <Suspense fallback={<div className="text-slate-500">Loading…</div>}>
      <AnalyticsPageContent />
    </Suspense>
  );
}
