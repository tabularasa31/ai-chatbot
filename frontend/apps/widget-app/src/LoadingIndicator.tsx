"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { t } from "./strings";

// If a stage stays the same for this long, swap the label so the visitor
// does not perceive a freeze (reasoning models can go silent before the
// first token).
const STALLED_AFTER_MS = 8000;

export function LoadingIndicator({ stage, locale }: { stage: string | null; locale: string | null }) {
  const [stalled, setStalled] = useState(false);

  useEffect(() => {
    setStalled(false);
    const id = setTimeout(() => setStalled(true), STALLED_AFTER_MS);
    return () => clearTimeout(id);
  }, [stage]);

  const label = stalled
    ? t(locale, "status_stalled")
    : stage === "writing"
      ? t(locale, "status_writing")
      : t(locale, "status_working");

  return (
    <div role="status" className="flex items-center gap-2 text-ui text-fg-muted">
      <Loader2 size={16} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
      {label}
    </div>
  );
}
