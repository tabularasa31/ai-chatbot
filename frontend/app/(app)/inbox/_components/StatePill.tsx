import type { HandoffState } from "@/lib/api";
import { cn } from "@/components/ui/utils";
import { PILL_STATES } from "../_lib/labels";

export function StatePill({ state }: { state: HandoffState }) {
  const pill = PILL_STATES[state];
  if (!pill) return null;
  return (
    <span className={cn("shrink-0 rounded-full px-2 text-caption font-medium", pill.className)}>{pill.label}</span>
  );
}
