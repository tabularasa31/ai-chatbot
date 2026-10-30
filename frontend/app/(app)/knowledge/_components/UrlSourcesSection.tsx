"use client";

import { Trash2 } from "lucide-react";
import type { UrlSource, UrlSourceDetail } from "@/lib/api";
import { formatDateTime, pluralize } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { SourceForm } from "./SourceForm";
import { DETAIL_ID_PREFIX, NAME_BUTTON_ON_BAND_CLASS, quickAnswerLabel, sourceStatus, type SourceFormValues } from "./shared";

export type DetailView =
  | { state: "loading" }
  | { state: "error"; onRetry: () => Promise<unknown> }
  | { state: "ready"; detail: UrlSourceDetail; onRetryRefresh: (() => Promise<unknown>) | null };

type UrlSourcesSectionProps = {
  source: UrlSource;
  view: DetailView;
  isEditing: boolean;
  onSaveEdit: (values: SourceFormValues) => Promise<void>;
  onCancelEdit: () => void;
  onRequestDeletePage: (pageId: string) => void;
  onPreviewPage: (pageId: string) => void;
};

function Heading({ children }: { children: string }) {
  return <h3 className="mb-2 text-body font-semibold text-fg">{children}</h3>;
}

function DetailBody({
  source,
  detail,
  isEditing,
  onSaveEdit,
  onCancelEdit,
  onRequestDeletePage,
  onPreviewPage,
}: Omit<UrlSourcesSectionProps, "view"> & { detail: UrlSourceDetail }) {
  return (
    <div className="space-y-6">
      {isEditing && (
        <SourceForm
          initial={{
            name: source.name,
            schedule: detail.schedule,
            exclusions: detail.exclusion_patterns.join("\n"),
          }}
          withUrl={false}
          submitLabel="Save changes"
          failureFallback="Couldn't save these changes. Try again."
          onSubmit={onSaveEdit}
          onCancel={onCancelEdit}
        />
      )}

      {detail.error_message && <InlineNotice tone="danger">{detail.error_message}</InlineNotice>}
      {detail.warning_message && <InlineNotice tone="warning">{detail.warning_message}</InlineNotice>}

      <div className="grid gap-6 lg:grid-cols-2">
        <section>
          <Heading>{`Indexed pages (${detail.pages.length})`}</Heading>
          {detail.pages.length === 0 ? (
            <p className="text-ui text-fg-muted">Pages appear here once indexing starts.</p>
          ) : (
            <ul className="divide-y divide-line">
              {detail.pages.map((page) => (
                <li key={page.id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0 flex-1">
                    <button
                      type="button"
                      onClick={() => onPreviewPage(page.id)}
                      className={`block break-anywhere ${NAME_BUTTON_ON_BAND_CLASS}`}
                    >
                      {page.title}
                    </button>
                    <p className="break-anywhere text-caption text-fg-muted">{page.url}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <span className="whitespace-nowrap text-caption tabular-nums text-fg-muted">{`${page.chunk_count} ${pluralize(page.chunk_count, "passage", "passages")}`}</span>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete page ${page.title}`}
                      title="Delete page"
                      onClick={() => onRequestDeletePage(page.id)}
                    >
                      <Trash2 size={20} aria-hidden="true" />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="space-y-6">
          <section>
            <Heading>Recent crawls</Heading>
            {detail.recent_runs.length === 0 ? (
              <p className="text-ui text-fg-muted">No crawls yet.</p>
            ) : (
              <ul className="divide-y divide-line">
                {detail.recent_runs.map((run) => {
                  const { label } = sourceStatus(run.status);
                  return (
                    <li key={run.id} className="py-2 text-ui">
                      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                        <span className="font-medium text-fg">{label}</span>
                        <span className="whitespace-nowrap text-caption tabular-nums text-fg-muted">{formatDateTime(run.created_at)}</span>
                      </div>
                      <p className="text-caption tabular-nums text-fg-muted">
                        {run.pages_found ? `${run.pages_indexed} of ${run.pages_found} ${pluralize(run.pages_found, "page", "pages")}` : `${run.pages_indexed} ${pluralize(run.pages_indexed, "page", "pages")}`}
                      </p>
                      {run.failed_urls.length > 0 && (
                        <details className="text-caption text-fg-muted">
                          <summary className="flex min-h-11 cursor-pointer items-center">
                            {`${run.failed_urls.length} ${pluralize(run.failed_urls.length, "page", "pages")} could not be read`}
                          </summary>
                          <ul className="space-y-1 pb-2">
                            {run.failed_urls.map((failed) => (
                              <li key={failed.url} className="break-anywhere">{`${failed.url} (${failed.reason})`}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section>
            <Heading>Skipped paths</Heading>
            {detail.exclusion_patterns.length === 0 ? (
              <p className="text-ui text-fg-muted">Nothing skipped</p>
            ) : (
              <ul className="space-y-1 font-mono text-ui text-fg">
                {detail.exclusion_patterns.map((pattern) => (
                  <li key={pattern} className="break-anywhere">{pattern}</li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      <section>
        <Heading>Quick answers</Heading>
        {detail.quick_answers.length === 0 ? (
          <p className="text-ui text-fg-muted">No quick answers found yet.</p>
        ) : (
          <dl className="grid gap-x-6 sm:grid-cols-2">
            {detail.quick_answers.map((item) => (
              <div key={item.key} className="border-b border-line py-2">
                <dt className="text-caption font-medium text-fg-muted">{quickAnswerLabel(item.key)}</dt>
                <dd className="break-anywhere text-ui text-fg">{item.value}</dd>
                <dd className="break-anywhere text-caption text-fg-muted">{item.source_url}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>
    </div>
  );
}

export function UrlSourcesSection({ view, ...rest }: UrlSourcesSectionProps) {
  return (
    <tr id={`${DETAIL_ID_PREFIX}${rest.source.id}`} className="bg-surface-alt">
      <td colSpan={5} className="px-4 py-4">
        {view.state === "loading" && (
          <SkeletonRegion>
            <div className="space-y-3">
              <Skeleton className="h-6 w-1/3 bg-line" />
              <Skeleton className="h-6 w-2/3 bg-line" />
              <Skeleton className="h-6 w-1/2 bg-line" />
            </div>
          </SkeletonRegion>
        )}
        {view.state === "error" && <LoadError message="Couldn't load this source." onRetry={view.onRetry} />}
        {view.state === "ready" && (
          <div className="space-y-4">
            {view.onRetryRefresh && <LoadError message="Couldn't refresh this source." onRetry={view.onRetryRefresh} />}
            <DetailBody {...rest} detail={view.detail} />
          </div>
        )}
      </td>
    </tr>
  );
}
