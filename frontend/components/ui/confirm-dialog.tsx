"use client";

import { useId, useState, type ReactNode } from "react";
import { Button } from "./button";
import { InlineNotice } from "./inline-notice";
import { useModalDialog } from "./use-modal-dialog";

type ConfirmDialogProps = {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  tone: "primary" | "destructive";
  failureMessage: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  fallbackFocus?: () => HTMLElement | null;
};

/**
 * Mount it only while a decision is pending. `onConfirm` must reject on failure; the dialog then
 * stays open with `failureMessage`, and calls `onClose` once `onConfirm` resolves.
 */
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  tone,
  failureMessage,
  onConfirm,
  onClose,
  fallbackFocus,
}: ConfirmDialogProps) {
  const titleId = useId();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const ref = useModalDialog({ onClose, canClose: !busy, fallbackFocus });

  async function confirm() {
    setBusy(true);
    setFailed(false);
    try {
      await onConfirm();
      onClose();
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className="m-auto w-11/12 max-w-md rounded-surface border border-line bg-surface p-0 text-fg shadow-overlay backdrop:bg-scrim dark:bg-surface-alt"
    >
      <div className="space-y-4 p-6">
        <h2 id={titleId} className="break-words text-h2 text-fg">
          {title}
        </h2>
        <p className="text-ui text-fg-muted">{body}</p>
        {failed && <InlineNotice tone="danger">{failureMessage}</InlineNotice>}
        <div className="flex justify-end gap-3">
          <Button variant="secondary" offset="surface" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant={tone} offset="surface" busy={busy} onClick={() => void confirm()}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
