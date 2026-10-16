import type { ReactNode } from "react";
import { Info, X } from "lucide-react";
import { Button } from "./button";

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
        <Button variant="ghost" size="icon" offset="surface" onClick={onDismiss} aria-label="Dismiss">
          <X size={16} aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}
