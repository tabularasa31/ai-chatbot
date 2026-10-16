"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button } from "./button";
import { InlineNotice } from "./inline-notice";

type ConfirmDialogProps = {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  tone: "primary" | "destructive";
  failureMessage: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
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
}: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const busyRef = useRef(busy);
  busyRef.current = busy;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const closeOnScrimClick = (event: MouseEvent) => {
      if (event.target === dialog && !busyRef.current) onCloseRef.current();
    };
    dialog.showModal();
    dialog.addEventListener("click", closeOnScrimClick);
    return () => {
      dialog.removeEventListener("click", closeOnScrimClick);
      dialog.close();
    };
  }, []);

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

  function close() {
    if (!busy) onClose();
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      className="m-auto w-[calc(100%-2rem)] max-w-md rounded-surface border border-line bg-surface p-0 text-fg shadow-overlay backdrop:bg-scrim/60 dark:bg-surface-alt"
    >
      <div className="space-y-4 p-6">
        <h2 id={titleId} className="break-words text-h2 text-fg">
          {title}
        </h2>
        <p className="text-ui text-fg-muted">{body}</p>
        {failed && <InlineNotice tone="danger">{failureMessage}</InlineNotice>}
        <div className="flex justify-end gap-3">
          <Button variant="secondary" offset="surface" onClick={close} disabled={busy}>
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
