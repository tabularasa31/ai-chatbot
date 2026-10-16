"use client";

import { useState } from "react";
import { Loader2, TriangleAlert } from "lucide-react";

/** `onRetry` must revalidate the failed request and resolve when it settles. */
export function LoadError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => Promise<unknown>;
}) {
  const [retrying, setRetrying] = useState(false);

  async function retry() {
    if (retrying) return;
    setRetrying(true);
    try {
      await onRetry();
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-control bg-danger-subtle px-4 py-2"
    >
      <TriangleAlert size={16} aria-hidden="true" className="shrink-0 text-danger" />
      <span className="min-w-0 flex-1 basis-48 text-ui text-fg">{message}</span>
      <button
        type="button"
        onClick={retry}
        aria-busy={retrying}
        className="inline-flex h-11 shrink-0 items-center gap-2 rounded-control border border-line-strong bg-surface px-4 text-ui font-medium text-fg transition-colors hover:bg-surface-alt active:bg-surface-alt focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
      >
        {retrying && <Loader2 size={16} aria-hidden="true" className="animate-spin" />}
        Try again
      </button>
    </div>
  );
}
