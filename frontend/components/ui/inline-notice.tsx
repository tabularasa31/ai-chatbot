import type { ReactNode } from "react";
import { Info, TriangleAlert, X } from "lucide-react";
import { Button } from "./button";

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
}: {
  children: ReactNode;
  tone?: keyof typeof TONES;
  action?: ReactNode;
  onDismiss?: () => void;
}) {
  const { Icon, surface, icon, role } = TONES[tone];
  return (
    <div role={role} className={`flex items-start gap-3 rounded-control py-2 pl-4 pr-2 ${surface}`}>
      <Icon size={16} aria-hidden="true" className={`mt-3 shrink-0 ${icon}`} />
      <div className="min-w-0 flex-1 break-words py-2.5 text-ui text-fg">{children}</div>
      {action}
      {onDismiss && (
        <Button variant="ghost" size="icon" offset="surface" onClick={onDismiss} aria-label="Dismiss">
          <X size={16} aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}
