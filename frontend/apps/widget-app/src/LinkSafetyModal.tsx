"use client";

import { useLayoutEffect, useRef } from "react";
import { ActionButton } from "./ActionButton";

type LinkSafetyLabels = {
  title: string;
  body: string;
  continue_label: string;
  cancel_label: string;
};

type LinkSafetyModalProps = {
  hostname: string;
  labels: LinkSafetyLabels;
  onConfirm: () => void;
  onCancel: () => void;
};

export function LinkSafetyModal({
  hostname,
  labels,
  onConfirm,
  onCancel,
}: LinkSafetyModalProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const [bodyBefore, bodyAfter] = labels.body.includes("{hostname}")
    ? labels.body.split("{hostname}", 2)
    : [`${labels.body} `, ""];

  useLayoutEffect(() => {
    const opener = document.activeElement;
    cancelRef.current?.focus();
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  function handleKeyDown(event: { key: string; shiftKey: boolean; preventDefault: () => void }) {
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    const first = cancelRef.current;
    const last = confirmRef.current;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-scrim px-4"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="link-safety-title"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={handleKeyDown}
        className="w-full max-w-md rounded-surface bg-surface p-6 text-fg shadow-overlay"
      >
        <h2 id="link-safety-title" className="text-h2">
          {labels.title}
        </h2>
        <p className="mt-2 text-ui [overflow-wrap:anywhere]">
          {bodyBefore}
          <span className="font-semibold">{hostname}</span>
          {bodyAfter}
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <ActionButton variant="secondary" onClick={onCancel} buttonRef={cancelRef}>
            {labels.cancel_label}
          </ActionButton>
          <ActionButton variant="primary" onClick={onConfirm} buttonRef={confirmRef}>
            {labels.continue_label}
          </ActionButton>
        </div>
      </div>
    </div>
  );
}
