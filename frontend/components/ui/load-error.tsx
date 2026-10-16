"use client";

import { useState } from "react";
import { Loader2, TriangleAlert } from "lucide-react";
import { Button } from "./button";

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
      <Button variant="secondary" offset="surface" onClick={retry} aria-busy={retrying}>
        {retrying && <Loader2 size={16} aria-hidden="true" className="animate-spin" />}
        Try again
      </Button>
    </div>
  );
}
