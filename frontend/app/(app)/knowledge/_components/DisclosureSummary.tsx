import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { FOCUS_RING } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";

export function DisclosureSummary({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <summary className={cn("flex min-h-11 cursor-pointer items-center gap-2 rounded-control", FOCUS_RING.canvas, className)}>
      {children}
      <ChevronDown
        size={16}
        aria-hidden="true"
        className="shrink-0 text-fg-muted transition-transform group-open/disclosure:rotate-180"
      />
    </summary>
  );
}
