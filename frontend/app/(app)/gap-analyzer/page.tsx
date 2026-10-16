"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, type GapDraftResponse, type GapItem } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useClientMe } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Select } from "@/components/ui/field";
import { FlashMessage, useFlash } from "@/components/ui/flash";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { SectionTabs, sectionTabId } from "@/components/ui/section-tabs";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { DraftPreviewDrawer } from "./_components/DraftPreviewDrawer";
import { GapList } from "./_components/GapList";
import type { RowAction, RowHandlers, RowPending } from "./_components/GapRow";
import { GAP_PANEL_ID, SHOW_OPTIONS, gapTabs, isInDraftFlow, itemsFor, recalculateWaitMinutes, type GapShow, type GapTab } from "./_components/shared";
import { useGapData } from "./_components/useGapData";

const KEY_NOTICE_ID = "gap-openai-key-notice";
const SKELETON_ROWS = 4;
const RECALCULATE_STARTED = "Recalculation started. New gaps appear here when it finishes.";

function ListSkeleton() {
  return (
    <SkeletonRegion>
      <div className="space-y-6 border-t border-line pt-4">
        {Array.from({ length: SKELETON_ROWS }, (_, index) => (
          <div key={index} className="space-y-2">
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="w-1/3" />
            <Skeleton className="w-2/3" />
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}

export default function GapAnalyzerPage() {
  const router = useRouter();
  const [tab, setTab] = useState<GapTab>("visitor-questions");
  const [show, setShow] = useState<GapShow>("active");
  const { data, failed, load } = useGapData(show);
  const { data: client, error: clientError } = useClientMe();
  const hasOpenAiKey = clientError ? false : client ? Boolean(client.has_openai_key) : true;

  const [pending, setPending] = useState<RowPending>(null);
  const [actionError, setActionError] = useState("");
  const [dismissed, setDismissed] = useState<GapItem | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [confirming, setConfirming] = useState<GapItem | null>(null);
  const [preview, setPreview] = useState<GapDraftResponse | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [waitMinutes, setWaitMinutes] = useState<number | null>(null);
  const [listFlash, flashList] = useFlash();
  const [recalcFlash, flashRecalc] = useFlash();

  function resetFeedback() {
    setActionError("");
    setDismissed(null);
    setWaitMinutes(null);
  }

  function changeTab(next: GapTab) {
    resetFeedback();
    setTab(next);
  }

  function changeShow(next: GapShow) {
    resetFeedback();
    setShow(next);
  }

  async function runRowAction(item: GapItem, action: RowAction, failure: string, work: () => Promise<void>) {
    setPending({ id: item.id, action });
    setActionError("");
    try {
      await work();
    } catch {
      setActionError(failure);
    } finally {
      setPending(null);
    }
  }

  async function dismissItem(item: GapItem) {
    await api.gapAnalyzer.dismiss(item.source, item.id);
    await load();
  }

  const handlers: RowHandlers = {
    onGenerate: (item) =>
      void runRowAction(item, "generate", "Couldn't draft an answer. Try again.", async () => {
        await api.gapAnalyzer.generateModeBDraft(item.id);
        router.push(`/gap-analyzer/mode_b/${item.id}/draft`);
      }),
    onPreview: (item) =>
      void runRowAction(item, "preview", "Couldn't prepare the draft preview. Try again.", async () => {
        setPreview(await api.gapAnalyzer.draft(item.source, item.id));
      }),
    onDismiss: (item) => {
      if (isInDraftFlow(item)) {
        setConfirming(item);
        return;
      }
      setDismissed(null);
      void runRowAction(item, "dismiss", "Couldn't dismiss this gap. Try again.", async () => {
        await dismissItem(item);
        setDismissed(item);
      });
    },
    onReactivate: (item) =>
      void runRowAction(item, "reactivate", "Couldn't reactivate this gap. Try again.", async () => {
        await api.gapAnalyzer.reactivate(item.source, item.id);
        await load();
        flashList("Gap reactivated");
      }),
  };

  async function undoDismiss(item: GapItem) {
    setUndoBusy(true);
    setActionError("");
    try {
      await api.gapAnalyzer.reactivate(item.source, item.id);
      setDismissed(null);
      await load();
    } catch {
      setActionError("Couldn't undo the dismissal. Try again.");
    } finally {
      setUndoBusy(false);
    }
  }

  async function recalculate() {
    setRecalculating(true);
    resetFeedback();
    try {
      const result = await api.gapAnalyzer.recalculate("both");
      if (result.status === "rate_limited") setWaitMinutes(recalculateWaitMinutes(result.retry_after_seconds));
      else flashRecalc(RECALCULATE_STARTED);
    } catch {
      setActionError("Couldn't start the recalculation. Try again.");
    } finally {
      setRecalculating(false);
    }
  }

  const summary = data?.summary;
  const items = data ? itemsFor(tab, data) : [];
  const showKeyNotice = tab === "visitor-questions" && !hasOpenAiKey;
  const showImpact = show === "active" && summary && summary.total_active > 0;

  return (
    <div className="max-w-6xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <h1 className="text-h1 text-fg">Doc gaps</h1>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {summary?.last_updated && (
            <span className="text-caption tabular-nums text-fg-muted">Updated {formatDateTime(summary.last_updated)}</span>
          )}
          <FlashMessage message={recalcFlash} />
          <Button variant="secondary" busy={recalculating} onClick={() => void recalculate()}>
            {recalculating ? "Starting" : "Recalculate"}
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-4">
        <SectionTabs
          label="Doc gaps"
          tabs={gapTabs(data)}
          activeId={tab}
          onChange={changeTab}
          panelId={GAP_PANEL_ID}
        />
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="text-ui text-fg-muted">
            Show
          </span>
          <Select
            label="Show"
            hideLabel
            value={show}
            onChange={(event) => changeShow(event.target.value as GapShow)}
            className="w-40"
          >
            {SHOW_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <div
        role="tabpanel"
        id={GAP_PANEL_ID}
        aria-labelledby={sectionTabId(GAP_PANEL_ID, tab)}
        aria-busy={!data && !failed}
        className="space-y-4"
      >
        {showKeyNotice && (
          <div id={KEY_NOTICE_ID}>
            <InlineNotice>
              Add your OpenAI key in{" "}
              <Link href="/settings" className="underline">
                Settings
              </Link>{" "}
              to draft answers.
            </InlineNotice>
          </div>
        )}
        {waitMinutes !== null && <InlineNotice>Recalculated recently. Try again in {waitMinutes} min.</InlineNotice>}
        {actionError && (
          <InlineNotice tone="danger" onDismiss={() => setActionError("")}>
            {actionError}
          </InlineNotice>
        )}
        {dismissed && (
          <InlineNotice
            onDismiss={() => setDismissed(null)}
            action={
              <Button variant="ghost" offset="surface" busy={undoBusy} onClick={() => void undoDismiss(dismissed)}>
                Undo
              </Button>
            }
          >
            Dismissed &ldquo;{dismissed.label}&rdquo;
          </InlineNotice>
        )}
        <FlashMessage message={listFlash} />
        {failed && <LoadError message="Couldn't load doc gaps." onRetry={load} />}
        {showImpact && <p className="text-ui text-fg-muted">{summary.impact_statement}</p>}
        {data ? (
          <GapList
            items={items}
            show={show}
            context={{ pending, hasOpenAiKey, keyNoticeId: KEY_NOTICE_ID, handlers }}
          />
        ) : (
          !failed && <ListSkeleton />
        )}
      </div>

      {preview && <DraftPreviewDrawer draft={preview} onClose={() => setPreview(null)} />}
      {confirming && (
        <ConfirmDialog
          title={`Dismiss "${confirming.label}"?`}
          body="Your draft is kept, but this gap leaves review and returns as open if restored."
          confirmLabel="Dismiss gap"
          tone="primary"
          failureMessage="Couldn't dismiss this gap. Try again."
          onConfirm={() => dismissItem(confirming)}
          onClose={() => setConfirming(null)}
        />
      )}
    </div>
  );
}
