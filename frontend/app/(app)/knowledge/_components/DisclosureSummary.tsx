import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/components/ui/utils";

export function DisclosureSummary({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <summary className={cn("flex min-h-11 cursor-pointer items-center gap-2", className)}>
      {children}
      <ChevronDown
        size={16}
        aria-hidden="true"
        className="shrink-0 text-fg-muted transition-transform group-open:rotate-180"
      />
    </summary>
  );
}
