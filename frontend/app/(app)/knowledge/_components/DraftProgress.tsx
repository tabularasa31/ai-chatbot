"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

const TICK_MS = 1000;

function statusLine(questionCount: number): string {
  if (questionCount === 0) return "Writing a draft from visitor questions";
  return `Writing a draft from ${questionCount} visitor ${questionCount === 1 ? "question" : "questions"}`;
}

/** The elapsed seconds are not announced; only the status line is. */
export function DraftProgress({ questionCount }: { questionCount: number }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setElapsed((seconds) => seconds + 1), TICK_MS);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="space-y-1">
      <p role="status" className="flex items-center gap-2 text-ui text-fg">
        <Loader2 size={16} aria-hidden="true" className="animate-spin" />
        {statusLine(questionCount)}
      </p>
      <p className="text-caption tabular-nums text-fg-muted">
        Takes up to 30 seconds. <span aria-hidden="true">{elapsed} s elapsed</span>
      </p>
    </div>
  );
}
