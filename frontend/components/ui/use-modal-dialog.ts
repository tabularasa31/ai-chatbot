"use client";

import { useEffect, useRef } from "react";

type ModalDialogOptions = {
  onClose: () => void;
  /** While false, Escape and scrim clicks keep the dialog open. */
  canClose?: boolean;
  /** Where focus goes on close when the element that opened the dialog is gone. */
  fallbackFocus?: () => HTMLElement | null;
};

/**
 * Opens the native `<dialog>` as modal on mount and closes it on unmount.
 * Returns focus to the opener, or to `fallbackFocus` when the opener no longer exists.
 */
export function useModalDialog({ onClose, canClose = true, fallbackFocus }: ModalDialogOptions) {
  const ref = useRef<HTMLDialogElement>(null);
  const latest = useRef({ onClose, canClose, fallbackFocus });
  latest.current = { onClose, canClose, fallbackFocus };

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const requestClose = () => {
      if (latest.current.canClose) latest.current.onClose();
      else if (!dialog.open) dialog.showModal();
    };
    const closeOnScrimClick = (event: MouseEvent) => {
      if (event.target === dialog) requestClose();
    };

    dialog.showModal();
    dialog.addEventListener("click", closeOnScrimClick);
    dialog.addEventListener("close", requestClose);
    return () => {
      dialog.removeEventListener("click", closeOnScrimClick);
      dialog.removeEventListener("close", requestClose);
      dialog.close();
      const target = opener?.isConnected ? opener : latest.current.fallbackFocus?.();
      target?.focus();
    };
  }, []);

  return ref;
}
