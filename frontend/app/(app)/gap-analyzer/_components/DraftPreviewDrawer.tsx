"use client";

import { useState } from "react";
import type { GapDraftResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { FlashMessage, useFlash } from "@/components/ui/flash";
import { InlineNotice } from "@/components/ui/inline-notice";

export function DraftPreviewDrawer({ draft, onClose }: { draft: GapDraftResponse; onClose: () => void }) {
  const [copied, flashCopied] = useFlash();
  const [copyFailed, setCopyFailed] = useState(false);

  async function copy() {
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(draft.markdown);
      flashCopied("Copied");
    } catch {
      setCopyFailed(true);
    }
  }

  return (
    <Drawer
      title={draft.title}
      meta={<p className="mt-1 text-caption text-fg-muted">Preview only. Nothing is saved.</p>}
      actions={
        <>
          <FlashMessage message={copied} />
          <Button variant="secondary" offset="surface" onClick={() => void copy()}>
            Copy
          </Button>
        </>
      }
      onClose={onClose}
    >
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-6">
        {copyFailed && <InlineNotice tone="danger">Couldn&apos;t copy the draft. Try again.</InlineNotice>}
        <pre className="whitespace-pre-wrap break-words rounded-control bg-surface-alt p-4 font-mono text-caption text-fg">
          {draft.markdown}
        </pre>
      </div>
    </Drawer>
  );
}
