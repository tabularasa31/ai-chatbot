import { ExternalLink, TriangleAlert } from "lucide-react";
import { cn } from "./utils";

const TONES = {
  danger: {
    box: "border-danger bg-danger-subtle",
    icon: "text-danger",
    role: "alert",
  },
  warning: {
    box: "border-warning bg-warning-subtle",
    icon: "text-warning",
    role: "status",
  },
} as const;

export type PageBannerAction = {
  label: string;
  href: string;
  external?: boolean;
};

export function PageBanner({
  tone,
  lead,
  text,
  action,
}: {
  tone: keyof typeof TONES;
  lead: string;
  text: string;
  action?: PageBannerAction;
}) {
  const styles = TONES[tone];
  return (
    <div
      role={styles.role}
      className={cn(
        "mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-control border px-4 py-2",
        styles.box,
      )}
    >
      <TriangleAlert size={16} aria-hidden="true" className={cn("shrink-0", styles.icon)} />
      <p className="min-w-0 flex-1 basis-64 text-ui text-fg">
        <strong className="font-semibold">{lead}</strong> {text}
      </p>
      {action && (
        <a
          href={action.href}
          {...(action.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
          className="inline-flex h-11 shrink-0 items-center gap-2 rounded-control border border-line-strong bg-surface px-4 text-ui font-medium text-fg transition-colors hover:bg-surface-alt active:bg-surface-alt focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          {action.label}
          {action.external && <ExternalLink size={16} aria-hidden="true" />}
        </a>
      )}
    </div>
  );
}
