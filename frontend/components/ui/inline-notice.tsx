import type { ReactNode } from "react";
import { Info, X } from "lucide-react";

export function InlineNotice({
  children,
  onDismiss,
}: {
  children: ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div role="status" className="flex items-start gap-3 rounded-control bg-info-subtle py-2 pl-4 pr-2">
      <Info size={16} aria-hidden="true" className="mt-3 shrink-0 text-info" />
      <div className="min-w-0 flex-1 py-2.5 text-ui text-fg">{children}</div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted transition-colors hover:bg-surface-alt active:bg-surface-alt focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          <X size={20} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
