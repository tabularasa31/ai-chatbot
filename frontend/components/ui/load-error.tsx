"use client";

import { useState } from "react";

export function LoadError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => Promise<unknown>;
}) {
  const [retrying, setRetrying] = useState(false);

  async function retry() {
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
      className="flex items-center justify-between gap-3 rounded-lg bg-red-50 text-red-600 text-sm px-3 py-2 border border-red-100"
    >
      <span>{message}</span>
      <button
        type="button"
        onClick={retry}
        disabled={retrying}
        className="shrink-0 px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
      >
        {retrying ? "Retrying…" : "Retry"}
      </button>
    </div>
  );
}
