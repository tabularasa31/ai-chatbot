import type { ReactNode } from "react";
import { Info, TriangleAlert, X } from "lucide-react";
import { Button } from "./button";
import { cn } from "./utils";

const TONES = {
  info: { Icon: Info, surface: "bg-info-subtle", icon: "text-info", role: "status" },
  warning: { Icon: TriangleAlert, surface: "bg-warning-subtle", icon: "text-warning", role: "status" },
  danger: { Icon: TriangleAlert, surface: "bg-danger-subtle", icon: "text-danger", role: "alert" },
} as const;

export function InlineNotice({
  children,
  tone = "info",
  action,
  onDismiss,
  singleLine = false,
}: {
  children: ReactNode;
  tone?: keyof typeof TONES;
  action?: ReactNode;
  onDismiss?: () => void;
  singleLine?: boolean;
}) {
  const { Icon, surface, icon, role } = TONES[tone];
  return (
    <div
      role={role}
      className={cn(
        "flex gap-3 rounded-control pl-4 pr-2",
        singleLine ? "items-center py-2.5" : "items-start py-2",
        surface,
      )}
    >
      <Icon size={16} aria-hidden="true" className={cn("shrink-0", !singleLine && "mt-3", icon)} />
      <div
        title={singleLine && typeof children === "string" ? children : undefined}
        className={cn("min-w-0 flex-1 text-ui text-fg", singleLine ? "truncate" : "break-anywhere py-2.5")}
      >
        {children}
      </div>
      {action}
      {onDismiss && (
        <Button variant="ghost" size="icon" offset="surface" onClick={onDismiss} aria-label="Dismiss">
          <X size={16} aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}
