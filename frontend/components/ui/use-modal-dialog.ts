"use client";

import { useEffect, useRef } from "react";

type ModalDialogOptions = {
  onClose: () => void;
  /** While false, Escape and scrim clicks keep the dialog open. */
  canClose?: boolean;
  /** Called when Escape or a scrim click was ignored because `canClose` is false. */
  onCloseBlocked?: () => void;
  /** Where focus goes on close when the element that opened the dialog is gone. */
  fallbackFocus?: () => HTMLElement | null;
};

/**
 * Opens the native `<dialog>` as modal on mount and closes it on unmount.
 * Returns focus to the opener, or to `fallbackFocus` when the opener no longer exists.
 */
export function useModalDialog({ onClose, canClose = true, onCloseBlocked, fallbackFocus }: ModalDialogOptions) {
  const ref = useRef<HTMLDialogElement>(null);
  const latest = useRef({ onClose, canClose, onCloseBlocked, fallbackFocus });
  latest.current = { onClose, canClose, onCloseBlocked, fallbackFocus };

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const keepFocusOnScrimPress = (event: MouseEvent) => {
      if (event.target === dialog) event.preventDefault();
    };
    const blockCancel = (event: Event) => {
      if (latest.current.canClose) return;
      event.preventDefault();
      latest.current.onCloseBlocked?.();
    };
    const closeOnScrimClick = (event: MouseEvent) => {
      if (event.target !== dialog) return;
      if (latest.current.canClose) latest.current.onClose();
      else latest.current.onCloseBlocked?.();
    };
    // A "close" event queued by an earlier effect run's cleanup (StrictMode) arrives
    // after the dialog was re-opened; only a close that left it closed is a real one.
    const handleClose = () => {
      if (dialog.open) return;
      if (latest.current.canClose) {
        latest.current.onClose();
      } else {
        dialog.showModal();
        latest.current.onCloseBlocked?.();
      }
    };

    dialog.showModal();
    dialog.addEventListener("mousedown", keepFocusOnScrimPress);
    dialog.addEventListener("click", closeOnScrimClick);
    dialog.addEventListener("cancel", blockCancel);
    dialog.addEventListener("close", handleClose);
    return () => {
      dialog.removeEventListener("mousedown", keepFocusOnScrimPress);
      dialog.removeEventListener("click", closeOnScrimClick);
      dialog.removeEventListener("cancel", blockCancel);
      dialog.removeEventListener("close", handleClose);
      dialog.close();
      const target = opener?.isConnected ? opener : latest.current.fallbackFocus?.();
      target?.focus();
    };
  }, []);

  return ref;
}
