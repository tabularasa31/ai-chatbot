"use client";

import { useId, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Button } from "./button";
import { useModalDialog } from "./use-modal-dialog";

/**
 * Right-hand modal panel. Mount it only while open; focus is trapped and returned by the native dialog.
 * Header and footer stay fixed; the caller's body must be the only scroll container.
 * Rendered into `document.body` so a parent's `space-y-*` margin cannot push the dialog off its edge.
 */
export function Drawer({
  title,
  meta,
  actions,
  onClose,
  canClose,
  onCloseBlocked,
  children,
}: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  onClose: () => void;
  canClose?: boolean;
  onCloseBlocked?: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  const ref = useModalDialog({ onClose, canClose, onCloseBlocked });

  return createPortal(
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className="m-0 ml-auto h-full max-h-full w-full max-w-xl overflow-hidden border-l border-line bg-surface p-0 text-fg shadow-overlay animate-drawer-in backdrop:animate-scrim-in backdrop:bg-scrim motion-reduce:animate-fade-in motion-reduce:backdrop:animate-fade-in dark:bg-surface-alt"
    >
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex items-start justify-between gap-4 border-b border-line px-6 py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="break-words text-body font-semibold text-fg">
              {title}
            </h2>
            {meta}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {actions}
            <Button variant="ghost" size="icon" offset="surface" onClick={onClose} aria-label="Close">
              <X size={20} aria-hidden="true" />
            </Button>
          </div>
        </header>
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </div>
    </dialog>,
    document.body,
  );
}
