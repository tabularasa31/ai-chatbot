"use client";

import { Suspense, useCallback, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";
import type { AnalyticsPeriod, AnalyticsSummaryResponse } from "@/lib/api";
import { useAnalyticsSummary } from "@/hooks/useApi";
import { buttonClasses } from "@/components/ui/button";
import { DeferredSkeleton } from "@/components/ui/deferred-skeleton";
import { LoadError } from "@/components/ui/load-error";
import { SectionTabs, sectionTabId } from "@/components/ui/section-tabs";

const PERIODS: { id: AnalyticsPeriod; label: string }[] = [
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "90d", label: "90 days" },
];

const PERIOD_DAYS: Record<AnalyticsPeriod, number> = { "7d": 7, "30d": 30, "90d": 90 };

const DEFAULT_PERIOD: AnalyticsPeriod = "30d";
const PANEL_ID = "analytics-panel";
const NO_VALUE = "—";
const SKELETON_ROWS = 5;

const TEXT_LINK_CLASS =
  "mt-1 inline-flex items-center gap-1 rounded-tag text-ui text-brand hover:underline " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";

type MetricRow = {
  label: string;
  definition: string;
  detail?: string;
  value: string;
  link?: { href: string; label: string };
};

function isAnalyticsPeriod(value: string | null): value is AnalyticsPeriod {
  return value === "7d" || value === "30d" || value === "90d";
}

function formatPercent(rate: number | null): string {
  return rate == null ? NO_VALUE : `${Math.round(rate * 100)}%`;
}

function metricRows(data: AnalyticsSummaryResponse): MetricRow[] {
  const escalated =
    data.deflection_rate == null ? null : Math.round(data.conversations * (1 - data.deflection_rate));
  return [
    {
      label: "Deflection rate",
      definition: "Conversations that did not create an escalation ticket.",
      detail:
        escalated == null
          ? undefined
          : `${escalated.toLocaleString()} of ${data.conversations.toLocaleString()} conversations were escalated.`,
      value: formatPercent(data.deflection_rate),
      link: { href: "/inbox?scope=all", label: "Open Inbox" },
    },
    {
      label: "Answered rate",
      definition: "Bot replies that used your documents, out of all replies except blocked and small-talk ones.",
      detail: "Unanswered questions show up in Doc gaps.",
      value: formatPercent(data.answered_rate),
      link: { href: "/gap-analyzer", label: "Open Doc gaps" },
    },
    {
      label: "Conversations",
      definition: "Conversations with at least one message in this period.",
      value: data.conversations.toLocaleString(),
    },
    {
      label: "Messages",
      definition: "Messages sent by visitors.",
      value: data.messages.toLocaleString(),
    },
    {
      label: "Filtered",
      definition: "Messages stopped by the safety and relevance checks before the bot answered.",
      value: data.filtered.toLocaleString(),
    },
  ];
}

function MetricList({ rows }: { rows: MetricRow[] }) {
  return (
    <dl className="max-w-3xl">
      {rows.map((row) => (
        <div
          key={row.label}
          className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-t border-line py-4 first:border-t-0"
        >
          <dt className="w-48 shrink-0 text-ui font-medium text-fg">{row.label}</dt>
          <dd className="min-w-0 flex-1 basis-64 text-ui text-fg-muted">
            <p className="max-w-prose">{row.definition}</p>
            {row.detail && <p className="max-w-prose">{row.detail}</p>}
            {row.link && (
              <Link href={row.link.href} className={TEXT_LINK_CLASS}>
                {row.link.label}
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
            )}
          </dd>
          <dd
            aria-label={row.value === NO_VALUE ? "No data" : undefined}
            className="min-w-24 text-right text-h2 tabular-nums text-fg"
          >
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function MetricListSkeleton() {
  return (
    <DeferredSkeleton>
      <div className="max-w-3xl">
        {Array.from({ length: SKELETON_ROWS }, (_, index) => (
          <div key={index} className="flex items-center gap-4 border-t border-line py-4 first:border-t-0">
            <div className="h-5 w-48 rounded-tag bg-surface-alt" />
            <div className="h-5 flex-1 rounded-tag bg-surface-alt" />
            <div className="h-7 w-24 rounded-tag bg-surface-alt" />
          </div>
        ))}
      </div>
    </DeferredSkeleton>
  );
}

function EmptyPeriod({ period }: { period: AnalyticsPeriod }) {
  return (
    <div className="space-y-4">
      <p className="text-ui text-fg-muted">No conversations in the last {PERIOD_DAYS[period]} days.</p>
      <Link href="/dashboard" className={buttonClasses({ variant: "secondary" })}>
        Install widget
      </Link>
    </div>
  );
}

function AnalyticsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const periodParam = searchParams.get("period");
  const period: AnalyticsPeriod = isAnalyticsPeriod(periodParam) ? periodParam : DEFAULT_PERIOD;

  const { data, error, isLoading, isValidating, mutate } = useAnalyticsSummary(period);

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

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-h1 text-fg">Analytics</h1>
        <div className="flex items-center gap-2">
          {isValidating && data && data.period !== period && (
            <span role="status">
              <Loader2 size={16} aria-hidden="true" className="animate-spin text-fg-muted" />
              <span className="sr-only">Updating</span>
            </span>
          )}
          <SectionTabs label="Period" tabs={PERIODS} activeId={period} onChange={setPeriod} panelId={PANEL_ID} />
        </div>
      </div>

      <div
        id={PANEL_ID}
        role="tabpanel"
        aria-labelledby={sectionTabId(PANEL_ID, period)}
        aria-busy={isLoading}
        className="space-y-4"
      >
        {error && <LoadError message="Couldn't load analytics." onRetry={() => mutate()} />}
        {isLoading && <MetricListSkeleton />}
        {data && (data.conversations === 0 ? <EmptyPeriod period={data.period} /> : <MetricList rows={metricRows(data)} />)}
      </div>
    </div>
  );
}

export default function AnalyticsPage() {
  return (
    <Suspense fallback={<MetricListSkeleton />}>
      <AnalyticsPageContent />
    </Suspense>
  );
}
